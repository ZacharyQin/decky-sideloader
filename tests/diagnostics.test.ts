import { expect, test } from "bun:test";
import { spawnSync } from "node:child_process";

function isolated(script: string) {
  const run = spawnSync(process.execPath, ["-e", script], { cwd: process.cwd(), encoding: "utf8" });
  expect(run.stderr).toBe("");
  expect(run.status).toBe(0);
}

test("diagnosticsController bounds queue/batches/drop counts, discards failed batches and cleans every timer", () => {
  isolated(`
    import { mock } from 'bun:test';
    import { strict as assert } from 'node:assert';
    mock.module('react', () => ({ useSyncExternalStore: (_subscribe, read) => read() }));
    const jobs = new Map(); let tick = 0;
    globalThis.setTimeout = (fn) => { jobs.set(++tick, fn); return tick; };
    globalThis.clearTimeout = (id) => jobs.delete(id);
    const flush = async () => { for (let i=0;i<10;i++) await Promise.resolve(); };
    const next = async () => { const [id, fn] = jobs.entries().next().value; jobs.delete(id); fn(); await flush(); };
    const controller = await import('./src/diagnosticsController.ts');
    const sends = []; let release; let exports = 0;
    const stop = controller.startDiagnostics({
      status: async () => ({ state:'idle' }),
      export: async () => { exports++; return { state:'idle' }; },
      record: async (events, dropped) => { sends.push({events, dropped}); if (sends.length === 1) await new Promise(r => release=r); else if (sends.length === 2) throw new Error('failure'); return { accepted:events.length }; },
    });
    await flush();
    const event = { event:'loader_read', ok:true, count:25, durationMs:1 };
    for (let i=0;i<100000;i++) controller.recordDiagnostic({...event, secret:'SYNTHETIC_TOKEN'});
    assert.equal(jobs.size,1); await next(); assert.equal(sends[0].events.length,16); assert.equal(sends[0].dropped,65535);
    for (let i=0;i<100;i++) controller.recordDiagnostic(event);
    assert.equal(jobs.size,0); release(); await flush();
    while(jobs.size) await next();
    assert.equal(sends.length,3); // 16 in-flight, at most 32 pending, no retry of failed batch.
    assert.ok(sends.every(s => s.events.length <= 16 && s.dropped <= 65535 && new TextEncoder().encode(JSON.stringify(s)).byteLength <= 8192));
    assert.ok(!JSON.stringify(sends).includes('SYNTHETIC_TOKEN'));
    assert.equal(exports,0);
    controller.recordDiagnostic(event); assert.equal(jobs.size,1); stop(); assert.equal(jobs.size,0);
    controller.recordDiagnostic(event); assert.equal(jobs.size,0);
    const stopStatus = controller.startDiagnostics({ record:async()=>({accepted:0}), export:async()=>{exports++;return {state:'idle'}}, status:async()=>({state:'exporting'}) });
    await flush(); assert.equal(jobs.size,1); stopStatus(); assert.equal(jobs.size,0); assert.equal(exports,0);
  `);
});

