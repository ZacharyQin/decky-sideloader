import assert from 'node:assert/strict';
import {existsSync, readFileSync, readdirSync} from 'node:fs';
import {join, relative} from 'node:path';
import {root, sha} from './harness.mjs';

export const selfInputs = JSON.parse(readFileSync(join(root,'e2e/fixtures/self-update.json'),'utf8'));
function tree(directory) {
  const values={};
  const visit=path=>{for(const entry of readdirSync(path,{withFileTypes:true})){const file=join(path,entry.name);if(entry.isDirectory())visit(file);else{assert.equal(entry.isFile(),true);values[relative(directory,file)]=sha(readFileSync(file));}}};
  visit(directory);return values;
}
function history(host) {
  const path=join(host.directory,'native-events.jsonl');
  return existsSync(path)?readFileSync(path,'utf8').trim().split('\n').filter(Boolean).map(row=>JSON.parse(row)):[];
}
async function state(host, stage, frontend=host.build) {
  return host.eventually(async()=>{
    const status=await host.call('get_self_update_status',{version:frontend.version,fingerprint:frontend.fingerprint});
    return status.operation?.stage===stage && status;
  },`self-update ${stage}`);
}
export async function fullSelfUpdate(host,spec,candidateRoot) {
  assert.ok(candidateRoot);
  const info=JSON.parse(readFileSync(join(candidateRoot,'.sideloader-e2e-candidate.json'),'utf8'));
  const original=tree(join(host.directory,'home/plugins/decky-sideloader'));
  const before=await host.call('__metadata__');
  assert.equal(before.identity.version,info.source_version);
  assert.notEqual(before.identity.version,info.version,'candidate must actually have another executable version');
  assert.notEqual(before.identity.sha256,info.binary_sha256);
  await host.call('set_check_notifications',false);
  const settingsFile=join(host.directory,'home/settings/decky-sideloader/settings.json');
  const originalSettings=readFileSync(settingsFile);
  const initial=await host.call('get_self_update_status',{version:info.source_version,fingerprint:'0'.repeat(64)});
  assert.equal(initial.operation,null,'complete self-update starts before discovery, without a seeded record');
  await host.mount();const oldFrontend={...host.build};
  await host.click('settings');await host.eventually(()=>host.actionEnabled('selfUpdateCheck'),'actual self-update check control');
  await host.click('selfUpdateCheck');await host.eventually(()=>host.actionEnabled('selfUpdateDownload'),'new release discovered by frontend/Python/Rust');
  assert.ok(host.frontendCalls.includes('check_self_update'));
  await host.click('selfUpdateDownload');assert.ok(host.frontendCalls.includes('prepare_self_update'));
  if(spec.gate) {
    await host.eventually(async()=>Object.values((await host.call('__metadata__')).http.gates).some(Boolean),'actual asset request reached deterministic server gate');
    await state(host,'downloading',oldFrontend);
    if(spec.action==='cancel_downloading') {
      await host.eventually(()=>host.actionEnabled('selfUpdateCancel'),'download cancellation control');await host.click('selfUpdateCancel');
    } else {
      await host.unmount(false);await host.call('__kill_core__');await host.restart();await host.mount();await host.click('settings');
    }
    await state(host,spec.stage,oldFrontend);
    await host.eventually(async()=>(await host.call('__metadata__')).http.aborted.length===1,'cancelled/dead core closed the exact gated HTTP request');
  } else if(['bad_digest','download_error'].includes(spec.action)) {
    await state(host,'failed',oldFrontend);
  } else {
    await state(host,'ready',oldFrontend);
    await host.eventually(()=>host.actionEnabled('selfUpdateInstall'),'prepared candidate reached rendered install control');
    if(spec.action==='cancel_ready') {
      await host.click('selfUpdateCancel');await state(host,'cancelled',oldFrontend);
    } else {
      if(spec.action==='restart_ready') {
        await host.unmount(false);const restarted=await host.restart();assert.notEqual(restarted.boot,restarted.previous_boot);
        await host.mount();await host.click('settings');await host.eventually(()=>host.actionEnabled('selfUpdateInstall'),'persisted prepared update recovered without native dispatch');
        assert.equal(history(host).length,0);
      }
      await host.click('selfUpdateInstall');await host.eventually(()=>host.prompt?.name==='Sideloader','real self-update dispatch reached native confirmation boundary');
      await host.eventually(()=>host.selfEventAcks.some(event=>event.kind==='prompt'),'self-update native request correlation acknowledged');
      if(spec.action==='cancel_prompt') {
        await host.eventually(()=>host.actionEnabled('selfUpdateCancel'),'correlated native cancel enabled');await host.click('selfUpdateCancel');await state(host,'cancelled',oldFrontend);
      } else {
        // The external Loader unloads the old frontend before returning its own confirmation call.
        host.selfNativeLifecycle=async args=>{
          const offset=host.selfEventAcks.length;
          await host.perform(async()=>host.emit('loader/plugin_download_start','Sideloader'));
          await host.eventually(()=>host.selfEventAcks.slice(offset).some(event=>event.kind==='start'&&event.stage==='installing'),'old core persisted correlated start before host reload');
          await host.unmount(false);
          const result=await host.call('__native_confirm__',...args);
          host.selfReload=await host.restart();
          return result;
        };
        await host.loader.call('utilities/confirm_plugin_install',host.prompt.request_id);
        host.selfNativeLifecycle=null;
        const changed=await host.call('__metadata__');
        assert.equal(changed.identity.version,info.version);assert.equal(changed.identity.sha256,info.binary_sha256);
        assert.equal(changed.identity.fingerprint,info.backend.fingerprint);
        assert.notEqual(changed.identity.boot_id,before.identity.boot_id);
        assert.notEqual(changed.core_pid,before.core_pid);
        assert.equal(changed.identity.boot_id,host.selfReload.boot);
        const withoutNewFrontend=await state(host,'verifying',oldFrontend);
        assert.equal(withoutNewFrontend.frontend_compatible,false,'new program alone cannot prove new frontend loaded');
        if(spec.action==='wrong_frontend') {
          await host.mount();await host.click('settings');
          const stale=await state(host,'verifying',oldFrontend);assert.equal(stale.frontend_compatible,false);
          assert.ok(host.frontendCallDetails.some(row=>row.method==='get_self_update_status'&&row.args[0]?.version===info.source_version));
          assert.equal(history(host).filter(row=>row.kind==='dispatch').length,1);
          await host.unmount(false);
        }
        await host.mount(candidateRoot);await host.click('settings');
        const completed=await state(host,'completed',info.frontend);assert.equal(completed.current,info.version);assert.equal(completed.frontend_compatible,true);
        await host.eventually(()=>host.visible().includes('Sideloader update completed'),'completion rendered by actually rebuilt newer frontend');
        assert.ok(host.frontendCallDetails.some(row=>row.method==='get_self_update_status'&&row.args[0]?.version===info.version&&row.args[0]?.fingerprint===info.frontend.fingerprint));
        assert.equal(sha(readFileSync(join(host.directory,'home/plugins/decky-sideloader/bin/sideloader-core'))),info.binary_sha256);
        assert.equal(sha(readFileSync(join(host.directory,'home/plugins/decky-sideloader/dist/index.js'))),info.frontend.bundle);
        assert.equal(existsSync(join(host.directory,'home/data/decky-sideloader/self-update',completed.operation.id,'plugin.zip')),false,'verified update package cleaned');
        assert.equal(await host.call('get_operation'),null,'self-update never becomes a normal install operation');
      }
    }
  }
  const metadata=await host.call('__metadata__');const log=history(host);
  assert.deepEqual(metadata.history,log);
  for(const[kind,expected]of Object.entries({dispatch:spec.dispatches,confirm:spec.confirms,cancel:spec.cancels}))assert.equal(log.filter(row=>row.kind===kind).length,expected,`self-update exact native ${kind}`);
  assert.deepEqual(metadata.http.errors,[]);assert.deepEqual(metadata.http.mismatches,{});
  for(const[url,count]of Object.entries(metadata.http.required))assert.equal(metadata.http.counts[url]??0,count);
  assert.equal(metadata.http.aborted.length,spec.gate?1:0);
  assert.equal(sha(readFileSync(join(host.directory,'home/data/decky-sideloader/private-user-note.bin'))),metadata.fixture.note_sha256);
  assert.deepEqual(readFileSync(settingsFile),originalSettings,'self-update preserves user settings exactly');
  if(!spec.confirms)assert.deepEqual(tree(join(host.directory,'home/plugins/decky-sideloader')),original,'failure/cancellation did not replace original program');
  return{id:spec.id,from:info.source_version,to:info.version,stage:spec.stage,nativeDispatches:spec.dispatches,nativeConfirms:spec.confirms,
    actualNewExecutable:spec.confirms===1,actualNewFrontend:spec.confirms===1,
    identities:{before:before.identity,after:metadata.identity,expectedBinary:info.binary_sha256,expectedFrontend:info.frontend},
    httpRequests:Object.values(metadata.http.counts).reduce((a,b)=>a+b,0),
    source:'discovery/download/native lifecycle/new PID+boot/executable+bundle identity; no seeded update operation'};
}
