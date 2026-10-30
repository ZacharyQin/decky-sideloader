"""The private driver snapshots actual host interfaces before its network sandbox starts."""
from collections import Counter
import json
import os
from pathlib import Path
import re
import stat
import zipfile

from fixture_assets import HERE, digest
from http_fixture import HttpFixture

DIAGNOSTICS=json.loads((HERE/'fixtures/diagnostics.json').read_text())


def diagnostics_case(name):
    matches=[case for case in DIAGNOSTICS['cases'] if case['id']==name]
    if len(matches)!=1:
        raise ValueError('Unknown diagnostic scenario')
    return {**matches[0],'source':'download','diagnostics':True,'native':'success','package':'overwrite'}


class DiagnosticsHttp(HttpFixture):
    def __init__(self,case):
        self.routes,self.required,self.status_scripts={},{},{}
        self.counts,self.errors,self.clients=Counter(),[],set()
        self.server=None
        self.asset,self.assets='',set()


class DiagnosticsFixture:
    methods={'__inspect_diagnostics__'}

    def __init__(self,home,case):
        self.home,self.case=home,case
        self.addresses=[]
        path=home/'plugins/example/package.json'
        package=json.loads(path.read_text())
        package['fixturePrivateNote']=DIAGNOSTICS['private_note']
        path.write_text(json.dumps(package))

    @property
    def metadata(self):
        return {'diagnostics':self.case,'addresses':self.addresses}

    async def control(self,method,args,record):
        if method!='__inspect_diagnostics__' or len(args)!=1:
            raise ValueError('Unknown diagnostic fixture request')
        path=Path(args[0])
        directory=self.home/'user/Downloads/decky-sideloader-diagnostics'
        if path.is_symlink() or path.parent!=directory or not re.fullmatch(r'diagnostics-[0-9]{19}-[a-f0-9]{32}\.zip',path.name):
            raise ValueError('Diagnostic package outside its owned export directory')
        info=path.stat()
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode)!=0o600 or info.st_nlink!=1 or info.st_uid!=os.getuid():
            raise ValueError('Diagnostic package ownership/permissions changed')
        archive_data=path.read_bytes()
        if not archive_data.startswith(b'decky-sideloader-diagnostics-v1\n') or len(archive_data)>6*1024*1024:
            raise ValueError('Diagnostic package marker/size mismatch')
        with zipfile.ZipFile(path) as archive:
            names=archive.namelist()
            allowed={'summary.json','manifest.json',*(f'logs/{name}' for name in ('active.jsonl','history-1.jsonl','history-2.jsonl','history-3.jsonl'))}
            if len(names)!=len(set(names)) or not {'summary.json','manifest.json','logs/active.jsonl'}<=set(names) or not set(names)<=allowed or archive.testzip():
                raise ValueError('Unexpected diagnostic members or CRC failure')
            if any((entry.external_attr>>16)&0o777!=0o600 for entry in archive.infolist()):
                raise ValueError('Diagnostic member permissions changed')
            members={name:archive.read(name) for name in names}
        payload=b'\n'.join(members.values())
        for forbidden in (DIAGNOSTICS['private_note'].encode(),str(self.home).encode(),b'fixture/example'):
            if forbidden in payload:
                raise ValueError('Original private metadata/path/repository leaked into diagnostics')
        manifest=json.loads(members['manifest.json'])
        if manifest['format']!=2 or manifest['limits']['packageBytes']!=6*1024*1024:
            raise ValueError('Diagnostic manifest contract changed')
        expected={name:{'name':name,'bytes':len(data),'sha256':digest(data)} for name,data in members.items() if name!='manifest.json'}
        actual={item['name']:item for item in manifest['files']}
        if len(actual)!=len(manifest['files']) or actual!=expected:
            raise ValueError('Manifest does not independently match all exported contents')
        summary=json.loads(members['summary.json'])
        events=[]
        for name,data in members.items():
            if name.startswith('logs/'):
                events.extend(json.loads(line) for line in data.splitlines()[1:] if line)
        return {'file':path.name,'bytes':info.st_size,'sha256':digest(archive_data),'members':sorted(names),
            'versions':summary['versions'],'builds':summary['builds'],'events':events,'privateMetadataAbsent':True}
