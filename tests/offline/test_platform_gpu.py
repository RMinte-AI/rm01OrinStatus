"""Synthetic providers only: source contract, lifecycle and causal sealing."""
import sys
import threading
import time
from types import SimpleNamespace
import pytest
from tegrastats_api.parser import TegrastatsParser
from tegrastats_api.platform_gpu import identify_platform, GPUSampler, NVMLReader, UnsupportedGPU
from test_data import FakeProcess, await_seq, record


def platform(family):
    soc = {'orin': 'tegra234', 'thor': 'tegra264'}[family]
    return {'schema_version': 1, 'family': 'jetson_'+family, 'soc': soc,
            'identification_source': 'device_tree_compatible', 'compatible': ['nvidia,'+soc],
            'gpu_backend': 'nvml' if family == 'thor' else 'tegrastats'}


@pytest.mark.parametrize('tokens,family', [(b'nvidia,tegra234\0other\0', 'jetson_orin'),
    (b'nvidia,tegra264\0', 'jetson_thor'), (b'nvidia,tegra234\0nvidia,tegra264\0', 'unknown'),
    (b'nvidia,tegra264x\0', 'unknown'), (b'Orin Thor\0', 'unknown')])
def test_exact_device_tree(tmp_path, tokens, family):
    p = tmp_path/'compatible'; p.write_bytes(tokens)
    d = identify_platform(p); assert d['family'] == family
    assert d['compatible'] == [s.decode() for s in tokens.split(b'\0') if s]
    assert identify_platform(tmp_path/'missing')['family'] == 'unknown'


def test_orin_seals_zero_and_rebroadcast(monkeypatch):
    fake = FakeProcess(); monkeypatch.setattr('tegrastats_api.parser.subprocess.Popen', lambda *a, **k: fake)
    p = TegrastatsParser(platform_reader=lambda: platform('orin'),
                        gpu_factory=lambda *a: pytest.fail('Orin must not create NVML'))
    try:
        p.start(); fake.lines.put('tj@46C GR3D_FREQ 0%'); await_seq(p, 1)
        a, ma = p.get_snapshot(); time.sleep(.01); b, mb = p.get_snapshot()
        assert a == b and ma['sample_seq'] == mb['sample_seq'] == 1
        assert mb['sample_age_ms'] >= ma['sample_age_ms']
        assert a['gpu']['utilization_pct'] == a['gpu']['gr3d_freq'] == 0
        assert a['gpu']['source'] == 'tegrastats' and a['gpu']['device_uuid'] is None
        assert a['platform'] == platform('orin')
        record('orin-contract', dict(a, telemetry_meta=ma))
        fake.lines.put('tj@47C'); await_seq(p, 2)
        assert p.get_snapshot()[0]['gpu']['status'] == 'unavailable'
    finally:
        p.stop()


class FakeReader:
    device_uuid = 'GPU-synthetic'
    def __init__(self, values=(0, 25)):
        self.values = iter(values); self.closed = False; self.calls = []
    def open(self): self.calls.append(('open', threading.get_ident()))
    def read(self):
        self.calls.append(('read', threading.get_ident()))
        x = next(self.values, 25)
        if isinstance(x, Exception): raise x
        return x
    def close(self): self.closed = True; self.calls.append(('close', threading.get_ident()))


def test_causal_completed_read_and_age():
    s = GPUSampler(1000, FakeReader())
    s._save(12, 'valid', 1, '2026-01-01T00:00:01Z'); s._save(99, 'valid', 3, '2026-01-01T00:00:03Z')
    a = s.snapshot_at(2)
    assert a['utilization_pct'] == 12 and a['age_at_telemetry_receive_ms'] == 1000
    assert s.snapshot_at(.5)['status'] == 'unavailable'
    assert s.snapshot_at(8)['status'] == 'stale' and s.snapshot_at(8)['utilization_pct'] is None
    assert s.snapshot_at(3)['utilization_pct'] == 99


def test_sampler_failure_recovery_and_release():
    r = FakeReader([RuntimeError('synthetic'), 0, 17]); s = GPUSampler(10, r); s.start()
    deadline = time.monotonic()+2
    while s.diagnostics()['reads'] < 3 and time.monotonic() < deadline: time.sleep(.002)
    a = s.snapshot_at(time.monotonic())
    assert a['status'] == 'valid' and a['device_uuid'] == 'GPU-synthetic'
    s.stop(); assert r.closed and not s.diagnostics()['running'] and s.diagnostics()['errors'] == 1
    ids = {x[1] for x in r.calls}; assert len(ids) == 1 and threading.get_ident() not in ids


@pytest.mark.parametrize('error,status', [(ImportError('no binding'), 'unavailable'),
    (UnsupportedGPU('MIG'), 'unavailable'), (RuntimeError('init'), 'error')])
def test_init_failure_releases(error, status):
    class Reader(FakeReader):
        def open(self): raise error
    r = Reader(); s = GPUSampler(1000, r); s.start(); s.thread.join(2)
    assert r.closed and s.snapshot_at(time.monotonic())['status'] == status
    s.stop()


