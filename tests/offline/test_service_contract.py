"""Producer T1-T8 regressions; all collectors, GPU readers and transports offline."""
import copy
import json
import sys
import threading
import time
from types import SimpleNamespace
from pathlib import Path

import pytest
from tegrastats_api.parser import TegrastatsParser
from tegrastats_api.server import TegrastatsServer
from tegrastats_api.platform_gpu import GPUSampler, NVMLReader, UnsupportedGPU
from test_data import FakeProcess, await_seq, record
from test_platform_gpu import FakeReader, platform
from test_cli_integration import HOST_POPEN


def parser_with_processes(monkeypatch, family='thor', factory=None, interval=3000):
    processes, commands = [], []
    def popen(command, **kwargs):
        commands.append(command)
        process = FakeProcess()
        processes.append(process)
        return process
    monkeypatch.setattr('tegrastats_api.parser.subprocess.Popen', popen)
    parser = TegrastatsParser(interval, platform_reader=lambda: platform(family),
                             gpu_factory=factory or (lambda n: GPUSampler(n, FakeReader())))
    return parser, processes, commands


def wait_for(predicate):
    deadline = time.monotonic() + 2
    while not predicate():
        assert time.monotonic() < deadline
        time.sleep(.002)


@pytest.mark.parametrize('invalid', [True, '0', None, -1, 101, float('nan'), float('inf'), -float('inf')])
def test_T5_invalid_gpu_read_preserves_null_contract(invalid):
    sampler = GPUSampler(1000, FakeReader([invalid]))
    sampler.start()
    try:
        wait_for(lambda: sampler.diagnostics()['reads'] == 1)
        result = sampler.snapshot_at(time.monotonic())
        assert result['status'] == 'error' and result['source'] == 'nvml'
        assert result['utilization_pct'] is result['sampled_at_utc'] is result['age_at_telemetry_receive_ms'] is None
        assert result['sample_interval_ms'] == 1000 and result['freshness_timeout_ms'] == 5000
        json.dumps(result, allow_nan=False)
    finally:
        sampler.stop()


@pytest.mark.parametrize('interval', [0, -1, True, 1.5, '1000', None])
def test_T6_invalid_gpu_schedule_rejected(interval):
    with pytest.raises(ValueError):
        GPUSampler(interval, FakeReader())


def test_T3_error_does_not_reuse_good_result_and_recovery_is_new_source():
    sampler = GPUSampler(1000, FakeReader())
    sampler._save(0, 'valid', 9.9, '2026-10-12T00:00:00Z')
    sampler._save(None, 'error', 10.1, None, 'SyntheticReadError')
    error = sampler.snapshot_at(10.2)
    assert error['status'] == 'error' and error['utilization_pct'] is None
    assert error['sampled_at_utc'] is error['age_at_telemetry_receive_ms'] is None
    sampler._save(0, 'valid', 10.3, '2026-10-12T00:00:01Z')
    assert sampler.snapshot_at(10.4)['status'] == 'valid'
    assert sampler.snapshot_at(10.4)['utilization_pct'] == 0


def test_T3_capture_before_parsing_survives_latest_overwrite():
    parser = TegrastatsParser(platform_reader=lambda: platform('thor'))
    sampler = GPUSampler(1000, FakeReader())
    parser._gpu_sampler = sampler
    parser._running = True
    parser._collector_state = 'running'
    sampler._save(12, 'valid', 9.9, '2026-10-12T00:00:00Z')
    parse_line = parser.parse_line
    def delayed_parse(line):
        for tick in range(20):
            sampler._save(99, 'valid', 10.1 + tick, '2026-10-12T00:00:01Z')
        return parse_line(line)
    parser.parse_line = delayed_parse
    assert parser._accept_line('tj@45C', parser._stream_id, '2026-10-12T00:00:00Z', 10)
    data, meta = parser.get_snapshot()
    assert data['gpu']['utilization_pct'] == 12
    assert data['gpu']['age_at_telemetry_receive_ms'] == pytest.approx(100)
    assert meta['sample_seq'] == 1
    # Bounded history cannot recover the old result later; it reports missing.
    assert sampler.snapshot_at(10)['status'] == 'unavailable'
    assert sampler.snapshot_at(10)['age_at_telemetry_receive_ms'] is None
    frozen = copy.deepcopy(data)
    sampler._save(5, 'valid', 30, '2026-10-12T00:00:02Z')
    assert parser.get_snapshot()[0] == frozen and parser.get_snapshot()[1]['sample_seq'] == 1
    record('T3-causal-sealing', {'data': data, 'meta': meta, 'history_capacity': sampler.history.maxlen})


