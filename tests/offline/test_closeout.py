import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest
from click.testing import CliRunner

from tegrastats_api import TegrastatsParser
from tegrastats_api import parser as production
from tegrastats_api.cli import cli
from tegrastats_api.config import Config, ENV_FIELDS
from test_data import FakeProcess, await_seq, record, collector
from test_cli_integration import invoke, HOST_POPEN

REAL_THREAD = threading.Thread


@pytest.mark.parametrize('fault', ['construct', 'start'])
def test_unstarted_reader_real_pipe(monkeypatch, fault):
    processes, retained_threads = [], []
    def output_process(command, **kwargs):
        assert command == ['tegrastats', '--interval', '1000']
        child = HOST_POPEN([sys.executable, '-u', '-c',
                           'import time; print("tj@45.75C", flush=True); time.sleep(30)'],
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        processes.append(child)
        return child
    class Unstarted:
        def __init__(self, **kwargs):
            self.args = kwargs
            retained_threads.append(self)
        def start(self):
            raise RuntimeError('injected reader start failure')
    def failed_constructor(**kwargs):
        retained_threads.append(kwargs)
        raise RuntimeError('injected reader construction failure')
    monkeypatch.setattr(production.subprocess, 'Popen', output_process)
    monkeypatch.setattr(production.threading, 'Thread', failed_constructor if fault == 'construct' else Unstarted)
    p = TegrastatsParser()
    try:
        with pytest.raises(RuntimeError, match='injected reader'):
            p.start()
        old_stream = p.get_snapshot()[1]['stream_id']
        assert processes[0].poll() is not None
        assert processes[0].stdout.closed
        assert retained_threads  # Strong external references, no GC invoked.
        assert p._process is p._thread is p._unclaimed_output is None
        assert p.get_snapshot()[1]['collector_state'] == 'error'
        assert p.get_snapshot()[1]['sample_seq'] == 0 and not p.get_snapshot()[1]['fresh']
        p.stop()
        p.stop()
        assert p.get_snapshot()[1]['collector_state'] == 'error'
        monkeypatch.setattr(production.threading, 'Thread', REAL_THREAD)
        fake = FakeProcess()
        monkeypatch.setattr(production.subprocess, 'Popen', lambda *a, **k: fake)
        p.start()
        fake.lines.put('tj@50C')
        await_seq(p, 1)
        assert p.get_snapshot()[1]['stream_id'] != old_stream
        assert p.get_current_status()['temperature']['tj'] == 50
        retained = retained_threads[0]
        kwargs = retained if isinstance(retained, dict) else retained.args
        kwargs['target'](*kwargs['args'])  # Cancelled delayed entry cannot claim old pipe.
        assert p.get_snapshot()[1]['sample_seq'] == 1
        record('closeout_F1_' + fault, {'real_child_reaped': True, 'real_stdout_closed': True,
                                      'external_reference_retained': True, 'restart_snapshot': p.get_snapshot()})
    finally:
        p.stop()
        for child in processes:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=3)
            child.stdout.close()


def test_start_failure_after_reader_claim(monkeypatch):
    entered = threading.Event()
    close_threads = []
    fake = FakeProcess()
    original_read = fake.readline
    original_close = fake.close
    def read():
        entered.set()
        return original_read()
    def close():
        close_threads.append(threading.get_ident())
        original_close()
    fake.readline, fake.close = read, close
    class FailedAfterClaim(REAL_THREAD):
        def start(self):
            super().start()
            assert entered.wait(2)
            raise RuntimeError('injected after reader claim')
    monkeypatch.setattr(production.subprocess, 'Popen', lambda *a, **k: fake)
    monkeypatch.setattr(production.threading, 'Thread', FailedAfterClaim)
    p = TegrastatsParser()
    with pytest.raises(RuntimeError, match='after reader claim'):
        p.start()
    p.stop()
    assert fake.closed and close_threads == [p._thread.ident]
    assert close_threads[0] != threading.get_ident()
    assert p.get_snapshot()[1]['collector_state'] == 'error'


def test_original_start_error_survives_cleanup_error(monkeypatch, caplog):
    fake = FakeProcess()
    terminate = fake.terminate
    def failed_terminate():
        raise OSError('injected termination error')
    monkeypatch.setattr(fake, 'terminate', failed_terminate)
    monkeypatch.setattr(production.subprocess, 'Popen', lambda *a, **k: fake)
    def fail_thread(**kwargs):
        raise RuntimeError('original thread failure')
    monkeypatch.setattr(production.threading, 'Thread', fail_thread)
    p = TegrastatsParser()
    with pytest.raises(RuntimeError, match='original thread failure'):
        p.start()
    assert fake.closed and p._process is fake
    assert 'cleanup also failed' in caplog.text
    monkeypatch.setattr(fake, 'terminate', terminate)
    p.stop()
    p.stop()
    assert p._process is None and p.get_snapshot()[1]['collector_state'] == 'error'


