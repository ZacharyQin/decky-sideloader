import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import http from 'node:http';
import {join} from 'node:path';
import {root,sha} from './harness.mjs';

export const diagnosticsInputs=JSON.parse(readFileSync(join(root,'e2e/fixtures/diagnostics.json'),'utf8'));
const protectedHashes=JSON.parse(readFileSync(join(root,'e2e/fixtures/install-hashes.json'),'utf8')).protected;
function pickup(value,method,addresses){
  const url=new URL(value);assert.equal(url.protocol,'http:');assert.ok(addresses.includes(url.hostname),'pickup URL must bind one of this fixture host’s actual private interfaces');assert.equal(url.username,'');assert.equal(url.password,'');assert.match(url.pathname,/^\/pickup\/[a-f0-9]{64}$/);
  return new Promise((resolve,reject)=>{
    const request=http.request(url,{method,agent:false},response=>{
      const chunks=[];let bytes=0;
      response.on('data',data=>{bytes+=data.length;if(bytes>6*1024*1024){response.destroy(Error('diagnostic response exceeds limit'));return;}chunks.push(data);});
      response.once('error',reject);response.once('end',()=>resolve({status:response.statusCode,headers:response.headers,body:Buffer.concat(chunks)}));
    });request.setTimeout(3000,()=>request.destroy(Error('bounded diagnostic HTTP timeout')));request.once('error',reject);request.end();
  });
}
export async function diagnosticUi(host,spec){
  const addresses=host.ready.fixture.addresses;assert.ok(Array.isArray(addresses)&&addresses.length>0);const download=(url,method)=>pickup(url,method,addresses);
  await host.mount();await host.click('settings');await host.click('diagnostics');await host.eventually(()=>host.actionEnabled('shareDiagnostics'),'real export/share action enabled for an actual private host address');await host.click('shareDiagnostics');
  const active=await host.eventually(async()=>{const value=await host.call('get_diagnostics_share');return value.state==='active'&&value;},'actual Rust export and share listener active');
  await host.eventually(()=>host.actionEnabled('copyDiagnostics'),'actual local QR and copy action rendered');assert.equal(host.screen.root.findAll(node=>node.type==='svg'&&node.props.shapeRendering==='crispEdges').length,1);assert.ok(host.visible().includes(active.url));
  await host.click('copyDiagnostics');await host.eventually(()=>host.clipboardWrites.length===1,'actual copy action used the external clipboard boundary');assert.deepEqual(host.clipboardWrites,[active.url]);
  const inspected=await host.call('__inspect_diagnostics__',active.package.path);assert.equal(inspected.versions.backend,diagnosticsInputs.version);assert.equal(inspected.versions.frontend,diagnosticsInputs.version);assert.equal(inspected.builds.matchesInstalled,true);assert.equal(inspected.builds.frontend.fingerprint,host.build.fingerprint);assert.equal(inspected.builds.installedBundle,host.build.bundle);assert.equal(inspected.builds.installedBinary,sha(readFileSync(join(host.directory,'home/plugins/decky-sideloader/bin/sideloader-core'))));assert.equal(inspected.privateMetadataAbsent,true);assert.ok(inspected.events.some(event=>event.event==='ui_action'&&event.action==='shareDiagnostics'));
  const head=await download(active.url,'HEAD');assert.equal(head.status,200);assert.equal(head.body.length,0);assert.equal(Number(head.headers['content-length']),active.package.bytes);assert.equal((await host.call('get_diagnostics_share')).downloads,0);
  const response=await download(active.url,'GET');assert.equal(response.status,200);assert.equal(response.body.length,active.package.bytes);assert.equal(sha(response.body),inspected.sha256);assert.equal(response.headers['content-type'],'application/zip');assert.equal(response.headers['cache-control'],'no-store');assert.equal(response.headers['x-content-type-options'],'nosniff');assert.ok(response.headers['content-disposition'].includes(inspected.file));await host.eventually(async()=>(await host.call('get_diagnostics_share')).downloads===1,'successful server transfer counted exactly once');
  await host.click('stopDiagnostics');await host.eventually(async()=>(await host.call('get_diagnostics_share')).state==='stopped','UI stop drained the real listener');await host.eventually(()=>!host.actionEnabled('copyDiagnostics')&&host.visible().includes('Download stopped'),'stopped UI removed the live link and QR');assert.equal(host.screen.root.findAll(node=>node.type==='svg'&&node.props.shapeRendering==='crispEdges').length,0);await assert.rejects(download(active.url,'GET'));
  assert.equal(host.frontendCalls.filter(method=>method==='start_diagnostics_share').length,1);assert.equal(host.frontendCalls.filter(method=>method==='stop_diagnostics_share').length,1);const exported=await host.call('get_diagnostics_status');assert.equal(exported.state,'success');assert.equal(exported.file,inspected.file);assert.equal(exported.bytes,inspected.bytes);const metadata=await host.call('__metadata__');assert.deepEqual(metadata.history,[]);assert.deepEqual(metadata.http.errors,[]);assert.deepEqual(metadata.http.mismatches,{});assert.equal(Object.keys(metadata.http.counts).length,0);
  for(const[name,expected]of Object.entries(protectedHashes))assert.equal(sha(readFileSync(join(host.directory,'home',name))),expected);
  return{id:spec.id,entry:'UI → actual export → QR/copy → HEAD/GET → stop',addressPolicy:'actual private host interface; cfg(test) discovery snapshot only',bytes:inspected.bytes,archiveSha256:inspected.sha256,members:inspected.members,successfulTransfers:1,nativeDispatches:0,privateMetadataAbsent:true};
}
