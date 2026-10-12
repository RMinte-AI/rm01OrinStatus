"""Reviewed child: real CLI/server/parser, fake process/GPU, self-signals only."""
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

def denied(*args, **kwargs):
    raise AssertionError('GPU CLI child forbids network and real collectors')

socket.socket.connect = socket.socket.connect_ex = socket.socket.bind = denied
socket.create_connection = denied
subprocess.Popen = denied

from click.testing import CliRunner
import tegrastats_api.cli as cli_module
import tegrastats_api.server as server_module
import tegrastats_api.parser as parser_module
from tegrastats_api.model_monitor import ModelMonitor
from tegrastats_api.platform_gpu import GPUSampler
from test_data import FakeProcess
from test_platform_gpu import FakeReader, platform

scenario = sys.argv[1]
commands, processes, servers, samplers, gpu_intervals = [], [], [], [], []
monitor_starts, monitor_stops = [], []
entered, release = threading.Event(), threading.Event()
close_failure = {'enabled': True}
close_attempts = []
captured = {}

def exit_signal():
    os.kill(os.getpid(), signal.SIGTERM)

class Reader(FakeReader):
    def open(self):
        super().open()
        if scenario == 'gpu-init-signal':
            entered.set()
            assert release.wait(2)
    def close(self):
        close_attempts.append(threading.get_ident())
        if scenario == 'gpu-close-signal':
            exit_signal()
            exit_signal()
        if scenario in ('source-thread-and-cleanup-error', 'gpu-signal-cleanup-error') and close_failure['enabled']:
            raise OSError('secondary GPU cleanup failure')
        super().close()

class Sampler(GPUSampler):
    def start(self):
        if scenario == 'gpu-start-failure':
            raise RuntimeError('optional GPU startup failure')
        super().start()
        if scenario == 'gpu-init-signal':
            assert entered.wait(1)
            exit_signal()  # Main thread still in parser.start and deferred phase.
            release.set()
        if scenario == 'source-thread-and-cleanup-error':
            deadline = time.monotonic() + 1
            while not self.history:
                assert time.monotonic() < deadline
                time.sleep(.001)

def factory(interval):
    gpu_intervals.append(interval)
    if scenario == 'gpu-factory-failure':
        raise RuntimeError('optional GPU construction failure')
    sampler = Sampler(interval, Reader([0]))
    samplers.append(sampler)
    return sampler

def popen(command, **kwargs):
    commands.append(command)
    process = FakeProcess()
    processes.append(process)
    return process

subprocess.Popen = popen
ModelMonitor.start = lambda self: monitor_starts.append(True)
ModelMonitor.stop = lambda self: monitor_stops.append(True)
real_thread = threading.Thread
if scenario == 'source-thread-and-cleanup-error':
    def thread(**kwargs):
        if getattr(kwargs.get('target'), '__name__', '') == '_reader_entry':
            raise RuntimeError('original source reader startup failure')
        return real_thread(**kwargs)

real_server = server_module.TegrastatsServer
def construct(config):
    server = real_server(config)
    servers.append(server)
    if scenario == 'source-thread-and-cleanup-error':
        # Engine.IO defines a Thread subclass while constructing the server.
        # Inject only after construction, at the intended parser reader path.
        parser_module.threading.Thread = thread
    server.parser._platform_reader = lambda: platform('thor')
    server.parser._gpu_factory = factory
    def sleep(interval): server._running = False
    server_module.time = SimpleNamespace(sleep=sleep)
    def listen(*args, **kwargs):
        if samplers and samplers[0].thread is not None:
            deadline = time.monotonic() + 1
            while not samplers[0].history:
                assert time.monotonic() < deadline
                time.sleep(.001)
        processes[0].lines.put('tj@46C VDD_GPU 123mW/100mW')
        deadline = time.monotonic() + 1
        while server.parser.get_snapshot()[1]['sample_seq'] != 1:
            assert time.monotonic() < deadline
            time.sleep(.001)
        captured['http'] = server.app.test_client().get('/api/status').json
        if scenario in ('gpu-running-signal', 'gpu-signal-cleanup-error'): exit_signal()
    server.socketio.run = listen  # No real listener or bind.
    return server

cli_module.TegrastatsServer = construct
result = CliRunner().invoke(cli_module.cli, ['run'] + sys.argv[2:])
parser_module.threading.Thread = real_thread
assert cli_module._server_instance is None
server = servers[0]
captured['snapshot_before_final_retry'] = server.parser.get_snapshot()
captured['diagnostics_before_final_retry'] = server.parser.capability_diagnostics()
close_failure['enabled'] = False
release.set()
server.stop()
server.stop()
captured['final_snapshot'] = server.parser.get_snapshot()
assert all(p.closed and p.poll() is not None for p in processes)
assert all(not s.thread or not s.thread.is_alive() for s in samplers)
print(json.dumps({'scenario': scenario, 'exit_code': result.exit_code, 'exception_type': type(result.exception).__name__,
                  'output': result.output, 'commands': commands, 'gpu_intervals': gpu_intervals,
                  'gpu_calls': [s.reader.calls for s in samplers], 'close_attempts': close_attempts,
                  'monitor_starts': len(monitor_starts), 'monitor_stops': len(monitor_stops), 'captured': captured}))