def test_failed_pipe_close_remains_owned_for_retry(monkeypatch, caplog):
    fake = FakeProcess()
    original_close = fake.close
    calls = []
    def close():
        calls.append(True)
        if len(calls) == 1:
            raise OSError('injected close failure')
        original_close()
    fake.close = close
    monkeypatch.setattr(production.subprocess, 'Popen', lambda *a, **k: fake)
    def fail(**kwargs):
        raise RuntimeError('original launch error')
    monkeypatch.setattr(production.threading, 'Thread', fail)
    p = TegrastatsParser()
    with pytest.raises(RuntimeError, match='original launch error'):
        p.start()
    assert p._unclaimed_output is fake and not fake.closed
    assert 'pipe close failed' in caplog.text
    p.stop()
    p.stop()
    assert fake.closed and p._unclaimed_output is None and len(calls) == 2
    assert p.get_snapshot()[1]['collector_state'] == 'error'


@pytest.mark.parametrize('env,args,expected,source', [
    ({}, [], 1000, '内置默认'),
    ({'TEGRASTATS_API_TEGRASTATS_INTERVAL':'3000'}, [], 3000, 'TEGRASTATS_API_TEGRASTATS_INTERVAL'),
    ({'TEGRASTATS_INTERVAL':'3000'}, [], 3000, 'TEGRASTATS_INTERVAL'),
    ({'TEGRASTATS_API_TEGRASTATS_INTERVAL':'3000','TEGRASTATS_INTERVAL':'2000'}, [], 3000, 'TEGRASTATS_API_TEGRASTATS_INTERVAL'),
    ({'TEGRASTATS_API_TEGRASTATS_INTERVAL':'3000'}, ['--tegrastats-interval','1000.0'],1000,'CLI --tegrastats-interval'),
    ({'TEGRASTATS_API_TEGRASTATS_INTERVAL':'bad'}, ['--tegrastats-interval','1000'],1000,'CLI --tegrastats-interval'),
])
def test_env_interval_real_cli(monkeypatch, env, args, expected, source):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    result = invoke('interval', *args)
    assert result['exit_code'] == 0
    assert result['commands'] == [['tegrastats','--interval',str(expected)]]
    assert result['captured']['sources']['tegrastats_interval'] == source
    assert result['captured']['consumers']['parser_timeout'] == max(5000,3*expected)
    preview = CliRunner().invoke(cli, ['config'] + args)
    assert preview.exit_code == 0
    assert preview.output.split('：\n',1)[1] in result['output']
    record('closeout_F2_interval_' + source + '_' + str(expected), result)


@pytest.mark.parametrize('env,args,expected', [
    ('true',[],True), ('true',['--no-debug'],False), ('false',['--debug'],True),
    ('true',['--debug','--no-debug'],False), ('false',['--no-debug','--debug'],True),
    ('bad',['--no-debug'],False)])
def test_debug_override(monkeypatch, env, args, expected):
    monkeypatch.setenv('TEGRASTATS_API_DEBUG',env)
    result=invoke('interval',*args)
    assert result['exit_code']==0
    assert result['captured']['listen_kwargs']['debug'] is expected
    if args:
        assert result['captured']['sources']['debug']=='CLI '+args[-1]


@pytest.mark.parametrize('field,value', [('host',''),('port','0'),('port','65536'),
    ('debug','typo'),('update_interval','0'),('update_interval','-1'),
    ('update_interval','nan'),('update_interval','inf'),('max_connections','0'),
    ('tegrastats_interval','bad'),('tegrastats_interval',''),('log_level','TRACE'),
    ('cors_origins',''),('log_file',''),('allow_unsafe_werkzeug','typo')])
def test_invalid_selected_env_no_side_effects(monkeypatch, field, value):
    primary, legacy=ENV_FIELDS[field]
    monkeypatch.setenv(primary,value)
    monkeypatch.setenv(legacy,str(Config().to_dict()[field]))
    result=invoke('interval')
    assert result['exit_code']==1 and not result['commands'] and not result['captured']
    assert field in result['output'] and primary in result['output']
    preview=CliRunner().invoke(cli,['config'])
    assert preview.exit_code==1 and primary in preview.output
    for args in (['--help'],['run','--help'],['config','--help'],['--version']):
        assert CliRunner().invoke(cli,args).exit_code==0


