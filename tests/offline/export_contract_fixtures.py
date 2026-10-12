"""Deterministic producer handoff; no collection, driver or network calls.

Thor text is retained historical device evidence, NOT a new device test. Platform
tokens, GPU outcomes, clocks and lifecycle below are explicitly synthetic.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import uuid
from unittest.mock import patch


def denied(*args, **kwargs):
    raise AssertionError('Fixture export forbids network and collection')


from tegrastats_api.parser import TegrastatsParser
from tegrastats_api.platform_gpu import GPUSampler
from tegrastats_api.server import TegrastatsServer


HERE = Path(__file__).parent
STAMP = '2026-10-12T00:00:10Z'


def platform(family):
    if family == 'unknown':
        return {'schema_version': 1, 'family': 'unknown', 'soc': None,
                'identification_source': None, 'compatible': [], 'gpu_backend': 'unavailable'}
    soc = {'orin': 'tegra234', 'thor': 'tegra264'}[family]
    return {'schema_version': 1, 'family': 'jetson_' + family, 'soc': soc,
            'identification_source': 'device_tree_compatible', 'compatible': ['nvidia,' + soc],
            'gpu_backend': 'nvml' if family == 'thor' else 'tegrastats'}


class SyntheticReader:
    device_uuid = 'GPU-synthetic-contract-fixture'


def build_cases():
    orin = json.loads((HERE / 'fixtures.json').read_text())['repository_line']
    thor = (HERE / 'thor-recorded-tegrastats.txt').read_text().splitlines()[0]
    cases = {}
    for name, family, status, value, completed in [
        ('orin-zero', 'orin', None, None, None),
        ('orin-nonzero', 'orin', None, None, None),
        ('orin-missing', 'orin', None, None, None),
        ('thor-zero', 'thor', 'valid', 0, 9.9),
        ('thor-nonzero', 'thor', 'valid', 37.5, 9.9),
        ('thor-error', 'thor', 'error', None, 9.9),
        ('thor-unavailable', 'thor', 'unavailable', None, 9.9),
        ('thor-stale', 'thor', 'valid', 12, 5),
        ('unknown', 'unknown', None, None, None),
    ]:
        parser = TegrastatsParser(3000, platform_reader=lambda: platform(family))
        parser._stream_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'fixture:' + name))
        parser._running = True
        parser._collector_state = 'running'
        if family == 'thor':
            sampler = GPUSampler(1000, SyntheticReader())
            sampler._save(value, status, completed, STAMP if status == 'valid' else None)
            parser._gpu_sampler = sampler
        line = thor if family == 'thor' else orin
        if name == 'orin-nonzero': line = line.replace('GR3D_FREQ 0%', 'GR3D_FREQ 37%')
        if name == 'orin-missing': line = line.replace('GR3D_FREQ 0%', '')
        with patch('tegrastats_api.parser.time.monotonic', return_value=10):
            assert parser._accept_line(line, parser._stream_id, STAMP, 10)
            server = TegrastatsServer()
            server.parser = parser
            response = server.app.test_client().get('/api/status')
            assert response.status_code == 200
            body = response.json
            # Send time is mutable, intentionally normalized for fixed fixture SHA.
            body['timestamp'] = STAMP
            cases[name] = body
            if name == 'thor-zero':
                parser._gpu_sampler._save(99, 'valid', 10.1, STAMP)
                repeat = server.app.test_client().get('/api/status').json
                repeat['timestamp'] = STAMP
                assert repeat == body
                cases['thor-repeat'] = repeat
                assert parser._accept_line(line, parser._stream_id, STAMP, 10)
                new = server.app.test_client().get('/api/status').json
                new['timestamp'] = STAMP
                assert new['telemetry_meta']['sample_seq'] == 2 and new['gpu'] == body['gpu']
                cases['thor-same-value-new-input'] = new
    cases['thor-error-with-legacy-zero'] = copy.deepcopy(cases['thor-error'])
    cases['thor-error-with-legacy-zero']['gpu']['gr3d_freq'] = 0
    # Consumer-only negative probes, deliberately NOT legal producer output.
    cases['identity-conflict-platform'] = copy.deepcopy(cases['thor-zero'])
    cases['identity-conflict-platform']['platform'] = platform('orin')
    cases['lifecycle-conflict-source-period'] = copy.deepcopy(cases['thor-same-value-new-input'])
    cases['lifecycle-conflict-source-period']['telemetry_meta']['source_sample_interval_ms'] = 1000
    legacy = TegrastatsParser.parse_line(orin)
    legacy['timestamp'] = STAMP
    cases['legacy-zero'] = legacy
    cases['legacy-empty-gpu'] = copy.deepcopy(legacy)
    cases['legacy-empty-gpu']['gpu'] = {}
    return cases


def export(output):
    output.mkdir(parents=True, exist_ok=False)
    files = {}
    for name, body in sorted(build_cases().items()):
        path = output / (name + '.json')
        path.write_text(json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True,
                                   allow_nan=False) + '\n')
        files[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {'contract': 'TTO-PLATFORM-CURVE-v2.0', 'producer_version': '1.1.1',
                'files': files, 'live_device_test': False,
                'provenance': 'Thor raw text retained from 20261012 device-install evidence; all GPU, platform and clocks synthetic',
                'consumer_only_negative_probes': ['identity-conflict-platform.json', 'lifecycle-conflict-source-period.json'],
                'legacy_compatibility': ['legacy-zero.json', 'legacy-empty-gpu.json']}
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'path': str(output.resolve()), 'manifest_sha256':
                      hashlib.sha256((output / 'manifest.json').read_bytes()).hexdigest()}))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', required=True, type=Path)
    with patch.object(socket.socket, 'connect', denied), patch.object(socket.socket, 'connect_ex', denied), \
         patch.object(socket.socket, 'bind', denied), patch.object(socket, 'create_connection', denied), \
         patch.object(subprocess, 'Popen', denied):
        export(p.parse_args().output)
