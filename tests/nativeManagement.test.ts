import { expect, test } from "bun:test";
import { spawnSync } from "node:child_process";

test("native ownership distinguishes update capability, channel changes and refresh failure without implicit network", () => {
  const run = spawnSync(process.execPath, ["-e", `
    import { mock } from 'bun:test';
    let callback, reply, source = 0, requests = 0, fail = false;
    const rows = [{name:'One',version:'2.0.0',load_type:1,disabled:false}, {name:'Git',version:'deadbeef',load_type:1,disabled:false}];
    mock.module('@decky/api', () => ({ callable: () => async (_id, value) => {reply=value;}, addEventListener: (_event, fn) => {callback=fn;return fn;}, removeEventListener: () => {} }));
    mock.module('./src/loaderBridge.ts', () => ({readLoaderPlugins: async () => ({ok:true,plugins:rows})}));
    globalThis.window = {DeckyBackend:{call:async (_route,key) => key==='store' ? source : 'https://custom.example/plugins'},DeckyPluginLoader:{updateVersion:async()=>({current:'v3.3.0'})}};
    globalThis.fetch = async (url, options) => {
      requests++;
      if (fail) throw Error('offline');
      if (url!=='https://testing.deckbrew.xyz/plugins/versions' || options.headers['X-Decky-Version']!=='v3.3.0') throw Error('wrong native channel');
      return new Response(JSON.stringify({One:['2.0.0','hash'],Git:['3.0.0','hash'],Uninstalled:['1.0.0','hash']}));
    };
    const {startNativeManagement}=await import('./src/nativeManagement.ts');
    const stop=startNativeManagement();
    const cached={channel:'https://plugins.deckbrew.xyz/plugins',names:['One','Git','Uninstalled']};
    await callback({id:'a',refresh:false,cache:cached});
    if (JSON.stringify(reply.capable)!=='["One"]' || requests!==0) throw Error('current version must still be native-update-capable');
    source=1;
    await callback({id:'b',refresh:false,cache:cached});
    if (reply.capable!==null || requests!==0) throw Error('old channel became new-channel authority');
    await callback({id:'c',refresh:true,cache:cached});
    if (JSON.stringify(reply.capable)!=='["One"]' || reply.cache.channel!=='https://testing.deckbrew.xyz/plugins') throw Error('native refresh capability mismatch');
    fail=true;
    await callback({id:'d',refresh:true,cache:reply.cache});
    if (!reply.error || reply.cache!==null) throw Error('failed refresh overwrote successful metadata');
    stop();
  `], {cwd:process.cwd(),encoding:"utf8"});
  expect(run.stderr).toBe("");
  expect(run.status).toBe(0);
});
