import assert from 'node:assert/strict';
import {existsSync,readFileSync,readdirSync} from 'node:fs';
import {join,relative,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {root,sha} from './harness.mjs';

export const discoveryInputs=JSON.parse(readFileSync(join(root,'e2e/fixtures/discovery.json'),'utf8'));
const hashes=JSON.parse(readFileSync(join(root,'e2e/fixtures/discovery-hashes.json'),'utf8'));
const protectedHashes=JSON.parse(readFileSync(join(root,'e2e/fixtures/install-hashes.json'),'utf8')).protected;
const asset='https://github.com/fixture/monorepo/releases/download/v3.2.1/beta.zip';
function tree(directory){if(!existsSync(directory))return{};const values={};const visit=path=>{for(const entry of readdirSync(path,{withFileTypes:true})){const file=join(path,entry.name);if(entry.isDirectory())visit(file);else{assert.equal(entry.isFile(),true);values[relative(directory,file)]=sha(readFileSync(file));}}};visit(directory);return values;}
function history(host){const path=join(host.directory,'native-events.jsonl');return existsSync(path)?readFileSync(path,'utf8').trim().split('\n').filter(Boolean).map(row=>JSON.parse(row)):[];}
async function choose(host,data){
  await host.eventually(()=>host.screen.root.findAll(node=>node.type==='DropdownItem'&&node.props.rgOptions?.some(row=>row.data===data)).length>0,'real selector options rendered');
  await host.perform(async()=>{const field=host.screen.root.findAll(node=>node.type==='DropdownItem'&&node.props.rgOptions?.some(row=>row.data===data))[0];assert.ok(!field.props.disabled);field.props.onChange({data});});
}
async function preview(host,spec){
  await host.click('newInstall');await host.perform(async()=>{const field=host.screen.root.findAll(node=>node.type==='TextField'&&typeof node.props.onChange==='function')[0];assert.ok(field);field.props.onChange({target:{value:discoveryInputs.query}});});
  await host.click('search');await host.eventually(()=>host.screen.root.findAll(node=>node.type==='Field'&&node.props.label===discoveryInputs.repo).length>0,'actual keyword search result rendered');
  await host.perform(async()=>{host.screen.root.findAll(node=>node.type==='Field'&&node.props.label===discoveryInputs.repo)[0].props.onClick();});
  if(spec.action==='truncated'){
    await host.eventually(()=>/目录过大|too large|complete.*probe/i.test(host.visible()),'truncated repository rejected without partial projects');assert.equal(host.actionEnabled('readReleases'),false);return false;
  }
  await host.eventually(()=>host.screen.root.findAll(node=>node.type==='DropdownItem'&&node.props.rgOptions?.some(row=>row.data===discoveryInputs.plugin_path)).length>0,'two repository plugins require an explicit selection');
  assert.equal(host.actionEnabled('readReleases'),false,'monorepo cannot silently select the first plugin');
  await choose(host,discoveryInputs.plugin_path);await host.click('readReleases');
  if(spec.action==='pagination_failure'){
    await host.eventually(()=>host.visible().includes('HTTP 503'),'release pagination failure surfaced instead of partial release list');assert.equal(host.actionEnabled('installPackage'),false);return false;
  }
  await host.eventually(()=>host.screen.root.findAll(node=>node.type==='DropdownItem'&&node.props.rgOptions?.some(row=>row.data===asset)).length>0,'different alpha/beta ZIP assets rendered');
  assert.equal(host.actionEnabled('installPackage'),false,'multiple ZIP assets require an explicit selection');
  return true;
}
export async function discovery(host,spec){
  assert.equal(await host.call('get_operation'),null);await host.mount();let ready=await preview(host,spec);
  if(spec.action==='restart'){
    assert.equal(history(host).length,0);await host.unmount(false);const restarted=await host.restart();assert.notEqual(restarted.boot,restarted.previous_boot);await host.mount();ready=await preview(host,spec);
  }
  let operation=null;
  if(ready){
    await choose(host,asset);await host.eventually(()=>host.actionEnabled('installPackage'),'selected Beta ZIP enabled installation');await host.click('installPackage');
    if(spec.action==='crash_dependency'){
      const url=discoveryInputs.dependencies[1].url;
      const interrupted=await host.eventually(async()=>{const value=await host.call('get_operation');return value?.dependency_download?.index===2&&value.dependency_download.received===4&&value;},'second remote dependency actually received a partial body');assert.equal(interrupted.stage,'checking');assert.equal(interrupted.download.status,'complete');
      await host.unmount(false);assert.equal((await host.call('__kill_core__')).returncode,-9);await host.eventually(async()=>{const report=(await host.call('__metadata__')).http;assert.deepEqual(report.errors,[]);return report.aborted.length===1&&report.aborted[0]===url&&report.active_clients===0;},'crashed dependency request closed without a replay');const restarted=await host.restart();assert.notEqual(restarted.boot,restarted.previous_boot);await host.mount();const failed=await host.operation('failed');assert.equal(failed.id,interrupted.id);assert.equal(failed.download.status,'complete');assert.equal(failed.dependency_download.status,'failed','interrupted dependency must not remain rendered as downloading');assert.equal(sha(readFileSync(fileURLToPath(failed.artifact))),hashes.packages.beta.archive);assert.equal(existsSync(join(host.directory,'home/plugins/beta-fixture')),false);assert.equal(history(host).length,0);
      await host.eventually(()=>host.actionEnabled('viewTask')||host.actionEnabled('continueInstall'),'interrupted dependency preparation surfaced');if(!host.actionEnabled('continueInstall'))await host.click('viewTask');await host.eventually(()=>host.actionEnabled('continueInstall'),'explicit retry of dependencies with retained main archive');await host.click('continueInstall');await host.operation('ready');await host.eventually(()=>host.actionEnabled('confirmInstall'),'new frontend requires explicit native confirmation');assert.equal(history(host).length,0);await host.click('confirmInstall');
    }
    if(spec.dispatches){
      await host.eventually(()=>host.prompt?.name==='Beta Fixture','merged dependency package dispatched for the selected plugin');const offset=host.installEventAcks.length;
      await host.perform(async()=>host.loader.call('utilities/confirm_plugin_install',host.prompt.request_id));await host.awaitConfirmationRecorded(offset);operation=await host.operation('closed');assert.equal(operation.outcome,'success');assert.equal(operation.package.name,'Beta Fixture');assert.equal(operation.package.folder,'beta-fixture');assert.equal(operation.package.version,'3.2.1');assert.equal(operation.dependencies.count,2);assert.deepEqual(readdirSync(operation.directory).filter(name=>name.startsWith('.remote-binaries-')),[],'interrupted owned dependency work was not reclaimed');
      assert.deepEqual(tree(join(host.directory,'home/plugins/beta-fixture')),hashes.packages.beta.merged_files);assert.equal(existsSync(join(host.directory,'home/plugins/alpha-fixture')),false);
      const settings=JSON.parse(readFileSync(join(host.directory,'home/settings/decky-sideloader/settings.json'),'utf8'));assert.equal(settings.bindings['Beta Fixture'].repo,discoveryInputs.repo);
      await host.eventually(()=>host.visible().includes('Installation complete'),'merged monorepo result rendered');
    }else{
      operation=await host.operation('failed');assert.equal(operation.download.status,'complete');assert.equal(operation.dependency_download.name,'lib-b');assert.equal(operation.dependency_download.index,2);assert.equal(operation.dependency_download.status,'failed','SHA rejection must not render the second dependency as complete');
      const path=fileURLToPath(operation.artifact);assert.equal(sha(readFileSync(path)),hashes.packages.beta.archive,'failed dependency did not replace original downloaded ZIP');assert.equal(existsSync(join(dirname(path),'source.zip')),false);assert.equal(existsSync(join(host.directory,'home/plugins/beta-fixture')),false);
    }
  }else assert.equal(await host.call('get_operation'),null);
  const metadata=await host.call('__metadata__');const log=history(host);assert.deepEqual(metadata.history,log);assert.equal(log.filter(row=>row.kind==='dispatch').length,spec.dispatches);assert.equal(log.filter(row=>row.kind==='confirm').length,spec.dispatches);assert.equal(log.filter(row=>row.kind==='cancel').length,0);assert.deepEqual(metadata.http.errors,[]);assert.deepEqual(metadata.http.mismatches,{});
  for(const[url,count]of Object.entries(metadata.http.required))assert.equal(metadata.http.counts[url]??0,count);
  for(const[name,expected]of Object.entries(protectedHashes))assert.equal(sha(readFileSync(join(host.directory,'home',name))),expected);
  assert.ok(host.frontendCalls.includes('search_repositories'));assert.ok(host.frontendCalls.includes('probe_repository'));
  return{id:spec.id,entry:'keyword search → explicit monorepo plugin → explicit ZIP asset',stage:operation?.stage??null,nativeDispatches:spec.dispatches,remoteDependencies:ready?2:0,sourceArchive:hashes.packages.beta.archive,httpRequests:Object.values(metadata.http.counts).reduce((a,b)=>a+b,0)};
}
