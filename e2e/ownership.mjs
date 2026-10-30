import assert from 'node:assert/strict';
import {existsSync,readFileSync} from 'node:fs';
import {join} from 'node:path';
import {root,sha} from './harness.mjs';

const protectedHashes=JSON.parse(readFileSync(join(root,'e2e/fixtures/install-hashes.json'),'utf8')).protected;
const versionHashes=JSON.parse(readFileSync(join(root,'e2e/fixtures/workflow-hashes.json'),'utf8')).packages;
async function plugin(host){return (await host.call('list_sideload_plugins')).plugins.find(row=>row.name==='Example');}
async function openRow(host){
  await host.eventually(()=>host.screen.root.findAll(node=>node.type==='Field'&&node.props.label==='Example'&&typeof node.props.onClick==='function').length>0,'current Example entry rendered');
  await host.perform(async()=>{host.screen.root.findAll(node=>node.type==='Field'&&node.props.label==='Example'&&typeof node.props.onClick==='function')[0].props.onClick();});
}
async function fill(host,value){await host.perform(async()=>{const field=host.screen.root.findAll(node=>node.type==='TextField'&&typeof node.props.onChange==='function')[0];assert.ok(field);field.props.onChange({target:{value}});});}
async function owned(host){return host.eventually(async()=>{const row=await plugin(host);return row.ownership.state==='owned'&&row.ownership.eligible&&row;},'ownership completed from real native frozen evidence');}
function history(host){const path=join(host.directory,'native-events.jsonl');return existsSync(path)?readFileSync(path,'utf8').trim().split('\n').filter(Boolean).map(row=>JSON.parse(row)):[];}
async function finish(host,spec,extra){
  const metadata=await host.call('__metadata__');const log=history(host);assert.deepEqual(metadata.history,log);assert.deepEqual(metadata.http.errors,[]);assert.deepEqual(metadata.http.mismatches,{});
  for(const[name,expected]of Object.entries(protectedHashes))assert.equal(sha(readFileSync(join(host.directory,'home',name))),expected);
  assert.deepEqual(log.filter(row=>row.kind==='dispatch').map(row=>row.args[4]),spec.native_types);
  assert.equal(log.filter(row=>row.kind==='confirm').length,spec.native_types.length);assert.equal(log.filter(row=>row.kind==='uninstall').length,0);
  assert.equal(await host.call('get_operation')===null,spec.native_types.length===0);
  return{id:spec.id,...extra,nativeTypes:spec.native_types,httpRequests:Object.values(metadata.http.counts).reduce((a,b)=>a+b,0)};
}

