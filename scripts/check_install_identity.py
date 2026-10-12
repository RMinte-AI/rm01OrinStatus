#!/usr/bin/env python3
"""Local package identity only; never starts a collector or queries NVML."""
import argparse
import hashlib
import importlib.metadata
import inspect
import json
from pathlib import Path
import sys
import zipfile
import tegrastats_api
from tegrastats_api.parser import TegrastatsParser

p = argparse.ArgumentParser()
p.add_argument('--wheel', required=True)
p.add_argument('--source', required=True)
p.add_argument('--output', required=True)
a = p.parse_args()
installed = Path(inspect.getfile(TegrastatsParser)).resolve().parent
assert 'site-packages' in installed.parts
files = {}
with zipfile.ZipFile(a.wheel) as z:
    for n in z.namelist():
        if n.endswith(('.dist-info/METADATA', '.dist-info/WHEEL', '.dist-info/entry_points.txt')):
            assert Path(importlib.metadata.distribution('tegrastats-api').locate_file(n)).read_bytes() == z.read(n)
        if not n.startswith('tegrastats_api/') or not n.endswith('.py'): continue
        relative = n[len('tegrastats_api/'):]
        content = z.read(n)
        assert content == (installed/relative).read_bytes() == (Path(a.source)/'src/tegrastats_api'/relative).read_bytes()
        files[relative] = hashlib.sha256(content).hexdigest()
try:
    nvml_version = importlib.metadata.version('nvidia-ml-py')
except importlib.metadata.PackageNotFoundError:
    nvml_version = None
result = {'version': tegrastats_api.__version__, 'distribution_version': importlib.metadata.version('tegrastats-api'),
          'python': sys.version, 'executable': sys.executable, 'parser_file': str(installed/'parser.py'),
          'source_equals_wheel_equals_installed': True, 'non_editable': True, 'source_sha256': files,
          'wheel_sha256': hashlib.sha256(Path(a.wheel).read_bytes()).hexdigest(),
          'nvml_binding': nvml_version, 'device_queries': False,
          'effective_service_execstart': 'not_checked_offline'}
assert result['version'] == result['distribution_version'] == '1.1.1'
Path(a.output).parent.mkdir(parents=True, exist_ok=True)
with Path(a.output).open('x') as f:json.dump(result,f,ensure_ascii=False,indent=2)
print(json.dumps(result,ensure_ascii=False,indent=2))