def test_T5_exact_expiry_and_utc_not_used_for_age():
    sampler = GPUSampler(1000, FakeReader())
    sampler._save(0, 'valid', 1, '2099-01-01T00:00:00Z')
    assert sampler.snapshot_at(5.999)['status'] == 'valid'
    expired = sampler.snapshot_at(6)
    assert expired['status'] == 'stale' and expired['utilization_pct'] is None
    assert expired['age_at_telemetry_receive_ms'] == 5000
    assert expired['sampled_at_utc'] == '2099-01-01T00:00:00Z'
    assert sampler.snapshot_at(.99)['age_at_telemetry_receive_ms'] is None


@pytest.mark.parametrize('fault', ['construct', 'start'])
def test_T4_gpu_start_failure_does_not_fail_source(monkeypatch, fault):
    intervals = []
    class CannotStart(GPUSampler):
        def start(self):
            raise RuntimeError('GPU thread start injected')
    def factory(interval):
        intervals.append(interval)
        if fault == 'construct':
            raise RuntimeError('GPU construction injected')
        return CannotStart(interval, FakeReader())
    parser, processes, commands = parser_with_processes(monkeypatch, factory=factory)
    try:
        parser.start()
        processes[0].lines.put('tj@46C VDD_GPU 123mW/100mW')
        await_seq(parser, 1)
        data, meta = parser.get_snapshot()
        assert meta['fresh'] and meta['collector_state'] == 'running'
        assert meta['source_sample_interval_ms'] == 3000
        assert data['temperature']['tj'] == 46 and data['power']['vdd_gpu']['current'] == 123
        assert data['platform']['gpu_backend'] == 'nvml'
        assert data['gpu']['status'] == 'error' and data['gpu']['utilization_pct'] is None
        assert intervals == [1000] and commands == [['tegrastats', '--interval', '3000']]
    finally:
        parser.stop()


def test_T4_partial_thread_start_keeps_close_owned(monkeypatch):
    reader = FakeReader()
    class StartedThenFailed(GPUSampler):
        def start(self):
            super().start()
            wait_for(lambda: bool(self.history))
            raise RuntimeError('start raised after worker entered')
    sampler = StartedThenFailed(1000, reader)
    parser, processes, _ = parser_with_processes(monkeypatch, factory=lambda n: sampler)
    try:
        parser.start()
        assert reader.closed and not sampler.thread.is_alive()
        processes[0].lines.put('tj@46C')
        await_seq(parser, 1)
        assert parser.get_snapshot()[0]['gpu']['status'] == 'error'
        assert [c[0] for c in reader.calls].count('close') == 1
    finally:
        parser.stop()