test("diagnostic API rejects malformed DTOs, oversized fields and invalid replies before use", () => {
  isolated(`
    import { mock } from 'bun:test';
    import { strict as assert } from 'node:assert';
    let reply = {state:'idle'}; const calls=[];
    mock.module('@decky/api', () => ({callable: name => async (...args) => { calls.push({name,args}); return reply; }}));
    const api = await import('./src/api.ts');
    assert.deepEqual(await api.getDiagnosticsStatus(),{state:'idle'});
    const file='diagnostics-1790000000000000000-'+'a'.repeat(32)+'.zip';
    reply={state:'success',file,path:'/synthetic/Downloads/decky-sideloader-diagnostics/'+file,bytes:100};
    assert.equal((await api.exportDiagnostics()).bytes,100);
    for (const bad of [null, [], {}, {state:'idle',extra:1}, {...reply,bytes:6291457}, {...reply,path:'/root/elsewhere/'+file}, {...reply,path:'/x/../Downloads/decky-sideloader-diagnostics/'+file}, {state:'error',error:'中文'.repeat(1000)}]) {
      reply=bad; await assert.rejects(api.getDiagnosticsStatus());
    }
    const event={event:'loader_read',ok:true,count:25,durationMs:1};
    const before=calls.length;
    for(const bad of [Array(17).fill(event),[{...event,token:'SYNTHETIC_TOKEN'}],[{...event,count:NaN}],[{...event,durationMs:'中文'.repeat(10000)}]]) await assert.rejects(api.recordDiagnosticEvents(bad,0));
    assert.equal(calls.length,before);
    reply={accepted:1}; assert.deepEqual(await api.recordDiagnosticEvents([event],0),reply);
    for(const bad of [{accepted:2},{accepted:1,extra:true},[],null]) { reply=bad; await assert.rejects(api.recordDiagnosticEvents([event],0)); }
    assert.ok(calls.every(c=>['get_diagnostics_status','export_diagnostics','record_diagnostic_events'].includes(c.name)));
  `);
});

test("diagnostic status startup races cannot overwrite completed export intent or replay an export", () => {
  isolated(`
    import {mock} from 'bun:test'; import {strict as assert} from 'node:assert';
    mock.module('react',()=>({useSyncExternalStore:(_subscribe,read)=>read()}));
    const c=await import('./src/diagnosticsController.ts');
    let finishStatus,finishExport;let calls=0;
    const stop=c.startDiagnostics({record:async()=>({accepted:0}),status:()=>new Promise(r=>finishStatus=r),export:()=>{calls++;return new Promise(r=>finishExport=r)}});
    const first=c.requestDiagnosticsExport();const second=c.requestDiagnosticsExport(); assert.equal(first,second);assert.equal(calls,1);
    finishExport({state:'error',error:'export failed'}); await first;
    finishStatus({state:'idle'});for(let i=0;i<10;i++)await Promise.resolve();
    assert.equal(c.useDiagnostics().state,'error');stop();
  `);
});

test("pickup API validates RFC1918, strict DTO, bounded credentials and session/address correlation", () => {
  isolated(`
    import {mock} from 'bun:test'; import {strict as assert} from 'node:assert';
    let reply;const calls=[];
    mock.module('@decky/api',()=>({callable:name=>async(...args)=>{calls.push({name,args});return reply}}));
    const a=await import('./src/api.ts');const id='a'.repeat(32);
    reply={addresses:['10.1.2.3','172.16.1.2','192.168.2.5']};assert.equal((await a.getDiagnosticsAddresses()).addresses.length,3);
    for(const addresses of [['127.0.0.1'],['0.0.0.0'],['8.8.8.8'],['172.32.0.1'],['192.168.01.1'],['::1'],['10.0.0.1','10.0.0.1'],Array(129).fill('10.0.0.1')]) {reply={addresses};await assert.rejects(a.getDiagnosticsAddresses());}
    const file='diagnostics-1790000000000000000-'+'a'.repeat(32)+'.zip';
    const good={state:'active',session:id,package:{state:'success',file,path:'/synthetic/Downloads/decky-sideloader-diagnostics/'+file,bytes:1234},url:'http://192.168.2.5:12345/pickup/'+'b'.repeat(64),remaining:300,downloads:0};
    reply=good;assert.deepEqual(await a.startDiagnosticsShare('192.168.2.5',id,null),good);
    for(const bad of [null,[],{state:'idle',url:good.url},{...good,extra:true},{...good,session:'wrong'},{...good,remaining:301},{...good,downloads:4},{...good,package:{...good.package,bytes:6291457}},...['https://192.168.2.5:1/pickup/','http://127.0.0.1:1/pickup/','http://192.168.2.5:0001/pickup/','http://192.168.2.5:65536/pickup/'].map(url=>({...good,url:url+'b'.repeat(64)})),{...good,url:good.url+'?extra=1'}]) {reply=bad;await assert.rejects(a.getDiagnosticsShare());}
    reply=good;await assert.rejects(a.startDiagnosticsShare('10.0.0.1',id,null));await assert.rejects(a.startDiagnosticsShare('192.168.2.5','c'.repeat(32),null));
    const count=calls.length;
    for(const args of [['127.0.0.1',id,null],['192.168.2.5',[],null],['192.168.2.5',id,'bad']])await assert.rejects(a.startDiagnosticsShare(...args));
    assert.equal(calls.length,count);
    await assert.rejects(a.stopDiagnosticsShare(id));
    reply={state:'stopped',session:id,package:good.package};assert.equal((await a.stopDiagnosticsShare(id)).state,'stopped');
    reply={state:'error',session:id,error:'retry'};assert.equal((await a.getDiagnosticsShare()).error,'retry');
  `);
});

