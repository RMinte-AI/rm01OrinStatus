"""Official old/new formats, mapped through the existing data and API contract."""
import pytest

from tegrastats_api import TegrastatsParser, TegrastatsServer
from test_data import await_seq, collector, tick


TEMPERATURE = {"cpu": 56.968, "gpu": 48.406, "tj": 56.968}
POWER = {
    "vdd_gpu_soc": {"current": 5147, "average": 4900, "unit": "mW"},
    "vdd_cpu_cv": {"current": 1225, "average": 1100, "unit": "mW"},
    "vin_sys_5v0": {"current": 3571, "average": 3200, "unit": "mW"},
}
NEW_LINE = (
    "RAM 1/2MB cpu@56.968C/61.25C gpu@48.406C/52C tj@56.968C/61.25C "
    "VDD_GPU_SOC 5147mW/4900mW/6000mW VDD_CPU_CV 1225mW/1100mW/1500mW "
    "VIN_SYS_5V0 3571mW/3200mW/4000mW"
)


@pytest.mark.parametrize("line", ["tj@56.968C gpu@-2.5C cpu@0C",
                                  "tj@56.968C/61.25C gpu@-2.5C/5C cpu@0C/10C"])
def test_temperature_keeps_current_value(line):
    assert TegrastatsParser.parse_line(line)["temperature"] == {"tj": 56.968, "gpu": -2.5, "cpu": 0.0}


@pytest.mark.parametrize("token", ["5147mW/4900mW/6000mW", "5147/4900/6000"])
def test_power_keeps_current_and_average(token):
    data = TegrastatsParser.parse_line("VDD_GPU_SOC " + token)
    assert data["power"] == {"vdd_gpu_soc": POWER["vdd_gpu_soc"]}
    conflicting = TegrastatsParser.parse_line("tj@40C VDD_GPU_SOC " + token + " VDD_GPU_SOC 99/100/101")
    assert conflicting["power"] == {}


def test_temperature_rejects_incomplete_and_extra_values():
    for token in ("tj@45C/50Cjunk", "tj@45C/badC", "tj@45C/50", "tj@45C/50C/55C"):
        assert TegrastatsParser.parse_line("RAM 1/2MB " + token)["temperature"] == {}


def test_power_rejects_bad_third_value_and_mixed_units():
    for token in ("1mW/2mW/bad", "1mW/2mW/3mWx", "1mW/2mW/3", "1/2/3mW", "1/2/3/4"):
        data = TegrastatsParser.parse_line("tj@45C/50C VDD_BAD " + token + " VIN_SYS_5V0 0mW/2mW/3mW")
        assert data["temperature"] == {"tj": 45.0}
        assert data["power"] == {"vin_sys_5v0": {"current": 0, "average": 2, "unit": "mW"}}


def test_new_formats_reader_http_socketio_and_identity(collector, monkeypatch):
    parser, processes, _ = collector
    server = TegrastatsServer()
    server.parser = parser
    http = server.app.test_client()
    client = server.socketio.test_client(server.app)
    try:
        parser.start()
        processes[0].lines.put(NEW_LINE)
        await_seq(parser, 1)
        first = http.get("/api/status").json
        assert first["temperature"] == TEMPERATURE and first["power"] == POWER
        assert http.get("/api/temperature").json["temperature"] == TEMPERATURE
        assert http.get("/api/power").json["power"] == POWER
        for _ in range(2):
            tick(server, monkeypatch)
            packet = [event["args"][0] for event in client.get_received()
                      if event["name"] == "tegrastats_update"][-1]
            assert packet["temperature"] == TEMPERATURE and packet["power"] == POWER
            assert packet["telemetry_meta"]["sample_seq"] == 1
            assert packet["telemetry_meta"]["received_at"] == first["telemetry_meta"]["received_at"]
        processes[0].lines.put(NEW_LINE)
        await_seq(parser, 2)
        assert http.get("/api/status").json["telemetry_meta"]["sample_seq"] == 2
        processes[0].lines.put("tj@57C/61.25C")
        await_seq(parser, 3)
        missing = http.get("/api/status").json
        assert missing["temperature"] == {"tj": 57.0} and missing["power"] == {}
        assert http.get("/api/power").json["power"] == {}
        tick(server, monkeypatch)
        packet = [event["args"][0] for event in client.get_received()
                  if event["name"] == "tegrastats_update"][-1]
        assert packet["power"] == {} and packet["telemetry_meta"]["sample_seq"] == 3
    finally:
        client.disconnect()
