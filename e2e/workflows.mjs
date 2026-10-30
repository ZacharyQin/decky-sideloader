import assert from 'node:assert/strict';
import {existsSync, readFileSync, readdirSync} from 'node:fs';
import {dirname, join, relative} from 'node:path';
import {root, sha} from './harness.mjs';
import {ownershipWorkflow, bindingMapWorkflow} from './ownership.mjs';

export const workflowInputs = JSON.parse(readFileSync(join(root, 'e2e/fixtures/workflows.json'), 'utf8'));
const hashes = JSON.parse(readFileSync(join(root, 'e2e/fixtures/workflow-hashes.json'), 'utf8'));
const protectedHashes = JSON.parse(readFileSync(join(root, 'e2e/fixtures/install-hashes.json'), 'utf8')).protected;
function tree(directory) {
  if (!existsSync(directory)) return {};
  const values = {};
  const visit = path => {
    for (const entry of readdirSync(path, {withFileTypes:true})) {
      const file = join(path, entry.name);
      if (entry.isDirectory()) visit(file);
      else { assert.equal(entry.isFile(), true); values[relative(directory,file)] = sha(readFileSync(file)); }
    }
  };
  visit(directory); return values;
}
function history(host) {
  const file = join(host.directory,'native-events.jsonl');
  return existsSync(file) ? readFileSync(file,'utf8').trim().split('\n').filter(Boolean).map(row=>JSON.parse(row)) : [];
}
function protect(host) {
  for (const [name,expected] of Object.entries(protectedHashes)) assert.equal(sha(readFileSync(join(host.directory,'home',name))),expected,`protected data ${name}`);
}
async function openExample(host) {
  const field = host.screen.root.findAll(node => node.type === 'Field' && node.props.label === 'Example' && typeof node.props.onClick === 'function')[0];
  assert.ok(field,'real plugin list entry');
  await host.perform(async()=>field.props.onClick());
  await host.eventually(()=>host.actionEnabled('replace'),'version read completed and upgrade action enabled');
}
async function selectVersion(host, version) {
  await host.click('readReleases');
  const matches = node => node.type==='Field' && node.props['data-release-tag'] === 'v'+version;
  await host.eventually(() => host.screen.root.findAll(matches).length > 0, `actual version list contains ${version}`);
  await host.perform(async()=>{
    const field = host.screen.root.findAll(matches)[0];
    assert.ok(field, `current version list contains ${version}`);
    field.props.onClick();
  });
  await host.eventually(()=>host.visible().includes('v'+version),'selected version reached details');
}
async function checkBackup(host, operation) {
  assert.equal(operation.backup.files,2,'real preparation backed up nonempty settings and data');
  const actual = await host.call('__inspect_backup__',operation.id);
  assert.equal(sha(readFileSync(operation.backup.archive)),actual.sha256);
  assert.equal(actual.sha256,operation.backup.sha256);
  assert.equal(actual.mode,0o600);
  assert.equal(actual.manifest.files,2);
  assert.deepEqual(Object.fromEntries(Object.entries(actual.files).map(([name,encoded])=>[name,sha(Buffer.from(encoded,'base64'))])),protectedHashes,'actual Rust-created backup contains exact protected inputs');
  assert.ok(Object.values(actual.modes).every(mode=>mode===0o600));
}
async function confirmInstallation(host, version, needsBackup=true) {
  await host.eventually(()=>host.prompt && host.prompt.version===version,'prepared version dispatched by the actual frontend');
  const before=host.installEventAcks.length;
  const awaiting=await host.operation('awaiting');
  if(needsBackup) await checkBackup(host,awaiting);
  else assert.equal(awaiting.backup,null);
  const request=host.prompt.request_id;
  await host.perform(async()=>{await host.loader.call('utilities/confirm_plugin_install',request);});
  await host.awaitConfirmationRecorded(before);
  const finished=await host.operation('closed');
  assert.equal(finished.outcome,'success');assert.equal(finished.binding_saved,true);
  assert.deepEqual(tree(join(host.directory,'home/plugins/example')),hashes.packages[version].files,'installed version files match independent frozen hashes');
  protect(host);
  await host.eventually(()=>host.visible().includes('Installation completed') || host.visible().includes('Installation complete'),'verified result rendered');
  return finished;
}

