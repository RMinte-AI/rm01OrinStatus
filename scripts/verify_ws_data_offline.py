#!/usr/bin/env python3
"""Recheck the local wheel in a clean directory without contacting any device.

Requires the documented Python test dependencies and a host C compiler.
Pip installs only the supplied wheel, with --no-index and --no-deps.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=root / 'rerun-results')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    wheels = list((root / 'evidence/ws-data/wheel').glob('*.whl'))
    assert len(wheels) == 1, 'Exactly one candidate wheel required'
    wheel = wheels[0]
    with zipfile.ZipFile(wheel) as archive:
        identities = {}
        for name in ('parser.py', 'server.py', 'cli.py', 'config.py',
                     'model_monitor.py', '__init__.py', 'py.typed'):
            source = (root / 'src/tegrastats_api' / name).read_bytes()
            assert source == archive.read('tegrastats_api/' + name), 'Wheel/source mismatch'
            identities[name] = hashlib.sha256(source).hexdigest()
        contents = archive.namelist()
    (output/'wheel_identity.json').write_text(json.dumps({
        'wheel_sha256': hashlib.sha256(wheel.read_bytes()).hexdigest(),
        'source_sha256': identities, 'contents': contents}, indent=2))
    with tempfile.TemporaryDirectory(prefix='ws-data-review-') as temp:
        clean = Path(temp)
        shutil.copytree(root / 'tests/offline', clean / 'tests/offline',
                        ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copytree(root / 'evidence/ws-data/baseline', clean / 'evidence/ws-data/baseline')
        installed = clean / 'installed'
        subprocess.run([sys.executable, '-m', 'pip', 'install', '--no-index', '--no-deps',
                        '--target', str(installed), str(wheel)], check=True, cwd=clean)
        env = dict(os.environ, PYTHONPATH=str(installed), EVIDENCE_OUTPUT=str(output))
        env['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
        subprocess.run([sys.executable, '-m', 'pytest', '-q', 'tests/offline',
                        '--junitxml=' + str(output / 'junit.xml')], check=True, cwd=clean, env=env)
        subprocess.run([sys.executable, 'tests/offline/verify_resources.py',
                        '--output', str(output)], check=True, cwd=clean, env=env)
        loaded = json.loads((output/'loaded_source.json').read_text())
        assert str(installed) in loaded['parser_file']
        assert loaded['parser_sha256'] == identities['parser.py']
        # The directory is removed on exit; retain only its classification/hash.
        loaded['loaded_from_isolated_wheel'] = True
        (output/'loaded_source.json').write_text(json.dumps(loaded, indent=2))
    print('Offline wheel replay passed. No device, service or live collector was started.')


if __name__ == '__main__':
    main()
