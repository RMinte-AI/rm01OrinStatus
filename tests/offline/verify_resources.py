"""Host-only bounded resource/serialized-size checks and pinned C client replay.

Run from an installed candidate or with PYTHONPATH=src. No collection starts.
The only child processes allowed here are the C compiler and its host harness.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
import platform
from pathlib import Path
import socket
import statistics
import subprocess
import tempfile
import time
import tracemalloc

from tegrastats_api import TegrastatsParser
import tegrastats_api.parser as production
from socketio.packet import Packet, EVENT


ROOT = Path(__file__).resolve().parents[2]
REF = Path(__file__).parent / "client_reference"
LINE = json.loads((Path(__file__).parent / "fixtures.json").read_text())["repository_line"]


def denied(*args, **kwargs):
    raise AssertionError("Offline resource check forbids network access")


socket.socket.connect = denied
socket.socket.connect_ex = denied
socket.socket.bind = denied
socket.create_connection = denied
spec = importlib.util.spec_from_file_location("baseline_parser", ROOT / "evidence/ws-data/baseline/parser.py")
baseline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(baseline)


def packet(data):
    # Engine.IO message prefix + the production Socket.IO packet encoder.
    return '4' + Packet(EVENT, data=["tegrastats_update", data]).encode()


def wire(p, line):
    # Publication helper used only with a simulated running lifecycle.
    p._accept_line(line, p._stream_id, "2026-10-09T00:00:00.000000Z", time.monotonic())
    data, meta = p.get_snapshot()
    data['timestamp'] = "2026-10-09T00:00:00.000000Z"
    data['telemetry_meta'] = meta
    return data


def measure(function, n):
    values = []
    for _ in range(3):
        start = time.perf_counter()
        for i in range(n):
            function()
        values.append((time.perf_counter() - start) * 1e6 / n)
    return {"frames_per_trial": n, "trials": 3, "us_per_frame": values,
            "median_us": statistics.median(values)}


def main():
    args = argparse.ArgumentParser()
    args.add_argument('--output', required=True, type=Path)
    out = args.parse_args().output
    out.mkdir(parents=True, exist_ok=True)
    p = TegrastatsParser()
    p._running = True
    p._collector_state = 'running'
    before = baseline.TegrastatsParser.parse_line(LINE)
    before['timestamp'] = "2026-10-09T00:00:00.000000Z"
    after = wire(p, LINE)
    def old_cycle():
        data = baseline.TegrastatsParser.parse_line(LINE)
        data['timestamp'] = "2026-10-09T00:00:00.000000Z"
        return packet(data)
    perf = {"platform": platform.platform(), "python": platform.python_version(),
            "baseline": measure(old_cycle, 10000),
            "candidate": measure(lambda: packet(wire(p, LINE)), 10000)}
    perf['increment_median_us'] = perf['candidate']['median_us'] - perf['baseline']['median_us']
    tracemalloc.start()
    retained = []
    for n in (1000, 5000, 10000):
        for _ in range(n):
            packet(wire(p, LINE))
        current, peak = tracemalloc.get_traced_memory()
        retained.append({"total_frames": sum((1000, 5000, 10000)[:len(retained)+1]),
                         "retained_bytes": current, "peak_bytes": peak})
    tracemalloc.stop()
    perf['memory'] = retained
    perf['retained_growth_bytes'] = retained[-1]['retained_bytes'] - retained[0]['retained_bytes']
    assert perf['retained_growth_bytes'] < 200000

    sizes = {"normal_before_bytes": len(packet(before).encode()),
             "normal_after_bytes": len(packet(after).encode()), "representative": []}
    # Use legitimate synthetic rail names to stress current receiver capacity.
    for limit in (4096, 8192):
        last = None
        for count in range(1, 200):
            line = LINE + ''.join(' VDD_SYNTH_%03d 1234mW/2345mW' % i for i in range(count))
            body = wire(p, line)
            message = packet(body)
            if len(message.encode()) >= limit:
                sizes['representative'].append({"limit": limit,
                    "last_below_bytes": len(packet(last).encode()),
                    "first_at_or_above_bytes": len(message.encode()), "synthetic_rails": count})
                (out / ('near_%d.json' % limit)).write_text(json.dumps(last, indent=2))
                (out / ('over_%d.json' % limit)).write_text(json.dumps(body, indent=2))
                if limit == 8192:
                    below = packet(last).encode()
                    reconstructed = b''.join(below[pos:pos+2048] for pos in range(0, len(below), 2048))
                    assert reconstructed == below
                    assert json.loads(reconstructed[2:])[1]['temperature']['tj'] == 45.75
                    sizes['below_limit_fragment_replay_passed'] = True
                    # Replay the exact capacity rule from the client receive path.
                    assembled = bytearray()
                    overflow = False
                    for pos in range(0, len(message), 2048):
                        chunk = message[pos:pos+2048].encode()
                        copy_len = min(len(chunk), 8192 - len(assembled) - 1)
                        overflow |= copy_len < len(chunk)
                        assembled.extend(chunk[:copy_len])
                    assert overflow and len(assembled) == 8191
                    sizes['over_limit_truncation_observed_in_host_replay'] = True
                break
            last = body
        else:
            raise AssertionError('capacity fixture not reached')
    sizes['normal_increment_bytes'] = sizes['normal_after_bytes'] - sizes['normal_before_bytes']

    with tempfile.TemporaryDirectory(prefix='ws-client-') as directory:
        exe = Path(directory) / 'client-harness'
        subprocess.run(['cc', '-I', str(REF), str(REF/'client_harness.c'),
                        str(REF/'cJSON.c'), str(REF/'ts_jsonpath.c'), '-o', str(exe)], check=True)
        client_results = []
        stale = copy.deepcopy(after)
        stale['telemetry_meta']['fresh'] = False
        stale['telemetry_meta']['collector_state'] = 'stopped'
        cases = [('before', before), ('after', after), ('stale', stale)]
        for limit in (4096, 8192):
            cases.append(('near_%d' % limit, json.loads((out / ('near_%d.json' % limit)).read_text())))
        for label, body in cases:
            result = json.loads(subprocess.check_output([str(exe)], input=json.dumps(body).encode()))
            assert result['compute_tj'] == result['mapped_tj'] == 45.75
            # These historical mismatches existed before this change.
            assert result['cpu_core_count'] == result['ram_used'] == result['old_power_gpu_soc'] == 0
            client_results.append({"case": label, "result": result})
    (out/'client_compatibility.json').write_text(json.dumps(client_results, indent=2))
    (out/'resources.json').write_text(json.dumps(perf, indent=2))
    (out/'message_sizes.json').write_text(json.dumps(sizes, indent=2))
    (out/'candidate_packet.json').write_text(json.dumps(after, indent=2))
    (out/'loaded_source.json').write_text(json.dumps({"parser_file": production.__file__,
        "parser_sha256": hashlib.sha256(Path(production.__file__).read_bytes()).hexdigest()}, indent=2))
    print(json.dumps({"resource_checks": "passed", "client_harness": "passed", "sizes": sizes}, indent=2))


if __name__ == '__main__':
    main()
