import importlib.util
import io
import json
import os
from pathlib import Path
import queue
import threading
import time
from types import SimpleNamespace

import pytest

from tegrastats_api import Config, TegrastatsParser, TegrastatsServer
from tegrastats_api import parser as parser_module
from tegrastats_api import server as server_module


ROOT = Path(__file__).resolve().parents[2]
LINE = json.loads((Path(__file__).parent / "fixtures.json").read_text())["repository_line"]


def record(name, value):
    if os.environ.get("EVIDENCE_OUTPUT"):
        path = Path(os.environ["EVIDENCE_OUTPUT"])
        path.mkdir(parents=True, exist_ok=True)
        (path / (name + ".json")).write_text(json.dumps(value, indent=2))


class FakeProcess:
    def __init__(self):
        self.lines = queue.Queue()
        self.stdout = self
        self.code = None
        self.closed = False
    def readline(self):
        value = self.lines.get(timeout=3)
        if isinstance(value, Exception):
            raise value
        return value
    def poll(self):
        return self.code
    def terminate(self):
        self.code = 0
        self.lines.put("")
    def wait(self, timeout=None):
        return self.code
    def kill(self):
        self.terminate()
    def close(self):
        self.closed = True


@pytest.fixture
def collector(monkeypatch):
    processes = []
    commands = []
    def popen(cmd, **kwargs):
        commands.append(cmd)
        process = FakeProcess()
        processes.append(process)
        return process
    monkeypatch.setattr(parser_module.subprocess, "Popen", popen)
    p = TegrastatsParser()
    yield p, processes, commands
    p.stop()


def await_seq(p, n):
    deadline = time.monotonic() + 2
    while p.get_snapshot()[1]["sample_seq"] != n:
        assert time.monotonic() < deadline, "reader failed to publish"
        time.sleep(.001)


def accept(p, line=LINE, anchor=None):
    return p._accept_line(line, p._stream_id, "2026-10-09T00:00:00Z",
                          time.monotonic() if anchor is None else anchor)


def tick(server, monkeypatch, emit=None):
    waits = []
    def sleep(interval):
        waits.append(interval)
        server._running = False
    monkeypatch.setattr(server_module, "time", SimpleNamespace(sleep=sleep))
    if emit:
        monkeypatch.setattr(server.socketio, "emit", emit)
    server._running = True
    server._update_data_thread()
    assert waits == [server.config.update_interval]