def test_T4_pending_old_worker_blocks_second_worker_but_not_TJ(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    class BlockedReader(FakeReader):
        def read(self):
            entered.set()
            assert release.wait(3)
            return 99
    samplers = []
    def factory(interval):
        reader = BlockedReader() if not samplers else FakeReader([0])
        sampler = GPUSampler(interval, reader)
        sampler.JOIN_TIMEOUT_SECONDS = .01
        samplers.append(sampler)
        return sampler
    parser, processes, _ = parser_with_processes(monkeypatch, factory=factory)
    try:
        parser.start()
        assert entered.wait(1)
        first_stream = parser.get_snapshot()[1]['stream_id']
        with pytest.raises(RuntimeError, match='cleanup_pending'):
            parser.stop()
        assert not samplers[0].reader.closed
        parser.start()
        assert len(samplers) == 1 and parser._pending_gpu_sampler is samplers[0]
        processes[1].lines.put('tj@47C')
        await_seq(parser, 1)
        data, meta = parser.get_snapshot()
        assert meta['stream_id'] != first_stream and meta['fresh']
        assert data['gpu']['status'] == 'error' and data['gpu']['utilization_pct'] is None
        release.set()
        samplers[0].thread.join(1)
        assert samplers[0].reader.closed and not samplers[0].history
        assert parser.get_snapshot()[0] == data
        parser.stop()
        parser.start()
        assert len(samplers) == 2
        wait_for(lambda: bool(samplers[1].history))
        processes[2].lines.put('tj@48C')
        await_seq(parser, 1)
        assert parser.get_snapshot()[0]['gpu']['utilization_pct'] == 0
        record('T4-late-owner-restart', {'restarted_data': parser.get_snapshot(), 'old_cleanup': samplers[0].diagnostics()})
    finally:
        release.set()
        parser.stop()


def test_T4_release_failure_is_retryable_by_original_owner():
    class ReleaseOnceFails(FakeReader):
        close_attempts = 0
        def close(self):
            self.close_attempts += 1
            if self.close_attempts == 1:
                raise OSError('release failed once')
            super().close()
    reader = ReleaseOnceFails()
    sampler = GPUSampler(1000, reader)
    sampler.start()
    wait_for(lambda: bool(sampler.history))
    with pytest.raises(RuntimeError, match='cleanup_error'):
        sampler.stop()
    assert sampler.diagnostics()['cleanup_required'] and not reader.closed
    sampler.stop()
    sampler.stop()
    assert reader.closed and reader.close_attempts == 2
    assert not sampler.diagnostics()['cleanup_required']


def test_T4_release_retry_remains_bounded_and_owned():
    entered, release = threading.Event(), threading.Event()
    attempts = []
    class RetryBlocks(FakeReader):
        def close(self):
            attempts.append(threading.get_ident())
            if len(attempts) == 1:
                raise OSError('first release failed')
            entered.set()
            assert release.wait(1)
            super().close()
    reader = RetryBlocks()
    sampler = GPUSampler(1000, reader)
    sampler.JOIN_TIMEOUT_SECONDS = .02
    sampler.start()
    wait_for(lambda: bool(sampler.history))
    try:
        with pytest.raises(RuntimeError, match='cleanup_error'):
            sampler.stop()
        started = time.monotonic()
        with pytest.raises(RuntimeError, match='cleanup_pending'):
            sampler.stop()
        assert entered.is_set() and time.monotonic() - started < .5
        assert sampler.diagnostics()['cleanup_required'] and sampler.thread.is_alive()
        with pytest.raises(RuntimeError, match='cleanup_pending'):
            sampler.stop()
        assert len(attempts) == 2 and threading.get_ident() not in attempts
        assert [call[0] for call in reader.calls].count('open') == 1
    finally:
        release.set()
        sampler.stop()
    sampler.stop()
    assert reader.closed and len(attempts) == 2


@pytest.mark.parametrize('entry', ['start', 'run'])
def test_T4_server_preserves_primary_error_and_cleans_monitor(entry, monkeypatch, caplog):
    server = TegrastatsServer()
    cleaned = []
    def start_failure(): raise RuntimeError('original startup failure')
    def cleanup_failure(): raise OSError('secondary cleanup failure')
    monkeypatch.setattr(server.parser, 'start', start_failure)
    monkeypatch.setattr(server.parser, 'stop', cleanup_failure)
    monkeypatch.setattr(server.model_monitor, 'stop', lambda: cleaned.append(True))
    with pytest.raises(RuntimeError, match='original startup failure'):
        getattr(server, entry)()
    assert cleaned and 'secondary cleanup failure' in caplog.text
    assert not server._shutdown_deferred


def test_T4_update_join_failure_does_not_skip_parser_or_monitor(monkeypatch):
    server = TegrastatsServer()
    cleaned = []
    def fail_join(**kwargs): raise RuntimeError('update join failed')
    server._update_thread = SimpleNamespace(is_alive=lambda: True, join=fail_join)
    monkeypatch.setattr(server.parser, 'stop', lambda: cleaned.append('parser'))
    monkeypatch.setattr(server.model_monitor, 'stop', lambda: cleaned.append('monitor'))
    with pytest.raises(RuntimeError, match='update join failed'):
        server.stop()
    assert cleaned == ['parser', 'monitor']


def test_T4_source_join_error_still_releases_gpu(monkeypatch):
    parser = TegrastatsParser(platform_reader=lambda: platform('thor'))
    released = []
    def fail_join(**kwargs): raise RuntimeError('source join failed')
    parser._thread = SimpleNamespace(is_alive=lambda: True, join=fail_join)
    parser._gpu_sampler = SimpleNamespace(request_stop=lambda: released.append('request'),
                                         stop=lambda: released.append('stop'))
    with pytest.raises(RuntimeError, match='source join failed'):
        parser.stop()
    assert released == ['request', 'stop']
    assert parser.get_snapshot()[1]['collector_state'] == 'error'


def test_T4_worker_owned_pipe_closed_when_process_cleanup_raises(monkeypatch):
    parser = TegrastatsParser()
    process = FakeProcess()
    process.lines.put('')
    parser._running = True
    def fail_cleanup(process): raise OSError('synthetic reap failure')
    monkeypatch.setattr(parser, '_cleanup_process', fail_cleanup)
    parser._parse_output(process, parser._stream_id)
    assert process.stdout.closed
    assert parser.get_snapshot()[1]['collector_state'] == 'error'


def test_T2_platform_input_and_snapshot_cannot_mutate_sealed_identity():
    identity = platform('orin')
    parser = TegrastatsParser(platform_reader=lambda: identity)
    parser._running = True
    parser._collector_state = 'running'
    assert parser._accept_line('tj@45C GR3D_FREQ 0%', parser._stream_id,
                               '2026-10-12T00:00:00Z', time.monotonic())
    identity['compatible'].append('external-mutation')
    data, meta = parser.get_snapshot()
    data['platform']['compatible'].append('snapshot-mutation')
    frozen, repeat = parser.get_snapshot()
    assert frozen['platform'] == platform('orin')
    assert meta['sample_seq'] == repeat['sample_seq'] == 1


def test_T6_actual_interval_frozen_until_new_lifecycle(monkeypatch):
    parser, processes, commands = parser_with_processes(monkeypatch, family='orin')
    try:
        parser.start()
        parser.interval = 1
        processes[0].lines.put('tj@46C GR3D_FREQ 0%')
        await_seq(parser, 1)
        data, meta = parser.get_snapshot()
        assert meta['source_sample_interval_ms'] == data['gpu']['sample_interval_ms'] == 3000
        assert meta['freshness_timeout_ms'] == 9000
        parser.stop()
        parser.start()
        processes[1].lines.put('tj@47C GR3D_FREQ 0%')
        await_seq(parser, 1)
        assert parser.get_snapshot()[1]['source_sample_interval_ms'] == 1
        assert commands == [['tegrastats', '--interval', '3000'], ['tegrastats', '--interval', '1']]
    finally:
        parser.stop()


def test_T7_gpu_ready_does_not_create_first_telemetry(monkeypatch):
    parser, processes, _ = parser_with_processes(monkeypatch)
    server = TegrastatsServer()
    server.parser = parser
    client = server.socketio.test_client(server.app)
    try:
        parser.start()
        wait_for(lambda: bool(parser._gpu_sampler.history))
        assert parser.get_current_status() == {} and parser.get_snapshot()[1]['sample_seq'] == 0
        for route in ('status', 'cpu', 'memory', 'temperature', 'power'):
            assert server.app.test_client().get('/api/' + route).status_code == 503
        health = server.app.test_client().get('/api/health').json
        assert health['status'] == 'healthy' and not health['telemetry_meta']['fresh']
        assert not [event for event in client.get_received() if event['name'] == 'tegrastats_update']
        processes[0].lines.put('tj@46C')
        await_seq(parser, 1)
        cpu = server.app.test_client().get('/api/cpu').json
        assert set(cpu) == {'cpu', 'telemetry_meta', 'timestamp'}
        assert cpu['telemetry_meta']['source_sample_interval_ms'] == 3000
    finally:
        client.disconnect()
        parser.stop()


def test_T5_orin_invalid_percentage_keeps_TJ_and_drops_bad_legacy_value(monkeypatch):
    parser, processes, _ = parser_with_processes(monkeypatch, family='orin')
    try:
        parser.start()
        processes[0].lines.put('tj@46C GR3D_FREQ 101%')
        await_seq(parser, 1)
        data, meta = parser.get_snapshot()
        assert data['temperature']['tj'] == 46 and meta['fresh']
        assert data['gpu']['status'] == 'error' and 'gr3d_freq' not in data['gpu']
    finally:
        parser.stop()


def test_T4_no_shutdown_when_nvml_init_failed(monkeypatch):
    calls = []
    def fail_init(): calls.append('init'); raise RuntimeError('no driver')
    monkeypatch.setitem(sys.modules, 'pynvml', SimpleNamespace(nvmlInit=fail_init, nvmlShutdown=lambda: calls.append('shutdown')))
    reader = NVMLReader()
    with pytest.raises(RuntimeError): reader.open()
    reader.close()
    assert calls == ['init'] and not reader.initialized


def test_T5_unsupported_read_reports_unavailable(monkeypatch):
    class NotSupported(Exception): pass
    def unavailable(handle): raise NotSupported()
    reader = NVMLReader()
    reader.api = SimpleNamespace(nvmlDeviceGetUtilizationRates=unavailable, NVMLError_NotSupported=NotSupported)
    reader.handle = object()
    with pytest.raises(UnsupportedGPU): reader.read()


def test_T1_missing_binding_is_optional_and_keeps_selected_policy(monkeypatch):
    monkeypatch.setitem(sys.modules, 'pynvml', None)
    parser, processes, _ = parser_with_processes(monkeypatch, factory=lambda n: GPUSampler(n))
    try:
        parser.start()
        wait_for(lambda: bool(parser._gpu_sampler.history))
        processes[0].lines.put('tj@46C')
        await_seq(parser, 1)
        data, meta = parser.get_snapshot()
        assert meta['fresh'] and data['platform']['gpu_backend'] == 'nvml'
        assert data['gpu']['status'] == 'unavailable' and data['gpu']['source'] == 'unavailable'
        assert data['gpu']['sample_interval_ms'] == 1000
        assert data['gpu']['sampled_at_utc'] is data['gpu']['age_at_telemetry_receive_ms'] is None
    finally:
        parser.stop()


@pytest.mark.parametrize('scenario', ['gpu-init-signal', 'gpu-close-signal', 'gpu-running-signal',
    'gpu-factory-failure', 'gpu-start-failure', 'source-thread-and-cleanup-error', 'gpu-signal-cleanup-error'])
def test_T4_T6_real_cli_gpu_signal_cleanup_and_command(scenario):
    import os
    harness = Path(__file__).with_name('gpu_cli_harness.py')
    child = HOST_POPEN([sys.executable, str(harness), scenario, '--tegrastats-interval', '3000.0'],
                       stdout=__import__('subprocess').PIPE, stderr=__import__('subprocess').PIPE,
                       text=True, env=dict(os.environ))
    try:
        stdout, stderr = child.communicate(timeout=8)
    except __import__('subprocess').TimeoutExpired:
        child.kill()
        stdout, stderr = child.communicate()
        pytest.fail('GPU CLI child did not exit: ' + stderr)
    assert child.returncode == 0, stderr
    result = json.loads(stdout)
    assert result['commands'] == [['tegrastats', '--interval', '3000']]
    assert result['gpu_intervals'] == [1000]
    meta = result['captured']['final_snapshot'][1]
    assert meta['source_sample_interval_ms'] == 3000 and not meta['fresh']
    if scenario == 'gpu-signal-cleanup-error':
        assert result['exit_code'] == 1
        before = result['captured']['diagnostics_before_final_retry']['gpu_reader']
        assert before['state'] == 'cleanup_error' and before['cleanup_required']
        assert result['monitor_stops'] >= 1 and meta['collector_state'] == 'error'
    elif scenario == 'source-thread-and-cleanup-error':
        assert result['exit_code'] == 1 and result['exception_type'] == 'SystemExit'
        assert '服务器启动失败: original source reader startup failure' in result['output']
        assert meta['collector_state'] == 'error' and result['monitor_stops'] >= 1
        assert result['captured']['diagnostics_before_final_retry']['gpu_reader']['state'] == 'cleanup_error'
    else:
        assert result['exit_code'] == 0 and meta['collector_state'] == 'stopped'
        if scenario == 'gpu-init-signal':
            assert meta['sample_seq'] == 0 and result['monitor_starts'] == 0
        else:
            body = result['captured']['http']
            assert body['temperature']['tj'] == 46 and meta['sample_seq'] == 1
            assert body['gpu']['sample_interval_ms'] == 1000
            expected = 'error' if scenario in ('gpu-factory-failure', 'gpu-start-failure') else 'valid'
            assert body['gpu']['status'] == expected
    record('T4-T6-cli-' + scenario, result)
