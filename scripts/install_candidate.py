#!/usr/bin/env python3
"""Install an explicit, hashed local wheel. No service, collector or NVML calls."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


PROBE = '''import importlib.metadata as m,json,sys
try:version=m.version("tegrastats-api")
except m.PackageNotFoundError:version=None
print(json.dumps({"executable":sys.executable,"prefix":sys.prefix,"distribution_version":version}))'''
VERIFY = '''import hashlib,importlib.metadata as m,json,sys,zipfile
from pathlib import Path
import tegrastats_api as p
root=Path(p.__file__).resolve().parent
files={}
with zipfile.ZipFile(sys.argv[1]) as z:
 for name in z.namelist():
  if name.startswith("tegrastats_api/") and name.endswith(".py"):
   relative=name[len("tegrastats_api/"):];data=z.read(name)
   assert (root/relative).read_bytes()==data,relative
   files[relative]=hashlib.sha256(data).hexdigest()
  elif name.endswith((".dist-info/METADATA",".dist-info/WHEEL",".dist-info/entry_points.txt")):
   assert Path(m.distribution("tegrastats-api").locate_file(name)).read_bytes()==z.read(name),name
dist=m.distribution("tegrastats-api")
direct=json.loads(dist.read_text("direct_url.json") or "{}")
assert not direct.get("dir_info",{}).get("editable",False),"editable installation"
assert root==Path(dist.locate_file("tegrastats_api")).resolve(),"import shadowed installed distribution"
assert "site-packages" in root.parts,"non-packaged import"
assert p.__version__==m.version("tegrastats-api")
try:nvml=m.version("nvidia-ml-py")
except m.PackageNotFoundError:nvml=None
print(json.dumps({"executable":sys.executable,"prefix":sys.prefix,"distribution_version":m.version("tegrastats-api"),"package_version":p.__version__,"package_directory":str(root),"source_sha256":files,"nvml_binding":nvml,"pynvml_imported":"pynvml" in sys.modules,"installed_equals_wheel":True,"non_editable":True,"running_service_verified":False,"device_queries":False}))'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheel', required=True, type=Path)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--python', required=True, type=Path, help='Explicit target interpreter; venv symlink preserved')
    parser.add_argument('--wheelhouse', type=Path)
    parser.add_argument('--thor', action='store_true', help='Install declared Thor extra from local wheelhouse')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    wheel = args.wheel.resolve()
    python = args.python.absolute()  # resolve() would lose a venv interpreter symlink.
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    if digest != args.sha256:
        parser.error('wheel SHA256 mismatch; nothing installed')
    if args.thor and args.wheelhouse is None:
        parser.error('--thor requires an offline wheelhouse')
    if args.output.exists():
        parser.error('output already exists; preserve previous installation record')
    before = json.loads(subprocess.check_output([str(python), '-I', '-c', PROBE], text=True))
    command = [str(python), '-I', '-m', 'pip', 'install', '--no-index', '--force-reinstall']
    if args.wheelhouse is not None:
        command += ['--find-links', str(args.wheelhouse.resolve())]
    else:
        command += ['--no-deps']
    command += [str(wheel) + ('[thor]' if args.thor else '')]
    subprocess.run(command, check=True)
    after = json.loads(subprocess.check_output([str(python), '-I', '-c', VERIFY, str(wheel)], text=True))
    if args.thor and not after['nvml_binding']:
        raise RuntimeError('Thor extra did not install its binding')
    result = {'wheel': str(wheel), 'wheel_sha256': digest, 'before': before, 'after': after,
              'installation_command': command, 'systemd_changed': False, 'services_restarted': False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as output:
        json.dump(result, output, ensure_ascii=False, indent=2)
        output.write('\n')
    print('Verified installed candidate. Running service identity remains separately unverified.')


if __name__ == '__main__':
    main()
