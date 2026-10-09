"""Only launched as a temporary host test child; signals target this PID only."""
import json
import os
import signal
import socket
import subprocess
import sys
import threading

if os.environ.get('REPLAY_REVIEWED_CANDIDATE'):
    import faulthandler
    faulthandler.dump_traceback_later(1, repeat=True)


def denied(*args, **kwargs):
    raise AssertionError('CLI child forbids network and real collector/monitor')


socket.socket.connect = denied
socket.socket.connect_ex = denied
socket.socket.bind = denied
socket.create_connection = denied
subprocess.Popen = denied

from click.testing import CliRunner
import tegrastats_api.cli as cli_module
from tegrastats_api.server import TegrastatsServer
from tegrastats_api.model_monitor import ModelMonitor
import tegrastats_api.server as server_module
from types import SimpleNamespace


scenario = sys.argv[1]
processes, servers, commands = [], [], []
monitor_starts = []
listener_calls = []
captured = {}
failed_exit_signals = []


def exit_signal():
    os.kill(os.getpid(), signal.SIGTERM)


class Process:
    def __init__(self):
        self.stdout = self
        self.closed = False
        self.code = None
        self.finished = threading.Event()
        self.terminate_count = self.wait_count = self.kill_count = 0
    def readline(self):
        self.finished.wait()
        return ''
    def close(self):
        self.closed = True
    def poll(self):
        return self.code
    def terminate(self):
        self.terminate_count += 1
        if scenario in ('closing', 'repeated', 'wait-timeout'):
            exit_signal()
            exit_signal()
        # Keep poll unreaped until wait so the timeout/kill path is exercised.
        self.finished.set()
    def wait(self, timeout=None):
        self.wait_count += 1
        if scenario == 'wait-timeout' and timeout is not None:
            exit_signal()
            raise subprocess.TimeoutExpired('fake tegrastats', timeout)
        self.code = -9 if self.kill_count else 0
        return self.code
    def kill(self):
        self.kill_count += 1
        self.finished.set()


def popen(command, **kwargs):
    commands.append(command)
    if scenario == 'failed-start':
        exit_signal()
        raise OSError('simulated launch failure')
    process = Process()
    processes.append(process)
    if scenario in ('starting', 'repeated'):
        # Signal after creation, before parser registers the returned handle.
        exit_signal()
        exit_signal()
    return process


subprocess.Popen = popen
ModelMonitor.start = lambda self: monitor_starts.append(True)
def monitor_stop(self):
    if scenario == 'failed-start' and not failed_exit_signals:
        failed_exit_signals.append(True)
        # An additional exit request after parser has reported the launch error.
        exit_signal()
ModelMonitor.stop = monitor_stop
original_server = TegrastatsServer


def construct(config):
    if scenario == 'before-start':
        exit_signal()
    server = original_server(config)
    servers.append(server)
    captured['config'] = config.to_dict()
    captured['log_level_effective'] = __import__('logging').getLogger().level
    captured['sources'] = getattr(config, 'sources', {})
    captured['consumers'] = {'limiter': server.limiter.max_connections,
                             'cors': server.socketio.server.eio.cors_allowed_origins,
                             'http_cors': server.app.test_client().get('/api/health',headers={'Origin':'https://example.test'}).headers.get('Access-Control-Allow-Origin'),
                             'parser_timeout': server.parser._freshness_timeout_ms}
    def sleep(interval):
        captured['update_sleep_seconds'] = interval
        server._running = False
    server_module.time = SimpleNamespace(sleep=sleep)
    def listen(*args, **kwargs):
        listener_calls.append(True)
        captured['listen_kwargs'] = kwargs
        p = server.parser
        assert p._accept_line('tj@45.75C VDD_A 0mW/2mW', p._stream_id,
                              '2026-10-09T00:00:00Z', __import__('time').monotonic())
        captured['before_exit'] = p.get_snapshot()
        if scenario == 'running':
            exit_signal()
    server.socketio.run = listen  # Never binds or starts a real web server.
    return server


cli_module.TegrastatsServer = construct
result = CliRunner().invoke(cli_module.cli, ['run'] + sys.argv[2:])
if servers:
    server = servers[0]
    p = server.parser
    snapshot = p.get_snapshot()
    captured.update({'config_interval': server.config.tegrastats_interval,
                     'parser_interval': p.interval, 'final_snapshot': snapshot,
                     'thread_alive': bool(p._thread and p._thread.is_alive())})
    identity = snapshot[1]['stream_id']
    sequence = snapshot[1]['sample_seq']
    server.stop()  # Explicit repeat cleanup, even after CLI finally.
    if server._shutdown_requested:
        server.start()  # Exit intent must prevent an accidental new lifecycle.
    assert p.get_snapshot()[1]['stream_id'] == identity
    assert p.get_snapshot()[1]['sample_seq'] == sequence
    assert not p._accept_line('tj@99C', identity, 'ignored', 0)
print(json.dumps({'scenario': scenario, 'exit_code': result.exit_code,
                  'output': result.output, 'commands': commands,
                  'monitor_starts': len(monitor_starts), 'listener_calls': len(listener_calls),
                  'failed_exit_signals': len(failed_exit_signals),
                  'processes': [{'terminate': p.terminate_count, 'wait': p.wait_count,
                                 'kill': p.kill_count, 'reaped': p.code is not None,
                                 'stdout_closed': p.closed} for p in processes],
                  'captured': captured}))
