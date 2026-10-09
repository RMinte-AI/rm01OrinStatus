import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tegrastats_api.config import Config
from test_data import record


# The only real subprocess allowed by these tests is this reviewed Python child.
# Inside the child, Popen is replaced before the production CLI is imported.
HOST_POPEN = subprocess.Popen
HARNESS = Path(__file__).with_name('cli_process_harness.py')


def invoke(scenario, *arguments):
    child = HOST_POPEN([sys.executable, str(HARNESS), scenario] + list(arguments),
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                       env=dict(os.environ))
    try:
        stdout, stderr = child.communicate(timeout=8)
    except subprocess.TimeoutExpired:
        child.kill()
        stdout, stderr = child.communicate()
        pytest.fail('CLI child failed to exit: ' + stderr)
    assert child.returncode == 0, stderr
    return json.loads(stdout)


@pytest.mark.parametrize('scenario', ['before-start', 'starting', 'running', 'closing',
                                      'repeated', 'failed-start', 'wait-timeout'])
def test_cli_signal_lifecycle(scenario):
    result = invoke(scenario)
    assert result['exit_code'] == (1 if scenario == 'failed-start' else 0)
    captured = result['captured']
    meta = captured['final_snapshot'][1]
    assert meta['collector_state'] == ('error' if scenario == 'failed-start' else 'stopped')
    assert not meta['fresh'] and not captured['thread_alive']
    for process in result['processes']:
        assert process['reaped'] and process['stdout_closed']
        assert process['terminate'] == 1
        assert process['kill'] == (1 if scenario == 'wait-timeout' else 0)
        assert process['wait'] == (2 if scenario == 'wait-timeout' else 1)
    if scenario in ('before-start', 'starting', 'repeated', 'failed-start'):
        assert result['monitor_starts'] == result['listener_calls'] == 0
    if scenario == 'before-start':
        assert not result['commands'] and meta['sample_seq'] == 0
    if scenario == 'failed-start':
        assert result['failed_exit_signals'] == 1
    if 'before_exit' in captured:
        old_meta = captured['before_exit'][1]
        assert meta['stream_id'] == old_meta['stream_id']
        assert meta['sample_seq'] == old_meta['sample_seq'] == 1
    record('cli_signal_' + scenario, result)


@pytest.mark.parametrize('arguments,expected', [([], 1000),
    (['--tegrastats-interval', '1000'], 1000),
    (['--tegrastats-interval', '1000.0'], 1000),
    (['--tegrastats-interval', '1'], 1),
    (['--tegrastats-interval', '3000'], 3000)])
def test_cli_interval_to_process(arguments, expected):
    result = invoke('interval', *arguments)
    assert result['exit_code'] == 0
    assert result['commands'] == [['tegrastats', '--interval', str(expected)]]
    assert result['captured']['config_interval'] == result['captured']['parser_interval'] == expected
    assert result['captured']['final_snapshot'][1]['freshness_timeout_ms'] == max(5000, 3 * expected)
    assert all(p['reaped'] and p['stdout_closed'] for p in result['processes'])
    record('cli_interval_' + (arguments[-1] if arguments else 'default'), result)


@pytest.mark.parametrize('value', ['0', '-1', 'nan', 'inf', '-inf', '1000.5', 'abc'])
def test_cli_rejects_invalid_interval(value):
    result = invoke('interval', '--tegrastats-interval', value)
    assert result['exit_code'] == 2
    assert not result['commands'] and not result['captured']
    record('cli_interval_invalid_' + value, result)


def test_config_interval_normalization(monkeypatch):
    for value in (1000, 1000.0, '1000.0'):
        assert Config(tegrastats_interval=value).tegrastats_interval == 1000
    monkeypatch.setenv('TEGRASTATS_API_TEGRASTATS_INTERVAL', '3000.0')
    assert Config.from_env().tegrastats_interval == 3000
    for value in (False, None, 0, -1, 1.5, float('inf'), float('nan')):
        with pytest.raises(ValueError):
            Config(tegrastats_interval=value)