test("pickup controller ignores late startup, supersedes preparation with stop, clears credentials/timers on unload and never replays", () => {
  isolated(`
    import {mock} from 'bun:test';import {strict as assert} from 'node:assert';
    mock.module('react',()=>({useSyncExternalStore:(_subscribe,read)=>read()}));
    const jobs=new Map();let tick=0;
    globalThis.setTimeout=fn=>{jobs.set(++tick,fn);return tick};globalThis.clearTimeout=id=>jobs.delete(id);
    const flush=async()=>{for(let i=0;i<20;i++)await Promise.resolve()};
    const c=await import('./src/diagnosticsController.ts');let finishInitial,finishStart;let starts=0,stops=0,reads=0;
    const protocol={record:async()=>({accepted:0}),status:async()=>({state:'idle'}),export:async()=>{throw Error('must not export')},sharing:{addresses:async()=>({addresses:['192.168.2.5']}),status:()=>++reads===1?new Promise(r=>finishInitial=r):Promise.resolve({state:'idle'}),start:(address,session,previous)=>{starts++;assert.equal(previous,null);return new Promise(r=>finishStart=()=>r({state:'active',session,url:'http://synthetic-revoked',remaining:300,downloads:0}))},stop:async session=>{stops++;return {state:'stopped',session}}}};
    const unload=c.startDiagnostics(protocol);await flush();
    await c.refreshDiagnosticShare();assert.equal(c.useDiagnosticShare().loading,false);
    const first=c.requestDiagnosticShare('192.168.2.5');assert.equal(c.requestDiagnosticShare('192.168.2.5'),first);assert.equal(starts,1);
    finishInitial({state:'idle'});await flush();assert.equal(c.useDiagnosticShare().status.state,'preparing');
    await c.stopDiagnosticShare();assert.equal(c.useDiagnosticShare().status.state,'stopped');
    finishStart();await first;assert.equal(c.useDiagnosticShare().status.state,'stopped');assert.equal(c.useDiagnosticShare().status.url,undefined);assert.equal(jobs.size,0);
    unload();assert.equal(jobs.size,0);assert.equal(c.useDiagnosticShare().status.session,undefined);
    const again=c.startDiagnostics({...protocol,sharing:{...protocol.sharing,status:async()=>({state:'idle'})}});await flush();assert.equal(starts,1);again();
  `);
});

test("pickup controller polls only status, and a stale poll cannot revive a stopped link", () => {
  isolated(`
    import {mock} from 'bun:test';import {strict as assert} from 'node:assert';
    mock.module('react',()=>({useSyncExternalStore:(_subscribe,read)=>read()}));
    const jobs=new Map();let tick=0;globalThis.setTimeout=fn=>{jobs.set(++tick,fn);return tick};globalThis.clearTimeout=id=>jobs.delete(id);
    const flush=async()=>{for(let i=0;i<20;i++)await Promise.resolve()};
    const c=await import('./src/diagnosticsController.ts');let starts=0,reads=0,finishPoll;const id='a'.repeat(32);
    const active={state:'active',session:id,url:'http://synthetic-revoked',remaining:300,downloads:0};
    const unload=c.startDiagnostics({record:async()=>({accepted:0}),status:async()=>({state:'idle'}),export:async()=>({state:'idle'}),sharing:{addresses:async()=>({addresses:['192.168.2.5']}),status:()=>++reads===1?Promise.resolve(active):new Promise(r=>finishPoll=r),start:async()=>{starts++;return active},stop:async session=>({state:'stopped',session})}});
    await flush();assert.equal(starts,0);assert.equal(jobs.size,1);
    const [key,fn]=jobs.entries().next().value;jobs.delete(key);fn();await flush();
    await c.stopDiagnosticShare();finishPoll(active);await flush();assert.equal(c.useDiagnosticShare().status.state,'stopped');assert.equal(c.useDiagnosticShare().status.url,undefined);assert.equal(jobs.size,0);unload();
  `);
});

