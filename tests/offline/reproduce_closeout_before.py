"""Reproduce F1/F2 using saved pre-closeout sources; only self-owned host children."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

PIPE_CHECK = r'''
import json, socket, subprocess, sys
real_popen = subprocess.Popen
def deny(*a, **k): raise AssertionError('network forbidden')
socket.socket.connect = socket.socket.bind = deny
from tegrastats_api.parser import TegrastatsParser
import tegrastats_api.parser as module
children, threads = [], []
def output(*a, **k):
    child=real_popen([sys.executable,'-u','-c','import time; print("tj@45C",flush=True); time.sleep(30)'],stdout=subprocess.PIPE,text=True)
    children.append(child)
    return child
class Unstarted:
    def __init__(self, **kwargs): threads.append(kwargs)
    def start(self): raise RuntimeError('injected thread start failure')
module.subprocess.Popen=output
module.threading.Thread=Unstarted
p=TegrastatsParser()
try:
    try: p.start()
    except RuntimeError: pass
    child=children[0]
    print(json.dumps({'reaped':child.poll() is not None,'stdout_closed':child.stdout.closed,'snapshot':p.get_snapshot(),'strong_thread_reference':bool(threads)}))
finally:
    for child in children:
        if child.poll() is None: child.kill(); child.wait(timeout=3)
        child.stdout.close()
'''


def main():
    root=Path(__file__).resolve().parents[2]
    out=Path(sys.argv[1]); out.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ws-closeout-before-') as temp:
        package=Path(temp)/'tegrastats_api'
        shutil.copytree(root/'src/tegrastats_api',package,ignore=shutil.ignore_patterns('__pycache__'))
        for name in ('parser.py','server.py','cli.py','config.py'):
            shutil.copy2(root/'evidence/closeout/before'/name,package/name)
        env={k:v for k,v in os.environ.items() if not k.startswith('TEGRASTATS')}
        env['PYTHONPATH']=temp
        pipe=json.loads(subprocess.check_output([sys.executable,'-c',PIPE_CHECK],env=env,timeout=8))
        assert pipe['reaped'] and not pipe['stdout_closed'] and pipe['strong_thread_reference']
        env['TEGRASTATS_API_TEGRASTATS_INTERVAL']='3000'
        config=json.loads(subprocess.check_output([sys.executable,str(Path(__file__).with_name('cli_process_harness.py')),'interval'],env=env,timeout=8))
        assert config['exit_code']==0 and config['commands']==[['tegrastats','--interval','1000']]
        (out/'F1_F2_before.json').write_text(json.dumps({'F1':pipe,'F2':config},indent=2))
    print('Pre-closeout F1 unclosed real pipe and F2 ignored environment reproduced.')


if __name__=='__main__': main()