export async function workflow(host, spec) {
  if(spec.action==='crash_verified') return crashVerifiedWorkflow(host,spec);
  if(['crash_backup','crash_download'].includes(spec.action)) return crashPreparationWorkflow(host,spec);
  if(spec.action.startsWith('crash_')) return crashPausedWorkflow(host,spec);
  if(spec.action==='ownership') return ownershipWorkflow(host,spec);
  if(spec.action==='binding_map') return bindingMapWorkflow(host,spec);
  const program=join(host.directory,'home/plugins/example');
  const original=tree(program);
  if(spec.network) await host.call('set_network_mode',spec.network);
  assert.equal(await host.call('get_operation'),null,'workflow begins before preparation, not a seeded ready record');
  await host.mount();await openExample(host);
  const completed=[];
  if(spec.action.startsWith('uninstall')) {
    await host.click('maintenance');await host.click('uninstall');
    await host.respondNativeUninstall(spec.action!=='uninstall_cancel');
    assert.ok(host.frontendCalls.includes('authorize_plugin_mutation'));
    if(spec.action==='uninstall_cancel') assert.deepEqual(tree(program),original);
    else {
      await host.eventually(()=>host.actionEnabled('newInstall'),'native uninstall refreshed actual home list');
      assert.equal(existsSync(program),false);protect(host);
    }
    if(spec.action==='uninstall_install') {
      host.prompt=null;await host.click('newInstall');
      const input=host.screen.root.findAll(node=>node.type==='TextField' && typeof node.props.onChange==='function')[0];assert.ok(input);
      await host.perform(async()=>input.props.onChange({target:{value:'fixture/example'}}));await host.click('search');
      await host.eventually(()=>host.actionEnabled('installPackage'),'repository probe and release preview complete');await host.click('installPackage');
      completed.push(await confirmInstallation(host,'2.0.0',false));
    }
  } else {
    for(const [index,version] of spec.targets.entries()) {
      if(index) {await host.click('back');await host.eventually(()=>host.actionEnabled('readReleases'),'updated installed version detail and releases ready');}
      await selectVersion(host,version);host.prompt=null;
      if(spec.action.startsWith('reinstall')) {
        await host.click('maintenance');await host.click('reinstall');
        await host.respondModal(true,'settings/data');
        await host.eventually(()=>host.modalCount===2 && host.modalScreen,'actual second confirmation after real backup');
        const ready=await host.operation('ready');await checkBackup(host,ready);
        await host.respondModal(spec.action!=='reinstall_cancel','2');
        if(spec.action==='reinstall_cancel') {
          await host.operation('cancelled');assert.deepEqual(tree(program),original);protect(host);break;
        }
      } else {
        if(version==='1.0.0' && index===0){await host.click('maintenance');}
        await host.click('replace');
        if(['retry','continue'].includes(spec.action)) {
          const failed=await host.operation('failed');assert.equal(history(host).length,0,'preparation failure did not dispatch native install');
          if(spec.action==='continue') {
            assert.equal(failed.download.status,'complete');await host.call('__repair_backup__');
            await host.eventually(()=>host.actionEnabled('continueInstall'),'rendered continue action');await host.click('continueInstall');
            assert.ok(host.frontendCalls.includes('continue_prepared'));
          } else {
            assert.equal(failed.download.status,'failed','failed download must publish a failed status so retry is reachable');
            await host.eventually(()=>host.actionEnabled('retryDownload'),'rendered retry action');await host.click('retryDownload');
            assert.ok(host.frontendCalls.includes('retry_download'));
          }
        }
      }
      completed.push(await confirmInstallation(host,version));
    }
  }
  const log=history(host);const metadata=await host.call('__metadata__');
  assert.deepEqual(metadata.history,log,'append-only native boundary log');
  assert.equal(log.filter(row=>row.kind==='dispatch').length,spec.native_types.length);
  assert.equal(log.filter(row=>row.kind==='confirm').length,spec.native_types.length);
  assert.equal(log.filter(row=>row.kind==='uninstall').length,spec.uninstalls);
  assert.equal(log.filter(row=>row.kind==='cancel').length,0);
  assert.deepEqual(log.filter(row=>row.kind==='dispatch').map(row=>row.args[4]),spec.native_types,'actual native action types');
  assert.equal(new Set(log.filter(row=>row.kind==='dispatch').map(row=>row.operation)).size,spec.native_types.length,'no replay across round trips');
  assert.deepEqual(metadata.http.errors,[]);assert.deepEqual(metadata.http.mismatches,{});
  for(const [url,count] of Object.entries(metadata.http.required)) assert.equal(metadata.http.counts[url]??0,count,`independent HTTP consumption ${url}`);
  protect(host);
  if(spec.action==='uninstall_cancel' || spec.action==='uninstall') assert.equal(await host.call('get_operation'),null);
  if(spec.action==='reinstall_cancel') assert.equal(log.length,0);
  return {id:spec.id,entry:'rendered plugin/version/maintenance/install pages; no seeded operation',versions:completed.map(op=>op.package.version),
    nativeTypes:spec.native_types,uninstalls:spec.uninstalls,httpRequests:Object.values(metadata.http.counts).reduce((a,b)=>a+b,0),
    network:spec.network??'direct',protectedFiles:Object.keys(protectedHashes).length};
}