def test_blocked_read_not_shutdown_concurrently():
    entered = threading.Event(); release = threading.Event()
    class Reader(FakeReader):
        def read(self): entered.set(); release.wait(5); return 0
    r = Reader(); s = GPUSampler(1000, r); s.start(); assert entered.wait(1)
    try:
        with pytest.raises(RuntimeError, match='did not exit'): s.stop()
        assert not r.closed and s.diagnostics()['running']
    finally:
        release.set(); s.stop()
    assert r.closed


def test_thor_parser_lifecycle_atomic(monkeypatch):
    fake = FakeProcess(); monkeypatch.setattr('tegrastats_api.parser.subprocess.Popen', lambda *a, **k: fake)
    samplers = []
    def factory(interval):
        s = GPUSampler(interval, FakeReader()); samplers.append(s); return s
    p = TegrastatsParser(platform_reader=lambda: platform('thor'), gpu_factory=factory)
    assert not samplers
    try:
        p.start(); s = samplers[0]; deadline = time.monotonic()+1
        while not s.history and time.monotonic() < deadline: time.sleep(.002)
        fake.lines.put('RAM 1/2MB tj@46C VDD_GPU 1000mW/500mW'); await_seq(p, 1)
        a, ma = p.get_snapshot()
        assert a['gpu']['status'] == 'valid' and a['gpu']['utilization_pct'] == 0
        assert 'gr3d_freq' not in a['gpu']
        s._save(77, 'valid', time.monotonic(), '2026-01-01T00:00:00Z')
        assert p.get_snapshot()[0] == a and p.get_snapshot()[1]['sample_seq'] == ma['sample_seq']
        assert p.capability_diagnostics()['platform'] == platform('thor')
    finally:
        p.stop()
    assert not s.diagnostics()['running'] and s.reader.closed


@pytest.mark.parametrize('count,mig', [(2, 0), (1, 1)])
def test_nvml_selection_no_configuration(monkeypatch, count, mig):
    calls = []
    class NotSupported(Exception): pass
    api = SimpleNamespace(nvmlInit=lambda: calls.append('init'), nvmlShutdown=lambda: calls.append('close'),
        nvmlDeviceGetCount=lambda: count, nvmlDeviceGetHandleByIndex=lambda i: ('handle', i),
        nvmlDeviceGetMigMode=lambda h: (mig, mig), NVMLError_NotSupported=NotSupported)
    monkeypatch.setitem(sys.modules, 'pynvml', api); r = NVMLReader()
    with pytest.raises(UnsupportedGPU): r.open()
    r.close(); assert calls == ['init', 'close']


def test_thor_http_and_socketio_same_sealed_measurement(monkeypatch):
    from tegrastats_api.server import TegrastatsServer
    from test_data import tick
    fake = FakeProcess()
    monkeypatch.setattr('tegrastats_api.parser.subprocess.Popen', lambda *a, **k: fake)
    samplers = []
    def factory(interval):
        s = GPUSampler(interval, FakeReader());samplers.append(s);return s
    p = TegrastatsParser(platform_reader=lambda: platform('thor'), gpu_factory=factory)
    server = TegrastatsServer();server.parser = p
    client = server.socketio.test_client(server.app)
    try:
        p.start();s = samplers[0];deadline = time.monotonic()+1
        while not s.history and time.monotonic()<deadline:time.sleep(.002)
        fake.lines.put('tj@46C');await_seq(p,1)
        http = server.app.test_client().get('/api/status').json
        tick(server,monkeypatch)
        events = [x['args'][0] for x in client.get_received() if x['name']=='tegrastats_update']
        assert events and events[-1]['gpu']==http['gpu'] and events[-1]['platform']==http['platform']
        assert events[-1]['telemetry_meta']['sample_seq']==http['telemetry_meta']['sample_seq']==1
        health = server.app.test_client().get('/api/health').json
        assert health['package_version']=='1.1.1' and health['capabilities']['platform']==platform('thor')
        record('thor-contract', http)
    finally:
        client.disconnect();p.stop()


def test_release_error_is_not_successful_shutdown():
    class Reader(FakeReader):
        def close(self):raise RuntimeError('synthetic release failure')
    r=Reader();s=GPUSampler(1000,r);s.start()
    deadline=time.monotonic()+1
    while not s.history and time.monotonic()<deadline:time.sleep(.002)
    with pytest.raises(RuntimeError,match='resource release failed'):s.stop()
    assert s.diagnostics()['state']=='cleanup_error' and not s.diagnostics()['running']


def test_slow_gpu_no_parallel_catchup_records_actual_cadence():
    class Reader(FakeReader):
        active=0;maximum=0
        def read(self):
            self.active+=1;self.maximum=max(self.maximum,self.active)
            time.sleep(.035);self.active-=1
            return 0
    r=Reader();s=GPUSampler(10,r);s.start();deadline=time.monotonic()+2
    while s.diagnostics()['reads']<3 and time.monotonic()<deadline:time.sleep(.002)
    s.stop();d=s.diagnostics()
    assert r.maximum==1 and d['missed']>=3 and d['period_count']>=2
    assert d['period_ms_max']>=30 and d['read_ms_max']>=30 and d['sample_interval_ms']==10