def baseline_module(name):
    spec = importlib.util.spec_from_file_location(
        "tegrastats_api.baseline_" + name, ROOT / "evidence/ws-data/baseline" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_baseline_counterexamples(monkeypatch):
    old = baseline_module("parser").TegrastatsParser
    before = old.parse_line(LINE)
    assert set(before["power"]) == {"ram", "swap"}
    server = baseline_module("server").TegrastatsServer(Config())
    server.parser = old()
    server.parser._current_data = before
    client = server.app.test_client()
    first = client.get("/api/status").json
    second = client.get("/api/status").json
    assert first["timestamp"] != second["timestamp"]
    assert first["temperature"] == second["temperature"]
    assert "telemetry_meta" not in second
    messages = []
    server.limiter.add_connection()
    # Its method globals belong to the real saved baseline server module.
    globals_ = server._update_data_thread.__func__.__globals__
    def sleep(_):
        server._running = False
    monkeypatch.setitem(globals_, "time", SimpleNamespace(sleep=sleep))
    monkeypatch.setattr(server.socketio, "emit", lambda event, data: messages.append((event, data)))
    for _ in range(2):
        server._running = True
        server._update_data_thread()
    ws = [data for event, data in messages if event == "tegrastats_update"]
    assert ws[0]["timestamp"] != ws[1]["timestamp"]
    assert ws[0]["temperature"] == ws[1]["temperature"]
    record("baseline_counterexamples", {"parsed": before, "http": [first, second], "socketio": ws})


def test_power_and_normal_contract():
    before = baseline_module("parser").TegrastatsParser.parse_line(LINE)
    after = TegrastatsParser.parse_line(LINE)
    for key in ("cpu", "memory", "temperature", "gpu"):
        assert before[key] == after[key]
    assert after["temperature"]["tj"] == 45.75
    assert after["power"] == {
        name: {"current": n, "average": n, "unit": "mW"}
        for name, n in (("vdd_gpu_soc", 2468), ("vdd_cpu_cv", 246), ("vin_sys_5v0", 3383))}
    record("power_before_after", {"before": before, "after": after})


@pytest.mark.parametrize("token", ["-1/2", "1.5/2", "1/2.5", "1mA/2mA", "1W/2W", "1MB/2MB", "1mW/2", "1/2mW", "1/2mWx", "1//2", "NC", "1/", "/2", "1e3/2", "+1/2"])
def test_invalid_power_partial_frame(token):
    data = TegrastatsParser.parse_line("tj@45.75C VDD_BAD " + token + " VDD_GOOD 0mW/2mW")
    assert data["temperature"]["tj"] == 45.75
    assert data["power"] == {"vdd_good": {"current": 0, "average": 2, "unit": "mW"}}


def test_power_names_duplicates():
    data = TegrastatsParser.parse_line("tj@40C RAM 1/2MB SWAP 0/3MB (cached 0MB) lfb 2/3 FAKE 2/3 NC 2/3 XVDD_GPU 2/3 VDD_GPU_SOC 0/9 VIN_SYS_5V0 3/4 VDDRQ 1/2")
    assert set(data["power"]) == {"vdd_gpu_soc", "vin_sys_5v0", "vddrq"}
    assert data["power"]["vdd_gpu_soc"] == {"current": 0, "average": 9, "unit": "mW"}
    for suffix in ("VDD_A 2/3", "VDD_A bad", "VDD_A"):
        assert "vdd_a" not in TegrastatsParser.parse_line("tj@40C VDD_A 1/3 " + suffix)["power"]
    assert TegrastatsParser.parse_line("VDD_A 1/3 VDD_A 1/3")["power"]["vdd_a"]["current"] == 1


@pytest.mark.parametrize("interval", [0, -1, True, 1.5, "1000", None])
def test_invalid_interval(interval):
    with pytest.raises(ValueError):
        TegrastatsParser(interval)


def test_empty_failure_and_health(monkeypatch):
    server = TegrastatsServer()
    client = server.app.test_client()
    for endpoint in ("status", "cpu", "memory", "temperature", "power"):
        response = client.get("/api/" + endpoint)
        assert response.status_code == 503
        m = response.json["telemetry_meta"]
        assert m["sample_seq"] == 0 and m["received_at"] is None
        assert m["sample_age_ms"] is None and m["fresh"] is False
    def fail(*a, **k):
        raise FileNotFoundError("fake missing tegrastats")
    monkeypatch.setattr(parser_module.subprocess, "Popen", fail)
    with pytest.raises(FileNotFoundError):
        server.parser.start()
    assert client.get("/api/health").json["telemetry_meta"]["collector_state"] == "error"
    assert client.get("/api/health").json["status"] == "healthy"


def test_reader_identical_noise_and_missing(collector):
    p, processes, commands = collector
    p.start()
    assert commands == [["tegrastats", "--interval", "1000"]]
    processes[0].lines.put(LINE + "\n")
    await_seq(p, 1)
    first = p.get_snapshot()
    processes[0].lines.put(LINE + "\n")
    await_seq(p, 2)
    second = p.get_snapshot()[1]
    for noise in ("", "ordinary log", "error tj@45C", "tj@1.2.3C", "CPU [garbage]", "VDD_BAD 1W/2W"):
        assert not accept(p, noise)
    assert p.get_snapshot()[1]["sample_seq"] == 2
    assert p.get_snapshot()[1]["received_at"] == second["received_at"]
    assert accept(p, "tj@46C")
    data, meta = p.get_snapshot()
    assert meta["sample_seq"] == 3
    assert data["power"] == {} and data["gpu"] == {}
    assert data["temperature"] == {"tj": 46.0}
    assert accept(p, "cpu@47C")
    assert "tj" not in p.get_current_status()["temperature"]
    record("reader_samples", {"first": first, "missing_fields": [data, meta]})


def test_age_boundaries_utc_jumps(collector, monkeypatch):
    p, _, _ = collector
    p.start()
    clock = SimpleNamespace(now=100., utc=1000.)
    monkeypatch.setattr(parser_module, "time", SimpleNamespace(monotonic=lambda: clock.now, time=lambda: clock.utc))
    assert accept(p, anchor=100.)
    received = p.get_snapshot()[1]["received_at"]
    for now, utc, age, fresh in ((104.999, -999999, 4999, True), (105., 999999, 5000, False), (106., 0, 6000, False), (99., 1000, None, False)):
        clock.now, clock.utc = now, utc
        meta = p.get_snapshot()[1]
        assert meta["received_at"] == received and meta["fresh"] == fresh
        if age is None:
            assert meta["sample_age_ms"] is None
        else:
            assert meta["sample_age_ms"] == pytest.approx(age)
    assert TegrastatsParser(3000).get_snapshot()[1]["freshness_timeout_ms"] == 9000


def test_reader_utc_clock_jumps(collector, monkeypatch):
    from datetime import datetime, timezone
    p, processes, _ = collector
    clock = SimpleNamespace(monotonic=100., utc=1000000.)
    class WallClock:
        @staticmethod
        def now(tz):
            return datetime.fromtimestamp(clock.utc, timezone.utc)
    monkeypatch.setattr(parser_module, "datetime", WallClock)
    monkeypatch.setattr(parser_module, "time", SimpleNamespace(monotonic=lambda: clock.monotonic, time=lambda: clock.utc))
    p.start()
    processes[0].lines.put(LINE)
    await_seq(p, 1)
    first = p.get_snapshot()[1]
    clock.monotonic += 2
    clock.utc -= 500000
    assert p.get_snapshot()[1]["sample_age_ms"] == 2000
    assert p.get_snapshot()[1]["received_at"] == first["received_at"]
    processes[0].lines.put(LINE)
    await_seq(p, 2)
    second = p.get_snapshot()[1]
    assert second["received_at"] < first["received_at"] and second["sample_age_ms"] == 0
    clock.utc += 10000000
    clock.monotonic += 5
    assert p.get_snapshot()[1]["sample_age_ms"] == 5000
    assert not p.get_snapshot()[1]["fresh"]


@pytest.mark.parametrize("ending,state", [("", "stopped"), (OSError("fake reader error"), "error")])
def test_eof_error_cleanup(collector, ending, state):
    p, processes, _ = collector
    p.start()
    processes[0].lines.put(LINE)
    await_seq(p, 1)
    processes[0].lines.put(ending)
    p._thread.join(2)
    assert not p._thread.is_alive()
    data, meta = p.get_snapshot()
    assert data["temperature"]["tj"] == 45.75
    assert meta["collector_state"] == state and not meta["fresh"]
    assert processes[0].closed and processes[0].code == 0


def test_restart_late_publish(collector):
    p, _, _ = collector
    p.start()
    assert accept(p)
    old = p.get_snapshot()[1]
    p.stop()
    assert not p.get_snapshot()[1]["fresh"]
    p.start()
    assert p.get_current_status() == {}
    assert p.get_snapshot()[1]["sample_seq"] == 0
    assert p.get_snapshot()[1]["stream_id"] != old["stream_id"]
    assert not p._accept_line("tj@99C", old["stream_id"], old["received_at"], time.monotonic())
    assert accept(p, "tj@50C")
    assert p.get_snapshot()[1]["sample_seq"] == 1
    record("restart", {"old": old, "new": p.get_snapshot()})


def test_concurrent_snapshot_and_mutation(collector):
    p, _, _ = collector
    p.start()
    done = threading.Event()
    failures = []
    reads = []
    def writer():
        try:
            for i in range(1, 2001):
                assert accept(p, "tj@%dC VDD_A %d/%d" % (i, i, i))
                if i % 100 == 0:
                    time.sleep(.001)
        except Exception as error:
            failures.append(str(error))
        finally:
            done.set()
    def reader():
        while not done.is_set():
            data, meta = p.get_snapshot()
            if data:
                reads.append(meta["sample_seq"])
                if data["temperature"]["tj"] != meta["sample_seq"] or data["power"]["vdd_a"]["current"] != meta["sample_seq"]:
                    failures.append((data, meta))
                data["temperature"]["tj"] = -99
                data["power"].clear()
                meta["stream_id"] = "changed"
            time.sleep(.0001)
    readers = [threading.Thread(target=reader) for _ in range(3)]
    for thread in readers:
        thread.start()
    writer_thread = threading.Thread(target=writer)
    writer_thread.start()
    writer_thread.join(5)
    assert done.is_set()
    for thread in readers:
        thread.join(2)
    assert not failures
    assert len(reads) > 100
    assert p.get_current_status()["temperature"]["tj"] == 2000
    assert p.get_snapshot()[1]["sample_seq"] == 2000
    record("concurrency", {"input_frames": 2000, "concurrent_reads": len(reads), "mismatched_snapshots": len(failures), "readers": 3})


def test_http_baseline_normal_fields(collector):
    p, _, _ = collector
    p.start()
    assert accept(p)
    new = TegrastatsServer()
    new.parser = p
    old = baseline_module("server").TegrastatsServer()
    old.parser._current_data = baseline_module("parser").TegrastatsParser.parse_line(LINE)
    for endpoint in ("status", "cpu", "memory", "temperature", "power"):
        before = old.app.test_client().get("/api/" + endpoint).json
        after = new.app.test_client().get("/api/" + endpoint).json
        assert isinstance(before.pop("timestamp"), str)
        assert isinstance(after.pop("timestamp"), str)
        assert isinstance(after.pop("telemetry_meta"), dict)
        if "power" in before:
            before.pop("power")
            after.pop("power")
        assert before == after


def test_old_reader_finishes_after_restart(collector, monkeypatch):
    p, processes, _ = collector
    original_parse = p.parse_line
    entered, release = threading.Event(), threading.Event()
    def parse(line):
        if line == "tj@99C":
            entered.set()
            assert release.wait(3)
        return original_parse(line)
    monkeypatch.setattr(p, "parse_line", parse)
    p.start()
    old_thread = p._thread
    processes[0].lines.put("tj@99C")
    assert entered.wait(2)
    # Simulate the join deadline while an old parsing operation is delayed.
    original_join = old_thread.join
    monkeypatch.setattr(old_thread, "join", lambda timeout: None)
    p.stop()
    p.start()
    processes[1].lines.put("tj@50C")
    await_seq(p, 1)
    release.set()
    original_join(2)
    assert not old_thread.is_alive()
    data, meta = p.get_snapshot()
    assert data["temperature"]["tj"] == 50 and meta["sample_seq"] == 1
    assert meta["collector_state"] == "running" and meta["fresh"]


def test_process_kill_after_wait_timeout(monkeypatch):
    process = FakeProcess()
    calls = []
    def wait(timeout=None):
        calls.append(timeout)
        if timeout:
            raise parser_module.subprocess.TimeoutExpired("fake", timeout)
        return 0
    monkeypatch.setattr(process, "wait", wait)
    p = TegrastatsParser()
    p._cleanup_process(process)
    assert calls == [5, None] and process.code == 0


def test_real_http_socketio_repeats_models_and_slow_send(collector, monkeypatch):
    p, _, _ = collector
    p.start()
    assert accept(p)
    server = TegrastatsServer()
    server.parser = p
    http = server.app.test_client()
    ws = [server.socketio.test_client(server.app) for _ in range(2)]
    model = server.model_monitor.get_all_status()
    model_body = http.get("/api/model_status").json
    assert isinstance(model_body.pop("timestamp"), str)
    model.pop("timestamp")
    assert model_body == model
    assert http.get("/api/model_logs/llm").status_code == 200
    assert http.get("/api/model_logs/invalid").status_code == 400
    bodies = []
    first = p.get_snapshot()[1]
    for _ in range(2):
        for endpoint in ("status", "cpu", "memory", "temperature", "power"):
            body = http.get("/api/" + endpoint).json
            assert isinstance(body["timestamp"], str) and body["timestamp"].endswith("Z")
            for key in ("stream_id", "sample_seq", "received_at"):
                assert body["telemetry_meta"][key] == first[key]
            bodies.append(body)
        tick(server, monkeypatch)
    events = [client.get_received() for client in ws]
    for received in events:
        updates = [item["args"][0] for item in received if item["name"] == "tegrastats_update"]
        assert len(updates) == 2
        assert updates[0]["timestamp"] != updates[1]["timestamp"]
        for update in updates:
            assert update["temperature"]["tj"] == 45.75
            assert update["power"] == bodies[4]["power"]
            assert update["telemetry_meta"]["sample_seq"] == 1
        model_updates = [item["args"][0].copy() for item in received if item["name"] == "model_status_update"]
        for update in model_updates:
            assert isinstance(update.pop("timestamp"), str)
            assert update == model
        assert len(model_updates) == 2
    ws[0].disconnect()
    ws[0] = server.socketio.test_client(server.app)
    assert p.get_snapshot()[1]["stream_id"] == first["stream_id"]
    # Hold a real emit call while the independent input reader publishes another line.
    entered, release = threading.Event(), threading.Event()
    real_emit = server.socketio.emit
    def slow_emit(event, data):
        if event == "tegrastats_update":
            entered.set()
            assert release.wait(2)
        real_emit(event, data)
    thread = threading.Thread(target=lambda: tick(server, monkeypatch, slow_emit))
    thread.start()
    assert entered.wait(2)
    p._process.lines.put("tj@51C")
    await_seq(p, 2)
    release.set()
    thread.join(2)
    assert not thread.is_alive()
    p.stop()
    tick(server, monkeypatch)
    stale = [item["args"][0] for item in ws[0].get_received() if item["name"] == "tegrastats_update"][-1]
    assert stale["temperature"]["tj"] == 51 and not stale["telemetry_meta"]["fresh"]
    assert stale["telemetry_meta"]["collector_state"] == "stopped"
    record("http_socketio", {"http": bodies, "socketio": events, "stale": stale})
    for client in ws:
        client.disconnect()
