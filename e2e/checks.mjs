import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {join} from 'node:path';
import {Host,root,sha,text} from './harness.mjs';

export const checksInputs=JSON.parse(readFileSync(join(root,'e2e/fixtures/checks.json'),'utf8'));
const protectedHashes=JSON.parse(readFileSync(join(root,'e2e/fixtures/install-hashes.json'),'utf8')).protected;
const settings=host=>JSON.parse(readFileSync(join(host.directory,'home/settings/decky-sideloader/settings.json'),'utf8'));
function report(host,source,empty=false){
  const saved=settings(host);assert.equal(saved.deletePackageAfterInstall,false);assert.equal(saved.downloadTimeoutMinutes,10);const value=saved.lastCheck;assert.equal(value?.source,source);assert.equal(value.error,undefined);assert.ok(Number.isFinite(Date.parse(value.checkedAt)));assert.equal(value.items.length,empty?0:2);
  const actual=value.items.map(item=>Object.fromEntries(['name','repo','current','latest','status'].map(key=>[key,item[key]]))).sort((a,b)=>a.name.localeCompare(b.name));assert.deepEqual(actual,empty?[]:checksInputs.expected);
  if(!empty)assert.match(value.items.find(item=>item.name==='Other Fixture').error,/HTTP 503/);
  if(source==='scheduled')assert.deepEqual(saved.lastScheduledCheck,value);else assert.equal(saved.lastScheduledCheck??null,null);
  return value;
}
async function verify(host,source,empty=false){
  const value=report(host,source,empty);const metadata=await host.call('__metadata__');assert.deepEqual(metadata.http.errors,[]);assert.deepEqual(metadata.http.mismatches,{});assert.deepEqual(metadata.history,[]);assert.equal(await host.call('get_operation'),null);
  for(const[url,count]of Object.entries(metadata.http.required))assert.equal(metadata.http.counts[url]??0,count);
  for(const[name,expected]of Object.entries(protectedHashes))assert.equal(sha(readFileSync(join(host.directory,'home',name))),expected);
  assert.equal(readFileSync(join(host.directory,'home/plugins/example/main.py'),'utf8'),'# original fixture example\n');assert.equal(readFileSync(join(host.directory,'home/plugins/other/main.py'),'utf8'),'# original other fixture\n');
  assert.ok(host.nativeCalls.every(call=>['loader/get_plugins','utilities/settings/get'].includes(call.route)));return value;
}
async function toggle(host,label,value){
  await host.eventually(()=>host.screen.root.findAll(node=>node.type==='ToggleField'&&node.props.label===label&&!node.props.disabled).length===1,'real setting toggle enabled');
  await host.perform(async()=>host.screen.root.findAll(node=>node.type==='ToggleField'&&node.props.label===label)[0].props.onChange(value));
}
async function enable(host,muted=false){
  await host.mount();await host.click('settings');
  if(muted){await toggle(host,'Scheduled check notifications',false);await host.eventually(()=>settings(host).checkNotifications===false,'notification policy persisted through UI');}
  await toggle(host,'Scheduled update checks',true);await host.eventually(()=>settings(host).autoCheck===true,'scheduled checking enabled through UI');
  assert.equal(host.backendEvents.get('scheduled_check_completed')?.size,1);
}
async function rows(host){
  await host.eventually(()=>{const row=host.screen.root.findAll(node=>node.type==='Field'&&node.props.label==='Example')[0];return row&&text(row).includes('2.0.0');},'real app displays verified update');
  const other=host.screen.root.findAll(node=>node.type==='Field'&&node.props.label==='Other Fixture')[0];assert.ok(text(other).includes('Unable to check for updates'));
}
export async function checks(host,spec,fixtureCore){
  if(spec.action==='manual'){
    await host.mount();assert.equal(settings(host).lastCheck??null,null);await host.click('checkUpdates');await rows(host);const saved=await verify(host,'manual');assert.equal(host.frontendCalls.filter(name=>name==='check_updates').length,1);assert.equal(host.toasts.length,0);assert.equal(host.observedEvents.filter(name=>name==='scheduled_check_completed').length,0);
    return{id:spec.id,source:saved.source,plugins:2,updates:1,errors:1,nativeDispatches:0};
  }
  const extra=[];let output,failure;
  try{
    await host.mount();await host.unmount();await enable(host);
    const detached=await Host.start(spec.detached_case,fixtureCore);extra.push(detached);await enable(detached);await detached.unmount();
    const muted=await Host.start(spec.id,fixtureCore);extra.push(muted);await enable(muted,true);
    for(const profile of [host,detached,muted])await profile.eventually(()=>profile.observedEventDetails.filter(event=>event.name==='scheduled_check_completed').length===1,'actual unaccelerated Rust 60-second scheduler event',90000);
    for(const profile of [host,detached,muted]){
      const saved=await verify(profile,'scheduled',profile===detached);const event=profile.observedEventDetails.find(event=>event.name==='scheduled_check_completed');assert.deepEqual(event.args[0].report,saved);assert.equal(profile.frontendCalls.filter(name=>name==='check_updates').length,0,'scheduler is not triggered by a test RPC');
    }
    assert.deepEqual(host.toasts.map(value=>({title:value.title,body:value.body})),[{title:'Sideloader',body:checksInputs.toast}]);assert.equal(detached.toasts.length,0,'actual event after unmount has no subscriber');assert.equal(detached.backendEvents.size,0);assert.equal(muted.toasts.length,0,'actual event respects disabled notification policy');assert.equal(muted.backendEvents.get('scheduled_check_completed').size,1);assert.equal(muted.observedEventDetails.find(event=>event.name==='scheduled_check_completed').args[0].checkNotifications,false);
    await host.click('back');await rows(host);await host.click('settings');await host.eventually(()=>host.actionEnabled('checkResults'),'scheduled result entry enabled');await host.click('checkResults');await host.eventually(()=>host.visible().includes('Scheduled check results')&&host.visible().includes('Other Fixture'),'saved two-item scheduled report page rendered');assert.equal(host.toasts.length,1);
    output={id:spec.id,source:'scheduled',clock:'actual Rust 60-second interval; no accelerated backend clock',profiles:['remounted notification once','unmounted before event: no toast','mounted notifications disabled: no toast'],pluginsByRole:{remounted:2,unmounted:0,muted:2},updatesPerMountedProfile:1,errorsPerMountedProfile:1,unmountedPolicy:'no native responder: no plugin eligibility or repository queries',nativeDispatches:0};
  }catch(error){failure=error;}
  for(const profile of extra){try{await profile.close(!failure);}catch(error){failure??=error;}}
  if(failure)throw failure;return output;
}
