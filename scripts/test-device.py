"""Run the current runtime ZIP in an owned target profile, without Loader deployment."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import uuid
import zipfile

ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--ssh-target',required=True)
parser.add_argument('--package',type=Path,required=True)
parser.add_argument('--report',type=Path,required=True)
args=parser.parse_args()
if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.@:-]{0,199}',args.ssh_target):raise SystemExit('Invalid SSH target')
if args.report.exists():raise SystemExit('Report exists; use a new result path')
paths={name:ROOT/('backend/main.py' if name=='main.py' else name) for name in ('LICENSE','plugin.json','package.json','main.py','data/repositories.json','bin/sideloader-core','dist/index.js','dist/build.json','dist/backend.json','dist/notices.json')}
with zipfile.ZipFile(args.package) as archive:
    expected={'decky-sideloader/'+name for name in paths}
    if len(archive.infolist())!=len(expected) or set(archive.namelist())!=expected or archive.testzip() is not None:raise SystemExit('Invalid runtime package')
    for name,source in paths.items():
        if archive.read('decky-sideloader/'+name)!=source.read_bytes():raise SystemExit('Runtime package differs from current local artifact: '+name)
data=args.package.read_bytes()
if len(data)>16*1024*1024:raise SystemExit('Runtime package exceeds probe budget')
digest=hashlib.sha256(data).hexdigest()
script=(ROOT/'e2e/device_smoke.py').read_text()
probe_id=uuid.uuid4().hex
command='python3 -c '+shlex.quote(script)+' '+shlex.quote(digest)+' '+shlex.quote(probe_id)
try:
    completed=subprocess.run(['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=8',args.ssh_target,command],input=data,capture_output=True,timeout=40)
    try:report=json.loads(completed.stdout)
    except (json.JSONDecodeError,UnicodeDecodeError):report={'status':'failed','error':'Remote probe did not return structured JSON','sshExitCode':completed.returncode}
    if completed.returncode!=0:report['sshExitCode']=completed.returncode
except subprocess.TimeoutExpired:report={'status':'blocked','error':'SSH probe exceeded 40-second deadline; inspect owned remote probe before cleanup'}
report.update(sshTarget=args.ssh_target,expectedPackageSHA256=digest,probeId=probe_id,profileRelativePath='.local/state/sideloader-private-probe-'+probe_id)
args.report.parent.mkdir(parents=True,exist_ok=True)
with args.report.open('x') as stream:json.dump(report,stream,indent=2);stream.write('\n')
print(json.dumps(report,indent=2))
raise SystemExit(0 if report.get('status')=='passed' and report.get('cleanup')=='removed-owned-profile' else 1)
