"""Actual local prefix install/upgrade and old-process evidence. Never starts services.

Reviewed children are only supplied Python interpreters running venv/pip, the
candidate installer, identity checker, and an in-process Flask health probe.
No systemctl, collector, model monitor or GPU initialization is invoked.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


PROBE = '''import importlib.metadata as m,json,os,socket,subprocess,sys
def denied(*a,**k):raise AssertionError("No network or collection")
socket.socket.connect=socket.socket.connect_ex=socket.socket.bind=denied
socket.create_connection=denied;subprocess.Popen=denied
import tegrastats_api as p
from tegrastats_api.server import TegrastatsServer
s=TegrastatsServer()
def identity():return {"pid":os.getpid(),"executable":sys.executable,"package_version":p.__version__,"distribution_version_on_disk":m.version("tegrastats-api"),"package_file":p.__file__,"health":s.app.test_client().get("/api/health").json}
print(json.dumps(identity()),flush=True)
if len(sys.argv)>1:
 input()
 print(json.dumps(identity()),flush=True)
'''


def run(command, **kwargs):
    return subprocess.run([str(x) for x in command], check=True, **kwargs)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('python', 'wheel', 'old-wheel', 'wheelhouse', 'output'):
        p.add_argument('--' + name, required=True, type=Path)
    args = p.parse_args()
    root = Path(__file__).resolve().parents[2]
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ, PYTHONPATH='/invalid/shadowed/source')
    retained = out/'retained-user-config'
    retained.mkdir()
    (retained/'service.override.conf').write_text('[Service]\nExecStart=/original/environment/bin/tegrastats-api run\n')
    (retained/'service.env').write_text('TEGRASTATS_API_TEGRASTATS_INTERVAL=3000\nTEGRASTATS_API_DEBUG=false\n')
    config_hashes = {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in retained.iterdir()}
    prefix = out / 'temporary-prefix'
    run([args.python, '-m', 'venv', prefix])
    python = prefix / 'bin/python'
    run([python, '-I', '-m', 'pip', 'install', '--no-index', '--find-links', args.wheelhouse,
         args.old_wheel.resolve()], env=env)
    # A loaded old server persists across on-disk upgrade, without binding a port.
    old = subprocess.Popen([str(python), '-I', '-u', '-c', PROBE, 'hold'], env=env,
                           stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        before = json.loads(old.stdout.readline())
        # Retained old wheel declares dist 1.0.1 but internal version 1.0.0;
        # record this real inconsistency rather than rewriting the old wheel.
        assert before['package_version'] == '1.0.0' and before['distribution_version_on_disk'] == '1.0.1'
        digest = hashlib.sha256(args.wheel.read_bytes()).hexdigest()
        install = [sys.executable, root/'scripts/install_candidate.py', '--python', python,
                   '--wheel', args.wheel.resolve(), '--sha256', digest, '--wheelhouse',
                   args.wheelhouse.resolve(), '--output', out/'orin-install.json']
        bad = list(install)
        bad[bad.index('--sha256')+1] = '0' * 64
        rejected = subprocess.run([str(x) for x in bad], env=env, capture_output=True, text=True)
        assert rejected.returncode != 0 and not (out/'orin-install.json').exists()
        unchanged = json.loads(subprocess.check_output([str(python), '-I', '-c', PROBE], env=env, text=True))
        assert unchanged['distribution_version_on_disk'] == '1.0.1'
        (out/'wrong-sha.json').write_text(json.dumps({'rejected': True, 'unchanged_distribution_version': '1.0.1',
                                                   'stderr': rejected.stderr}, indent=2))
        run(install, env=env)
        installed = json.loads((out/'orin-install.json').read_text())
        assert installed['after']['nvml_binding'] is None and not installed['after']['pynvml_imported']
        old.stdin.write('\n'); old.stdin.flush()
        held = json.loads(old.stdout.readline())
        old.wait(timeout=10)
        assert old.returncode == 0 and held['pid'] == before['pid']
        assert held['package_version'] == '1.0.0' and held['distribution_version_on_disk'] == '1.1.1'
        current = json.loads(subprocess.check_output([str(python), '-I', '-c', PROBE], env=env, text=True))
        assert current['package_version'] == current['health']['package_version'] == '1.1.1'
        assert current['pid'] != held['pid']
        run([python, '-I', root/'scripts/check_install_identity.py', '--wheel', args.wheel.resolve(),
             '--source', root, '--output', out/'orin-identity.json'], env=env)
        thor = list(install)
        thor[thor.index('--output')+1] = out/'thor-install.json'
        thor.append('--thor')
        run(thor, env=env)
        binding = json.loads((out/'thor-install.json').read_text())['after']
        assert binding['nvml_binding'] == '13.615.71' and not binding['pynvml_imported']
        # An old override can select an entirely different prefix. Exercise that
        # supplied executable, without pretending to inspect the user's systemd.
        legacy = out/'legacy-override-prefix'
        run([args.python, '-m', 'venv', legacy])
        legacy_python = legacy/'bin/python'
        run([legacy_python, '-I', '-m', 'pip', 'install', '--no-index', '--find-links', args.wheelhouse,
             args.old_wheel.resolve()], env=env)
        override = json.loads(subprocess.check_output([str(legacy_python), '-I', '-c', PROBE], env=env, text=True))
        assert override['distribution_version_on_disk'] == '1.0.1' and override['executable'] != current['executable']
        result = {'old_pid_before_upgrade': before, 'old_pid_after_disk_upgrade': held,
                  'new_process_after_upgrade': current, 'simulated_old_override': override,
                  'old_pid_is_not_candidate': True, 'override_executable_is_not_candidate': True,
                  'user_systemd_checked': False, 'service_started': False, 'device_queries': False}
        assert config_hashes == {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in retained.iterdir()}
        result['local_config_fixture_sha256_preserved'] = config_hashes
        (out/'runtime-identity.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        if old.poll() is None:
            old.kill(); old.wait()
    print('Local install/upgrade, optional binding, wrong-SHA, old PID and override replay passed.')


if __name__ == '__main__': main()
