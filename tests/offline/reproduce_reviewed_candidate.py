"""Expected-failure replay against the preserved reviewed candidate, host only."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def main():
    root = Path(__file__).resolve().parents[2]
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ws-reviewed-') as temp:
        package = Path(temp) / 'tegrastats_api'
        shutil.copytree(root / 'src/tegrastats_api', package,
                        ignore=shutil.ignore_patterns('__pycache__'))
        for name in ('parser.py', 'server.py', 'cli.py', 'config.py'):
            shutil.copy2(root / 'evidence/ws-data/revision-baseline' / name, package / name)
        results = []
        for scenario, args in [('starting', []), ('closing', []),
                               ('interval', ['--tegrastats-interval', '1000'])]:
            env = dict(os.environ, PYTHONPATH=temp, REPLAY_REVIEWED_CANDIDATE='1')
            child = subprocess.Popen([sys.executable, str(Path(__file__).with_name('cli_process_harness.py')),
                                      scenario] + args,
                                     env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            timed_out = False
            try:
                stdout, stderr = child.communicate(timeout=4)
            except subprocess.TimeoutExpired:
                timed_out = True
                child.kill()
                stdout, stderr = child.communicate()
            result = {'scenario': scenario, 'timed_out': timed_out,
                      'host_child_returncode': child.returncode, 'stderr': stderr}
            if scenario != 'interval':
                assert timed_out and 'signal_handler' in stderr and 'stop' in stderr, stderr
            else:
                result['cli_result'] = json.loads(stdout)
                assert result['cli_result']['exit_code'] == 1
                assert not result['cli_result']['commands']
            results.append(result)
    (out / 'reviewed_candidate_counterexamples.json').write_text(json.dumps(results, indent=2))
    print('Preserved candidate: startup/cleanup deadlocks and explicit-1000 rejection reproduced.')


if __name__ == '__main__':
    main()