async function workflowHttp(host){
  const metadata=await host.call('__metadata__');assert.deepEqual(metadata.http.errors,[]);assert.deepEqual(metadata.http.mismatches,{});for(const[url,count]of Object.entries(metadata.http.required))assert.equal(metadata.http.counts[url]??0,count);return metadata;
}
async function crashVerifiedWorkflow(host,spec){
  assert.equal(await host.call('get_operation'),null);await host.mount();await openExample(host);await selectVersion(host,'2.0.0');await host.click('replace');await host.eventually(()=>host.prompt,'actual correlated native request');const awaiting=await host.operation('awaiting');await checkBackup(host,awaiting);let confirmation;
  await host.perform(async()=>{confirmation=host.loader.call('utilities/confirm_plugin_install',host.prompt.request_id).then(value=>({value}),error=>({error:String(error)}));});await host.eventually(async()=>{const gate=await host.call('__phase_status__');assert.deepEqual(gate.errors,[]);return gate.entered&&gate.checkpoint==='install-verified';},'actual native start/finish and installed-file verification durably saved before result closure');
  const before=JSON.parse(readFileSync(join(dirname(awaiting.directory),'operation.json'),'utf8'));assert.equal(before.id,awaiting.id);assert.equal(before.stage,'verified');assert.equal(before.saw_start,true);assert.equal(before.saw_finish,true);assert.equal(before.request_id,awaiting.request_id);assert.notEqual(before.outcome,'success');assert.deepEqual(tree(join(host.directory,'home/plugins/example')),hashes.packages['2.0.0'].files);assert.equal(history(host).filter(row=>row.kind==='dispatch').length,1);assert.equal(history(host).filter(row=>row.kind==='confirm').length,1);protect(host);
  await host.unmount(false);assert.equal((await host.call('__kill_core__')).returncode,-9);await host.eventually(async()=>{const gate=await host.call('__phase_status__');assert.deepEqual(gate.errors,[]);return gate.aborted&&gate.clients===0;},'crashed verified-result checkpoint connection ended');await host.call('__phase_disable__');await confirmation;const restarted=await host.restart();assert.notEqual(restarted.boot,restarted.previous_boot);await host.mount();const closed=await host.operation('closed');assert.equal(closed.id,before.id);assert.equal(closed.outcome,'success');assert.equal(closed.close_reason,'verified');assert.equal(closed.backup.sha256,before.backup.sha256);await host.eventually(()=>host.visible().includes('Installation complete'),'new frontend displays actual persisted verified result');assert.deepEqual(tree(join(host.directory,'home/plugins/example')),hashes.packages['2.0.0'].files);assert.equal(history(host).filter(row=>row.kind==='dispatch').length,1,'verified result recovery replayed native install');assert.equal(history(host).filter(row=>row.kind==='confirm').length,1);await checkBackup(host,closed);await workflowHttp(host);protect(host);
  return{id:spec.id,entry:'actual UI/native install → durable verified files/result → SIGKILL → new core/frontend closes existing verified result',nativeDispatches:1,operationReused:true,backupReused:true};
}
async function crashPreparationWorkflow(host,spec){
  const downloading=spec.action==='crash_download';const asset='https://github.com/fixture/example/releases/download/v2.0.0/plugin.zip';
  assert.equal(await host.call('get_operation'),null);const original=tree(join(host.directory,'home/plugins/example'));
  await host.mount();await openExample(host);await selectVersion(host,'2.0.0');await host.click('replace');
  let copying;
  if(downloading){
    copying=await host.eventually(async()=>{const value=await host.call('get_operation');const metadata=await host.call('__metadata__');return metadata.http.gates[asset]&&value?.download?.received===64&&value;},'actual partial main-package body persisted before interruption');assert.equal(copying.stage,'checking');assert.equal(copying.download.status,'downloading');
  }else{
    await host.eventually(async()=>(await host.call('__phase_status__')).entered,'actual Rust backup copied its first content chunk');copying=await host.call('get_operation');assert.equal(copying.stage,'backup');assert.equal(copying.backup,null);assert.equal(copying.download.status,'complete');
  }
  assert.equal(history(host).length,0);protect(host);await host.unmount(false);const killed=await host.call('__kill_core__');assert.equal(killed.returncode,-9,'actual SIGKILL');
  if(downloading)await host.eventually(async()=>{const value=(await host.call('__metadata__')).http;assert.deepEqual(value.errors,[]);return value.aborted.length===1&&value.aborted[0]===asset&&value.active_clients===0;},'killed process closed its actual HTTP body connection');
  else{await host.eventually(async()=>{const value=await host.call('__phase_status__');assert.deepEqual(value.errors,[]);return value.aborted&&value.clients===0;},'old process closed the actual Unix checkpoint connection');await host.call('__phase_disable__');}
  const restarted=await host.restart();assert.notEqual(restarted.boot,restarted.previous_boot);assert.notEqual(restarted.core_pid,killed.core_pid);await host.mount();
  const recovered=await host.operation('failed');assert.equal(recovered.id,copying.id);assert.equal(recovered.download.status,downloading?'failed':'complete');assert.deepEqual(tree(join(host.directory,'home/plugins/example')),original);assert.equal(history(host).length,0);
  const action=downloading?'retryDownload':'continueInstall';await host.eventually(()=>host.actionEnabled('viewTask')||host.actionEnabled(action),'interrupted task surfaced through actual UI');if(!host.actionEnabled(action))await host.click('viewTask');await host.eventually(()=>host.actionEnabled(action),'explicit interrupted-preparation recovery action enabled');await host.click(action);await host.operation('ready');await host.eventually(()=>host.actionEnabled('confirmInstall'),'recovered preparation requires an explicit new-frontend native confirmation');assert.equal(history(host).length,0);await host.click('confirmInstall');
  const finished=await confirmInstallation(host,'2.0.0');assert.equal(finished.id,copying.id);assert.deepEqual(readdirSync(finished.directory).filter(name=>name.startsWith('.backup-')),[],'interrupted owned temporary backup was not reclaimed');const log=history(host);assert.equal(log.filter(row=>row.kind==='dispatch').length,1);assert.equal(log.filter(row=>row.kind==='confirm').length,1);assert.equal(log.filter(row=>row.kind==='dispatch')[0].args[4],spec.native_types[0]);await workflowHttp(host);protect(host);
  return{id:spec.id,entry:'actual UI → interrupted '+(downloading?'partial HTTP download':'backup copy')+' → SIGKILL → new core/frontend → explicit recovery → install',operationReused:true,downloads:downloading?2:1,backupFiles:2,nativeDispatches:1,nativeTypes:spec.native_types};
}
async function crashPausedWorkflow(host,spec){
  const action=spec.action;assert.equal(await host.call('get_operation'),null);const original=tree(join(host.directory,'home/plugins/example'));
  await host.mount();await openExample(host);await selectVersion(host,'2.0.0');let confirmation;
  if(['crash_ready','crash_uninstall'].includes(action)){
    await host.click('maintenance');await host.click('reinstall');await host.respondModal(true,'settings/data');await host.eventually(()=>host.modalCount===2&&host.modalScreen,'actual post-backup confirmation paused before native claim');const ready=await host.operation('ready');await checkBackup(host,ready);
    if(action==='crash_uninstall'){await host.respondModal(true,'2');await host.eventually(async()=>(await host.call('__workflow_metadata__')).uninstall_pending,'actual native uninstall is pending before its file effect');await host.operation('uninstalling');}
  }else{
    await host.click('replace');await host.eventually(()=>host.prompt,'actual correlated native request dispatched');await host.eventually(async()=>(await host.call('get_operation'))?.request_id===host.prompt.request_id,'native prompt identity durably acknowledged');
    if(action==='crash_installing'){
      await host.perform(async()=>{confirmation=host.loader.call('utilities/confirm_plugin_install',host.prompt.request_id);});await host.eventually(()=>host.afterStartGate&&host.installEventAcks.some(event=>event.kind==='start'),'actual native files written and start ACK persisted before finish');const installing=await host.operation('installing');assert.equal(installing.saw_start,true);assert.equal(installing.saw_finish,false);assert.deepEqual(tree(join(host.directory,'home/plugins/example')),hashes.packages['2.0.0'].files);
    }
  }
  const before=await host.call('get_operation');const dispatches=['crash_awaiting','crash_installing'].includes(action)?1:0;const confirms=action==='crash_installing'?1:0;assert.equal(history(host).filter(row=>row.kind==='dispatch').length,dispatches);assert.equal(history(host).filter(row=>row.kind==='confirm').length,confirms);protect(host);
  const oldMount=host.frontendMounts;await host.unmount(false,action==='crash_uninstall');const killed=await host.call('__kill_core__');assert.equal(killed.returncode,-9);const rebooted=await host.restart();assert.notEqual(rebooted.boot,rebooted.previous_boot);await host.mount();const polls=host.frontendCalls.filter(method=>method==='get_operation').length;await host.eventually(()=>host.frontendCalls.filter(method=>method==='get_operation').length>=polls+2,'new frontend completed bootstrap and subsequent real controller polls');
  const after=await host.call('get_operation');assert.equal(after.id,before.id);assert.notEqual(after.outcome,'success');assert.notEqual(after.stage,'closed');if(action==='crash_ready')assert.equal(after.stage,'ready');if(action==='crash_uninstall'){assert.equal(after.stage,'unknown');assert.equal(after.uninstall_blocked,true);}if(action==='crash_awaiting')assert.equal(after.request_id,before.request_id);if(action==='crash_installing'){assert.equal(after.saw_start,true);assert.equal(after.saw_finish,false);}assert.ok(!host.visible().includes('Installation complete'));
  assert.equal(history(host).filter(row=>row.kind==='dispatch').length,dispatches);assert.equal(history(host).filter(row=>row.kind==='confirm').length,confirms);assert.equal(history(host).filter(row=>row.kind==='uninstall').length,0);assert.equal(history(host).filter(row=>row.kind==='cancel').length,0);assert.deepEqual(tree(join(host.directory,'home/plugins/example')),action==='crash_installing'?hashes.packages['2.0.0'].files:original);await workflowHttp(host);protect(host);
  if(action==='crash_uninstall'){const calls=host.frontendCallDetails.filter(value=>value.mount===oldMount).length;const pending=[...host.pending.values()].filter(value=>value.method==='__native_uninstall__').map(value=>value.promise);assert.equal(pending.length,1);await host.perform(async()=>{await host.call('__release_uninstall__');await Promise.all(pending);});assert.equal(host.frontendCallDetails.filter(value=>value.mount===oldMount).length,calls,'late native uninstall completion sent an old-instance RPC');assert.equal(existsSync(join(host.directory,'home/plugins/example')),false);assert.equal(history(host).filter(row=>row.kind==='uninstall').length,1);await host.unmount(false);const unresolved=await host.call('get_operation');assert.equal(unresolved.stage,'unknown');assert.notEqual(unresolved.outcome,'success');protect(host);}
  if(confirmation){await host.unmount(false);const calls=host.frontendCalls.length;await host.perform(async()=>{host.afterStartGate.release();await confirmation;});assert.equal(host.frontendCalls.length,calls,'late native finish after dismount sent an old-instance RPC');}
  return{id:spec.id,entry:'actual UI/native phase → SIGKILL → new core/frontend',interrupted:before.stage,recovered:after.stage,completionEvidence:false,nativeDispatches:dispatches,nativeConfirms:confirms,nativeUninstalls:spec.uninstalls,noReplay:true};
}
