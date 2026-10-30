"""Remote private-profile runtime probe; stdin is the locally verified runtime ZIP."""
import asyncio
import hashlib
import importlib.util
import io
import json
import logging
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import sys
import types
import zipfile

EXPECTED={'decky-sideloader/'+name for name in ('LICENSE','plugin.json','package.json','main.py','data/repositories.json','bin/sideloader-core','dist/index.js','dist/build.json','dist/backend.json','dist/notices.json')}
RUNNING_PLUGIN=None
RUNNING_CHILD=None


async def probe(stage, archive_bytes):
    global RUNNING_PLUGIN,RUNNING_CHILD
    home=stage/'home'
    plugin_dir=home/'plugins/decky-sideloader'
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        if set(archive.namelist())!=EXPECTED or len(archive.infolist())!=len(EXPECTED) or sum(item.file_size for item in archive.infolist())>32*1024*1024 or archive.testzip() is not None:
            raise ValueError('Runtime ZIP member/size/CRC contract')
        for item in archive.infolist():
            if stat.S_ISLNK(item.external_attr>>16):raise ValueError('Runtime ZIP link')
            target=home/'plugins'/item.filename
            target.parent.mkdir(parents=True,exist_ok=True)
            with target.open('xb') as stream:stream.write(archive.read(item))
            target.chmod(0o700 if item.filename.endswith('/bin/sideloader-core') else 0o600)
    metadata=json.loads((plugin_dir/'dist/backend.json').read_text())
    version=json.loads((plugin_dir/'package.json').read_text())['version']
    if metadata['version']!=version or hashlib.sha256((plugin_dir/'bin/sideloader-core').read_bytes()).hexdigest()!=metadata['sha256']:raise ValueError('Runtime binary identity')
    for path in (home/'settings/decky-sideloader',home/'data/decky-sideloader',home/'logs/decky-sideloader',home/'user'):
        path.mkdir(parents=True,exist_ok=True)
    (home/'settings/decky-sideloader/settings.json').write_text('{"autoCheck":false,"networkMode":"direct"}')
    (home/'settings/loader.json').write_text('{"frozenPlugins":[]}')
    sdk=types.ModuleType('decky')
    events=[]
    async def emit(name,*args):events.append(name)
    sdk.emit=emit
    sdk.logger=logging.getLogger('sideloader-private-device-profile')
    for key,value in {'DECKY_HOME':home,'DECKY_PLUGIN_DIR':plugin_dir,'DECKY_PLUGIN_VERSION':version,'DECKY_PLUGIN_NAME':'Sideloader','DECKY_PLUGIN_RUNTIME_DIR':home/'data/decky-sideloader','DECKY_PLUGIN_SETTINGS_DIR':home/'settings/decky-sideloader','DECKY_PLUGIN_LOG_DIR':home/'logs/decky-sideloader','DECKY_USER_HOME':home/'user','DECKY_VERSION':''}.items():setattr(sdk,key,str(value))
    sys.modules['decky']=sdk
    spec=importlib.util.spec_from_file_location('sideloader_private_device_adapter',plugin_dir/'main.py')
    bridge=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    plugin=bridge.Plugin()
    RUNNING_PLUGIN=plugin
    child=None
    try:
        async with asyncio.timeout(20):
            await plugin._main()
            child=plugin._process
            RUNNING_CHILD=child
            assert await plugin.ping()=='pong'
            settings=await plugin.get_app_settings()
            assert settings['networkMode']=='direct' and settings['autoCheck'] is False
            assert await plugin.get_operation() is None
            hello=plugin._hello.result()
            report={'scope':'actual target OS/Python/production adapter/release Rust with an isolated SDK boundary and owned profile','actualLoaderPluginLoaded':False,'version':version,'protocol':metadata['protocol'],'runtimeSHA256':metadata['sha256'],'backendFingerprint':metadata['fingerprint'],'pid':child.pid,'handshake':hello,'rpc':['ping','get_app_settings','get_operation'],'python':platform.python_version(),'machine':platform.machine(),'uid':os.getuid(),'events':events}
    finally:
        if RUNNING_CHILD is None:RUNNING_CHILD=plugin._process
        await plugin._unload()
    if child is None or child.returncode!=0 or plugin._pending or plugin._log_tasks or not plugin._reader_task.done() or not plugin._stderr_task.done():raise RuntimeError('Owned runtime failed clean shutdown')
    report.update(status='passed',childExitCode=child.returncode,pendingRPCs=0,readerDone=True,stderrDone=True)
    return report


def main():
    expected_hash=sys.argv[1]
    probe_id=sys.argv[2]
    if not re.fullmatch(r'[a-f0-9]{32}',probe_id):raise ValueError('Invalid owned probe identifier')
    data=sys.stdin.buffer.read(16*1024*1024+1)
    if len(data)>16*1024*1024 or hashlib.sha256(data).hexdigest()!=expected_hash:raise ValueError('Uploaded package checksum/size')
    base=Path.home()/'.local/state'
    base.mkdir(parents=True,exist_ok=True)
    stage=base/('sideloader-private-probe-'+probe_id)
    stage.mkdir(mode=0o700)
    marker=stage/'.neo-probe-owner.json'
    owner={'format':1,'nonce':probe_id,'packageSHA256':expected_hash}
    marker.write_text(json.dumps(owner,sort_keys=True)+'\n')
    marker.chmod(0o600)
    report={'status':'failed','packageSHA256':expected_hash}
    try:
        report.update(asyncio.run(probe(stage,data)))
    except Exception as error:
        report.update(errorType=type(error).__name__,error=str(error)[:800])
    finally:
        stopped=RUNNING_CHILD is None or RUNNING_CHILD.returncode is not None
        safe=stopped and not marker.is_symlink() and json.loads(marker.read_text())==owner and stage.name.startswith('sideloader-private-probe-') and stage.parent==base
        safe=safe and all(not path.is_symlink() and path.lstat().st_uid==os.getuid() and (not path.is_file() or path.stat().st_nlink==1) for path in stage.rglob('*'))
        if safe:
            shutil.rmtree(stage)
            report['cleanup']='removed-owned-profile'
        else:report.update(cleanup='retained-unexpected-entry',retainedDirectory=str(stage))
    print(json.dumps(report,sort_keys=True))
    return 0 if report['status']=='passed' and report['cleanup']=='removed-owned-profile' else 1


if __name__=='__main__':raise SystemExit(main())
