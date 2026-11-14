"""Run the existing footage engine on the box, in a private scratch directory.
Only the funded OpenAI key crosses stdin over SSH; it is never saved or logged.
No service, switch, customer's project or production data is changed.
"""
from pathlib import Path
import io
import json
import os
import shlex
import subprocess
import tarfile
import uuid

HOST = 'root@13.140.241.231'
KEY = str(Path.home() / '.ssh/valmera_box_ed25519')
REMOTE = r'''import json, os, sys
sys.path.insert(0, '/app')
os.environ['OPENAI_API_KEY'] = sys.stdin.readline().strip()
os.environ['VALMERA_FOOTAGE'] = '1'
os.environ['SKIP_DB_INIT'] = '1'
from footage import run, cut, plan
cut.MAX_S = 7.8
run.SHOT_S = 4.5
plan.PLAN_SYSTEM += '\nThis run is a 16:9 long documentary. Average picture length is 4.5 seconds. Match every line exactly; exclude still montages.'
find = run.find
j=json.load(open(sys.argv[1]))
find(j['lines'],j['named'],j['about'],sys.argv[2],1920,1080,
     max_sources=10, per=2, avoid=j.get('avoid'), log=lambda s:print(s,flush=True))
'''


def ssh(command, **kw):
    return subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=20', '-i', KEY, HOST, command], check=True, **kw)


def fetch(payload, out, log=print):
    out = Path(out)
    rec = out / 'footage.json'
    if rec.exists():
        return json.loads(rec.read_text())
    out.mkdir(parents=True, exist_ok=True)
    names = ssh("docker ps --format '{{.Names}}'", capture_output=True, text=True).stdout.splitlines()
    slot = next((n for n in names if n.startswith('vbox-batch')), None)
    if not slot:
        raise RuntimeError('no box batch image available for footage')
    scratch = '/tmp/wf4-' + uuid.uuid4().hex
    q = shlex.quote
    package = io.BytesIO()
    with tarfile.open(fileobj=package, mode='w') as tf:
        for name, data in [('input.json', json.dumps(payload).encode()), ('fetch.py', REMOTE.encode())]:
            t = tarfile.TarInfo(name); t.size = len(data); tf.addfile(t, io.BytesIO(data))
    ssh(f'docker exec {q(slot)} mkdir -p {q(scratch)}', capture_output=True)
    try:
        ssh(f'docker exec -i {q(slot)} tar xf - -C {q(scratch)}', input=package.getvalue(), capture_output=True)
        cmd = f'docker exec -i {q(slot)} python -u {q(scratch)}/fetch.py {q(scratch)}/input.json {q(scratch)}/out'
        with open(out / 'fetch.log', 'w') as f:
            ssh(cmd, input=os.environ['OPENAI_API_KEY']+'\n', text=True, stdout=f, stderr=subprocess.STDOUT, timeout=7200)
        archive = out / 'transfer.tar'
        with open(archive, 'wb') as f:
            ssh(f'docker exec {q(slot)} tar cf - -C {q(scratch)}/out .', stdout=f, timeout=600)
        with tarfile.open(archive) as tf:
            tf.extractall(out, filter='data')
        archive.unlink()
        result = json.loads(rec.read_text())
        log(f"box footage: {result['counts']}")
        return result
    finally:
        ssh(f'docker exec {q(slot)} rm -rf {q(scratch)}', capture_output=True)