export async function ownershipWorkflow(host,spec){
  assert.equal(await host.call('get_operation'),null);await host.mount();await host.click('settings');await host.click('refreshRecognition');
  await host.eventually(async()=>(await host.call('__workflow_metadata__')).store_reads===1&&host.actionEnabled('refreshRecognition'),'actual store catalog and native versions refresh completed');
  const store=await plugin(host);assert.equal(store.loader_managed,true);assert.equal(store.ownership.eligible,false);
  await assert.rejects(host.call('prepare_install','fixture/example','v2.0.0','https://github.com/fixture/example/releases/download/v2.0.0/plugin.zip','Example',null,'install'),/归属|商店|接管/);
  await host.click('takeover');await openRow(host);await host.click('takeover');await host.click('manualRepo');await fill(host,'fixture/example');await host.click('saveBinding');
  if(['write_failure','cross_instance'].includes(spec.ownership)){
    await host.eventually(async()=>{const value=await host.call('__workflow_metadata__');return value.frozen_attempts===1&&(spec.ownership==='cross_instance'?value.frozen_pending:host.actionEnabled('readOwnership'));},'first external freeze write reached configured failure/gate');
    await host.unmount(false);const restarted=await host.restart();assert.notEqual(restarted.boot,restarted.previous_boot);await host.mount();await openRow(host);
    assert.equal((await plugin(host)).ownership.eligible,false);assert.equal(host.actionEnabled('replace'),false);
    if(spec.ownership==='cross_instance'){
      await host.call('__release_frozen_write__');
      await host.eventually(()=>![...host.pending.values()].some(item=>item.method==='__frozen_update__'),'old instance native write actually settled');
      const pending=await plugin(host);assert.equal(pending.ownership.state,'pending','late native write cannot activate from disposed frontend');
      assert.equal((await host.call('__workflow_metadata__')).frozen_attempts,1,'restart did not replay array write');
      await host.click('readOwnership');
    }else{
      await host.click('readOwnership');
      await host.eventually(()=>host.actionEnabled('takeover'),'unmatched frozen read remains pending');assert.equal((await plugin(host)).ownership.eligible,false);
      await host.click('takeover');
    }
  }
  const active=await owned(host);assert.equal(active.repo,'fixture/example');
  await host.eventually(()=>host.actionEnabled('refreshReleases'),'owned release snapshot loaded');
  let finalState='owned';
  if(spec.ownership==='return'){
    host.prompt=null;await host.click('replace');await host.eventually(()=>host.prompt?.name==='Example','owned plugin update dispatched');const offset=host.installEventAcks.length;
    await host.perform(async()=>host.loader.call('utilities/confirm_plugin_install',host.prompt.request_id));await host.awaitConfirmationRecorded(offset);const operation=await host.operation('closed');assert.equal(operation.outcome,'success');assert.equal(operation.backup.files,2);
    for(const[name,expected]of Object.entries(versionHashes['2.0.0'].files))assert.equal(sha(readFileSync(join(host.directory,'home/plugins/example',name))),expected);
    await host.click('back');await host.eventually(()=>host.actionEnabled('refreshReleases'),'new identity release cache refreshed before returning ownership');await host.click('maintenance');await host.click('returnStore');
    await host.eventually(async()=>{const row=await plugin(host);return row.ownership.intent?.state==='released'&&!row.ownership.eligible;},'store return confirmed through real native write and backend ledger');
    const returned=await plugin(host);assert.equal(returned.ownership.state,'store');finalState=returned.ownership.state;
    assert.equal(host.actionEnabled('replace'),false);await assert.rejects(host.call('get_plugin_releases','fixture/example','2.0.0','Example','example',false),/归属|商店|接管/);
    const saved=JSON.parse(readFileSync(join(host.directory,'home/settings/decky-sideloader/settings.json'),'utf8'));assert.equal(saved.bindings.Example.repo,'fixture/example','return preserves user binding while removing permission');
  }else if(spec.ownership==='identity'){
    await host.call('__change_identity__');
    for(let step=0;step<4&&!host.actionEnabled('settings');step++)await host.click('back');
    assert.ok(host.actionEnabled('settings'),'returned through store/settings to home');
    await openRow(host);const changed=await plugin(host);assert.equal(changed.memory_changed,true);assert.equal(changed.ownership.eligible,false);assert.equal(host.actionEnabled('replace'),false);
    await assert.rejects(host.call('get_plugin_releases','fixture/example','1.0.0','Example','example',false),/身份|归属|变化|确认/);finalState=changed.ownership.state;
  }
  const loader=JSON.parse(readFileSync(join(host.directory,'home/settings/loader.json'),'utf8'));assert.equal(loader.fixtureOtherSetting,'preserve-me');assert.ok(loader.frozenPlugins.includes('Other Fixture'));
  const expectedFrozen=spec.ownership==='return'?Boolean(spec.prior_frozen):true;assert.equal(loader.frozenPlugins.includes('Example'),expectedFrozen);
  const metadata=await host.call('__workflow_metadata__');assert.equal(metadata.frozen_attempts,spec.ownership==='return'||spec.ownership==='write_failure'?2:1);
  assert.equal(metadata.store_reads,1);assert.ok(host.frontendCalls.includes('begin_store_ownership'));assert.ok(host.frontendCalls.includes('ownership_native_step'));
  return finish(host,spec,{ownership:finalState,priorFrozen:Boolean(spec.prior_frozen),nativeFreezeWrites:metadata.frozen_attempts,crossInstance:spec.ownership==='cross_instance'});
}

export async function bindingMapWorkflow(host,spec){
  await host.mount();await openRow(host);await host.eventually(()=>host.actionEnabled('replace'),'initial package repository release loaded');await host.click('maintenance');await host.click('manageBinding');await host.click('manualRepo');await fill(host,'fixture/fork');await host.click('saveBinding');
  await host.eventually(async()=>{const row=await plugin(host);return row.repo==='fixture/fork'&&row.repo_source==='user'&&host.visible().includes('No published releases');},'manual repository and empty fork release snapshot applied');
  await host.click('back');await host.click('settings');await host.eventually(()=>host.actionEnabled('updateRepositoryMap'),'map action enabled after list refresh');await host.click('updateRepositoryMap');
  await host.eventually(()=>host.visible().includes('Repository map updated')&&host.frontendCalls.includes('update_repository_map'),'map update completed');
  let row=await plugin(host);assert.equal(row.repo,'fixture/fork','mapping update must preserve explicit binding');assert.equal(row.ownership.eligible,true);
  await host.unmount(false);await host.restart();await host.mount();await openRow(host);await host.eventually(()=>host.visible().includes('No published releases'),'persisted fork snapshot reused after process restart');row=await plugin(host);assert.equal(row.repo,'fixture/fork');
  await host.click('maintenance');await host.click('manageBinding');await host.click('unlink');await host.eventually(async()=>{const current=await plugin(host);return current.repo===null&&current.repo_source==='disabled';},'explicit unbinding overrides both package metadata and updated map');
  await assert.rejects(host.call('get_plugin_releases','fixture/fork','1.0.0','Example','example',false),/信息|变化|关联|仓库/);assert.equal(host.actionEnabled('replace'),false);
  assert.ok(host.frontendCalls.includes('set_binding'));assert.ok(host.frontendCalls.includes('clear_binding'));assert.ok(host.frontendCalls.includes('update_repository_map'));
  return finish(host,spec,{binding:'disabled',mapPreservedUserBinding:true,restartCache:true});
}