def test_all_config_consumers_and_preview(monkeypatch):
    values={'host':'127.0.0.1','port':'58091','debug':' OFF ','update_interval':'0.25',
            'max_connections':'7','tegrastats_interval':'3000.0','cors_origins':'https://example.test',
            'log_level':'warning','log_file':'local-test.log','allow_unsafe_werkzeug':'no'}
    for name,value in values.items():
        monkeypatch.setenv(ENV_FIELDS[name][0],value)
    result=invoke('interval')
    assert result['exit_code']==0
    actual=result['captured']
    assert actual['listen_kwargs']=={'host':'127.0.0.1','port':58091,'debug':False}
    assert actual['consumers']=={'limiter':7,'cors':'https://example.test','http_cors':'https://example.test','parser_timeout':9000}
    assert actual['update_sleep_seconds']==0.25
    assert actual['config']['update_interval']==0.25 and actual['config']['log_level']=='WARNING'
    assert actual['log_level_effective']==30
    assert actual['config']['allow_unsafe_werkzeug'] is False
    assert '文件日志：未接线' in result['output']
    preview=CliRunner().invoke(cli,['config'])
    assert preview.exit_code==0 and preview.output.split('：\n',1)[1] in result['output']
    assert Config().tegrastats_interval==1000  # Explicit constructor ignores env.
    record('closeout_F2_all_fields',result)


@pytest.mark.parametrize('value,expected',[('true',True),('1',True),(' YES ',True),('On',True),
                                        ('false',False),('0',False),(' NO ',False),('Off',False)])
def test_environment_booleans(monkeypatch,value,expected):
    monkeypatch.setenv('TEGRASTATS_DEBUG',value)
    monkeypatch.setenv('TEGRASTATS_ALLOW_UNSAFE_WERKZEUG',value)
    result=invoke('interval')
    assert result['exit_code']==0
    assert result['captured']['listen_kwargs']['debug'] is expected
    assert result['captured']['config']['allow_unsafe_werkzeug'] is expected


def test_all_cli_overrides_and_repeat_preview(monkeypatch):
    for name in ('host','port','debug','update_interval','max_connections','tegrastats_interval','log_level'):
        monkeypatch.setenv(ENV_FIELDS[name][0],'bad')
    args=['--host','localhost','--port','58092','--no-debug','--update-interval','0.5',
          '--max-connections','2','--tegrastats-interval','1000','--log-level','INFO']
    result=invoke('interval',*args)
    assert result['exit_code']==0
    assert result['captured']['listen_kwargs']['port']==58092
    runner=CliRunner()
    for _ in range(2):
        preview=runner.invoke(cli,['config']+args)
        assert preview.exit_code==0 and preview.output.split('：\n',1)[1] in result['output']


@pytest.mark.parametrize('name,value,expected', [
    ('host','localhost','localhost'),('port','58093',58093),('debug','yes',True),
    ('update_interval','0.5',0.5),('max_connections','4',4),
    ('tegrastats_interval','3000',3000),('cors_origins','https://example.test','https://example.test'),
    ('log_level','error','ERROR'),('log_file','test-file.log','test-file.log'),
    ('allow_unsafe_werkzeug','off',False)])
def test_all_legacy_aliases_and_primary_priority(monkeypatch,name,value,expected):
    primary,legacy=ENV_FIELDS[name]
    monkeypatch.setenv(legacy,value)
    result=invoke('interval')
    assert result['exit_code']==0
    assert result['captured']['config'][name]==expected
    assert result['captured']['sources'][name]==legacy
    monkeypatch.setenv(legacy,'bad')
    monkeypatch.setenv(primary,value)
    selected=invoke('interval')
    assert selected['exit_code']==0
    assert selected['captured']['config'][name]==expected
    assert selected['captured']['sources'][name]==primary
    record('closeout_F2_alias_' + name, {'legacy':result,'selected_primary':selected})


def test_repeated_run_does_not_reuse_previous_config(monkeypatch, collector):
    import tegrastats_api.cli as module
    from tegrastats_api.server import TegrastatsServer
    from tegrastats_api.model_monitor import ModelMonitor
    observed=[]
    def construct(config):
        server=TegrastatsServer(config)
        server.socketio.run=lambda *a,**k: observed.append((config.to_dict(),k))
        return server
    monkeypatch.setattr(module,'TegrastatsServer',construct)
    monkeypatch.setattr(module.signal,'signal',lambda *a: None)
    monkeypatch.setattr(ModelMonitor,'start',lambda *a: None)
    runner=CliRunner()
    monkeypatch.setenv('TEGRASTATS_API_TEGRASTATS_INTERVAL','3000')
    assert runner.invoke(cli,['run']).exit_code==0
    monkeypatch.delenv('TEGRASTATS_API_TEGRASTATS_INTERVAL')
    assert runner.invoke(cli,['run']).exit_code==0
    assert [item[0]['tegrastats_interval'] for item in observed]==[3000,1000]
    assert collector[2]==[['tegrastats','--interval','3000'],['tegrastats','--interval','1000']]
    assert module._server_instance is None


def test_preview_does_not_construct_server(monkeypatch):
    import tegrastats_api.cli as module
    def denied(*a,**k):
        raise AssertionError('preview constructed a server')
    monkeypatch.setattr(module,'TegrastatsServer',denied)
    monkeypatch.setenv('TEGRASTATS_INTERVAL','3000')
    result=CliRunner().invoke(cli,['config'])
    assert result.exit_code==0 and '采集间隔：3000 ms' in result.output
