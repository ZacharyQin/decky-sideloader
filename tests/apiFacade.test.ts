import { expect, test } from "bun:test";
import { spawnSync } from "node:child_process";

test("API facade enriches list and manual check, preserving backend rejection", () => {
  // Fresh runtime avoids the UI suite's deliberately mocked API facade.
  const script = `
    import { mock } from 'bun:test';
    const calls = [];
    let fail = false;
    const disk = { native_snapshot: { ok: true, plugins: [{ name: 'Native', version: '2', load_type: 0, disabled: false }] }, plugins: [], store_ok: false, store_error: 'offline', last_check: null };
    mock.module('@decky/api', () => ({ callable: (name) => async () => {
      calls.push(name); if (fail) throw new Error('disk RPC failed'); return disk;
    }}));
    const api = await import('./src/api.ts');
    for (const read of [api.listSideloadPlugins, api.checkUpdates]) {
      const result = await read();
      if (result.plugins[0].folder !== null || !result.loader.ok || result.store_ok) throw new Error('merge missing');
    }
    fail = true;
    for (const read of [api.listSideloadPlugins, api.checkUpdates]) {
      let rejected = false;
      try { await read(); } catch (e) { rejected = e.message === 'disk RPC failed'; }
      if (!rejected) throw new Error('RPC failure swallowed');
    }
  `;
  const run = spawnSync(process.execPath, ["-e", script], { cwd: process.cwd(), encoding: "utf8" });
  expect(run.stderr).toBe("");
  expect(run.status).toBe(0);
});


test("ownership RPC DTOs are bounded, correlated and cannot accept malformed native state", () => {
  const script = `
    import { mock } from 'bun:test';
    let reply;
    const calls = [];
    mock.module('@decky/api', () => ({ callable: (name) => async (...args) => { calls.push([name, ...args]); return reply; }}));
    const api = await import('./src/api.ts');
    const intent = { id: 'a'.repeat(32), name: 'One', folder: 'one', repo: 'acme/One', prior_frozen: false, action: 'takeover', state: 'prepared' };
    reply = intent;
    await api.beginStoreOwnership('One', 'one', 'acme/One', 'takeover');
    for (const value of [null, {}, {...intent, extra: true}, {...intent, id: 'x'}, {...intent, name: 'Other'}, {...intent, folder: '../one'}, {...intent, prior_frozen: 1}, {...intent, repo: 'x'.repeat(257)}]) {
      reply = value; let failed = false;
      try { await api.beginStoreOwnership('One', 'one', 'acme/One', 'takeover'); } catch { failed = true; }
      if (!failed) throw Error('invalid intent accepted');
    }
    reply = { intent: {...intent, state: 'writing'}, frozen: ['One', 'Other'] };
    await api.ownershipNativeStep(intent.id, 'writing');
    for (const value of [null, {}, {intent, frozen: null}, {intent, frozen: ['One','One']}, {intent, frozen: ['x'.repeat(257)]}, {intent, frozen: Array(4097).fill('One')}, {intent, frozen: ['bad\\n']}, {intent: {...intent, id: 'b'.repeat(32)}, frozen: []}, {intent, frozen: [], extra: true}]) {
      reply = value; let failed = false;
      try { await api.ownershipNativeStep(intent.id, 'writing'); } catch { failed = true; }
      if (!failed) throw Error('invalid native snapshot accepted');
    }
    await api.authorizePluginMutation('One');
    if (calls[0][0] !== 'begin_store_ownership' || calls.at(-1).join(',') !== 'authorize_plugin_mutation,One') throw Error('RPC mismatch');
  `;
  const run = spawnSync(process.execPath, ["-e", script], { cwd: process.cwd(), encoding: "utf8" });
  expect(run.stderr).toBe("");
  expect(run.status).toBe(0);
});


test("task52 cancel and dismiss RPCs retain exact task IDs and propagate persistence rejection", () => {
  const script = `
    import { mock } from 'bun:test';
    const calls = [];
    let fail = false;
    mock.module('@decky/api', () => ({ callable: (name) => async (...args) => {
      calls.push([name, ...args]);
      if (fail) throw Error('write failed');
      return {id: args[0], stage: 'cancelled'};
    }}));
    const api = await import('./src/api.ts');
    for (const method of [api.cancelOperation, api.dismissOperation]) {
      if ((await method('exact-task')).id !== 'exact-task') throw Error('task identity lost');
    }
    fail = true;
    for (const method of [api.cancelOperation, api.dismissOperation]) {
      let rejected = false;
      try { await method('exact-task'); } catch (e) { rejected = e.message === 'write failed'; }
      if (!rejected) throw Error('persistence rejection swallowed');
    }
    if (JSON.stringify(calls) !== JSON.stringify([
      ['cancel_operation','exact-task'], ['dismiss_operation','exact-task'],
      ['cancel_operation','exact-task'], ['dismiss_operation','exact-task']
    ])) throw Error('RPC scope changed');
  `;
  const run = spawnSync(process.execPath, ["-e", script], { cwd: process.cwd(), encoding: "utf8" });
  expect(run.stderr).toBe(""); expect(run.status).toBe(0);
});