test("frontend diagnostics keep page/action/error evidence without messages, URLs or other plugins' errors", () => {
  isolated(`
    import {strict as assert} from 'node:assert';
    const target=new EventTarget();globalThis.window=target;
    const c=await import('./src/diagnosticsController.ts');
    const sent=[];let exported;
    const stop=c.startDiagnostics({status:async()=>({state:'idle'}),record:async(events)=>{sent.push(...events);return {accepted:events.length}},export:async()=>{exported=[...sent];return {state:'idle'}}});
    await c.recordDiagnosticView('details','SYNTHETIC_TOKEN','SYNTHETIC_PRIVATE_RELEASE');
    c.recordUIAction('refreshReleases');
    const own=new TypeError('Bearer SYNTHETIC_TOKEN');own.stack='TypeError: private\\n at run (https://steamloopback.host/plugins/decky-sideloader/dist/index.js?token=SYNTHETIC_TOKEN:42:7)';
    const other=new Error('SYNTHETIC_TOKEN');other.stack='Error: private\\n at run (https://steamloopback.host/plugins/another/dist/index.js:5:2)';
    for(const error of [own,other]){const event=new Event('error');Object.defineProperty(event,'error',{value:error});window.dispatchEvent(event)}
    await c.requestDiagnosticsExport();
    assert.equal(exported.find(e=>e.event==='ui_page').page,'details');
    assert.match(exported.find(e=>e.event==='ui_page').pluginId,/^[a-f0-9]{32}$/);
    assert.equal(exported.find(e=>e.event==='ui_action').action,'refreshReleases');
    const errors=exported.filter(e=>e.event==='frontend_error');assert.equal(errors.length,1);assert.equal(errors[0].errorType,'TypeError');assert.equal(errors[0].line,42);assert.equal(errors[0].column,7);
    assert.ok(!JSON.stringify(exported).includes('SYNTHETIC'));assert.ok(!JSON.stringify(exported).includes('https://'));
    stop();window.dispatchEvent(Object.assign(new Event('unhandledrejection'),{reason:own}));
    assert.equal(sent.filter(e=>e.event==='frontend_error').length,1);
  `);
});

test("a stalled diagnostic batch cannot block export and unloading cancels a not-yet-dispatched export", () => {
  isolated(`
    import {strict as assert} from 'node:assert';
    const jobs=new Map();let tick=0;globalThis.setTimeout=(fn,ms)=>{jobs.set(++tick,{fn,ms});return tick};globalThis.clearTimeout=id=>jobs.delete(id);
    const flush=async()=>{for(let i=0;i<30;i++)await Promise.resolve()};
    const c=await import('./src/diagnosticsController.ts');let exports=0;
    const protocol={status:async()=>({state:'idle'}),record:()=>new Promise(()=>{}),export:async()=>{exports++;return {state:'idle'}}};
    let stop=c.startDiagnostics(protocol);c.recordUIAction('search');const pending=c.requestDiagnosticsExport();
    const deadline=[...jobs].find(([,job])=>job.ms===1000);assert.ok(deadline);jobs.delete(deadline[0]);deadline[1].fn();await pending;assert.equal(exports,1);stop();assert.equal(jobs.size,0);
    stop=c.startDiagnostics(protocol);c.recordUIAction('search');const cancelled=c.requestDiagnosticsExport();stop();await cancelled;await flush();assert.equal(exports,1);assert.equal(jobs.size,0);
  `);
});
