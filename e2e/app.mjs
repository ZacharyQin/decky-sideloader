import assert from 'node:assert/strict';
import {existsSync, readFileSync, readdirSync} from 'node:fs';
import {mkdir, writeFile} from 'node:fs/promises';
import {dirname, join, relative} from 'node:path';
import {Host, root, sha, text} from './harness.mjs';
import {workflow, workflowInputs} from './workflows.mjs';
import {fullSelfUpdate, selfInputs} from './self_update.mjs';
import {discovery, discoveryInputs} from './discovery.mjs';
import {checks, checksInputs} from './checks.mjs';
import {diagnosticUi, diagnosticsInputs} from './diagnostics.mjs';
import {assertCoverage, assertHttpTrace, assertInstallation, assertSelfUpdate, exerciseOracles} from './oracles.mjs';

const inputs = JSON.parse(readFileSync(join(root, 'e2e/fixtures/install.json'), 'utf8'));
const goldens = JSON.parse(readFileSync(join(root, 'e2e/fixtures/install-hashes.json'), 'utf8'));
const httpGoldens = JSON.parse(readFileSync(join(root, 'e2e/fixtures/http-routes.json'), 'utf8'));
const caseIndex = process.argv.indexOf('--case');
const selection = caseIndex === -1 ? null : process.argv[caseIndex + 1];
const reportIndex = process.argv.indexOf('--report');
const reportPath = reportIndex === -1 ? null : process.argv[reportIndex + 1];
if (caseIndex !== -1 && !selection || reportIndex !== -1 && !reportPath) throw Error('Missing option value');
const fixtureIndex = process.argv.indexOf('--fixture-core');
const fixtureCore = fixtureIndex === -1 ? null : process.argv[fixtureIndex + 1];
const candidateIndex = process.argv.indexOf('--self-candidate');
const candidateRoot = candidateIndex === -1 ? null : process.argv[candidateIndex + 1];
const selectedCases = selection ? inputs.cases.filter(row => row.id === selection) : inputs.cases;
const selectedDownloads = selection ? inputs.downloads.filter(row => row.id === selection) : inputs.downloads;
const selectedWorkflows = selection ? workflowInputs.cases.filter(row => row.id === selection) : workflowInputs.cases;
const selectedSelf = selection ? selfInputs.cases.filter(row => row.id === selection) : selfInputs.cases;
const selectedDiscovery = selection ? discoveryInputs.cases.filter(row => row.id === selection) : discoveryInputs.cases;
const selectedChecks = selection ? checksInputs.cases.filter(row => row.id === selection) : checksInputs.cases;
const selectedDiagnostics = selection ? diagnosticsInputs.cases.filter(row => row.id === selection) : diagnosticsInputs.cases;
if (selectedSelf.length && !candidateRoot) throw Error('Use test:e2e to build the different-version self-update candidate');
if (selection && !selectedCases.length && !selectedDownloads.length && !selectedWorkflows.length && !selectedSelf.length && !selectedDiscovery.length && !selectedChecks.length && !selectedDiagnostics.length && !['baseline', 'self-update'].includes(selection)) throw Error('Unknown --case');
const results = [];
const oracleChecks = exerciseOracles(inputs.cases);

function tree(directory) {
  if (!existsSync(directory)) return {};
  const output = {};
  const visit = path => {
    for (const entry of readdirSync(path, {withFileTypes:true})) {
      const file = join(path, entry.name);
      if (entry.isDirectory()) visit(file);
      else { assert.equal(entry.isFile(), true, 'unexpected special fixture file'); output[relative(directory, file)] = sha(readFileSync(file)); }
    }
  };
  visit(directory);
  return output;
}
function history(host) {
  const path = join(host.directory, 'native-events.jsonl');
  return existsSync(path) ? readFileSync(path, 'utf8').trim().split('\n').filter(Boolean).map(line => JSON.parse(line)) : [];
}
async function withHost(name, fn) {
  const isSelf = selfInputs.cases.some(row => row.id === name);
  const needsDriver = inputs.downloads.some(row => row.id === name) || workflowInputs.cases.some(row => row.id === name) || discoveryInputs.cases.some(row => row.id === name) || checksInputs.cases.some(row => row.id === name) || diagnosticsInputs.cases.some(row => row.id === name) || isSelf;
  const host = await Host.start(name, needsDriver ? fixtureCore : null, isSelf ? candidateRoot : null);
  let success = false;
  let originalFailure;
  try { const result = await fn(host); success = true; return result; }
  catch (error) {
    originalFailure = error;
    try { const detail = await host.call('__metadata__'); error.message += `; fixture: ${JSON.stringify({http:detail.http, history:detail.history, operation:await host.call('get_operation'), rpc:host.rpcCalls.slice(-12)})}`; }
    catch { /* Preserve the original failure when the fixture has exited. */ }
    throw error;
  } finally {
    try { await host.close(success); }
    catch (cleanupError) {
      if (!originalFailure) throw cleanupError;
      originalFailure.message += `; cleanup: ${cleanupError.message}`;
    }
  }
}
async function installation(expected) {
  return withHost(expected.id, async host => {
    const fixture = host.ready.fixture;
    const home = join(host.directory, 'home');
    const original = tree(join(home, 'plugins', fixture.package.folder));
    await host.mount();
    await host.click('viewTask');
    await host.operation('ready');
    if (expected.action === 'cancel_ready') await host.click('cancelRequest');
    else {
      await host.click('confirmInstall');
      if (expected.dispatches) {
        await host.eventually(() => host.prompt, 'native prompt requested');
        if (expected.action === 'cancel_prompt') {
          await host.eventually(() => host.actionEnabled('cancelRequest'), 'correlated native cancellation enabled');
          await host.click('cancelRequest');
        } else if (expected.action === 'restart_unknown') {
          await host.eventually(async () => (await host.call('get_operation')).request_id === fixture.request_id, 'real native request persisted');
          await host.unmount();
          await host.restart(true);
          await host.mount();
          await host.click('viewTask');
          const beforeLateFinish = host.installEventAcks.length;
          await host.perform(async () => { host.emit('loader/plugin_download_finish', fixture.package.name); });
          await host.eventually(() => host.installEventAcks.slice(beforeLateFinish).some(event => event.kind === 'finish'), 'late finish acknowledged without replay or success');
        } else {
          await host.perform(async () => { await host.loader.call('utilities/confirm_plugin_install', fixture.request_id); });
          if (expected.native !== 'wrong_name') await host.awaitConfirmationRecorded();
        }
      }
    }
    let operation = await host.operation(expected.stage);
    if (['cancel_ready', 'cancel_prompt'].includes(expected.action)) {
      await host.perform(async () => {
        host.emit('loader/add_plugin_install_prompt', fixture.package.name, fixture.package.version, fixture.request_id, fixture.archive_sha256);
        host.emit('loader/plugin_download_start', fixture.package.name);
        host.emit('loader/plugin_download_finish', fixture.package.name);
      });
      operation = await host.operation('cancelled');
    }
    const protectedHashes = Object.fromEntries(Object.keys(inputs.protected).map(name => [name, sha(readFileSync(join(home, name)))]));
    const log = history(host);
    assert.deepEqual((await host.call('__metadata__')).history, log, 'append-only log agrees with observed native boundary');
    const installedHashes = tree(join(home, 'plugins', fixture.package.folder));
    const directory = dirname(fixture.artifact);
    const backupPath = join(directory, 'data-backup.zip');
    const actual = {operation, history:log, protectedHashes, expectedProtectedHashes:goldens.protected,
      installedHashes, expectedInstalledHashes:goldens.packages[expected.package].files,
      artifactExists:existsSync(fixture.artifact), backupMatches:existsSync(backupPath) && sha(readFileSync(backupPath)) === fixture.backup_sha256,
      originalProgramBefore:original, originalProgramAfter:installedHashes};
    assertInstallation(expected, actual);
    if (expected.outcome === 'success') {
      await host.eventually(() => host.visible().includes('Installation complete') || host.visible().includes('Installed successfully'), 'verified success reached rendered operation UI');
      const settings = JSON.parse(readFileSync(join(home, 'settings/decky-sideloader/settings.json'), 'utf8'));
      assert.equal(settings.bindings[fixture.package.name].repo, fixture.package.repo, 'actual repository binding persisted');
    } else assert.notEqual(operation.outcome, 'success');
    assert.ok(host.rpcCalls.includes('cancel_operation') || host.rpcCalls.includes('submit_prepared'), 'real frontend mutation reached Python/Rust');
    return {id:expected.id, stage:operation.stage, outcome:operation.outcome,
      counts:{dispatch:log.filter(row=>row.kind==='dispatch').length, confirm:log.filter(row=>row.kind==='confirm').length, cancel:log.filter(row=>row.kind==='cancel').length},
      installedFiles:Object.keys(installedHashes).length, protectedFiles:Object.keys(protectedHashes).length};
  });
}
async function download(expected) {
  assert.ok(fixtureCore, 'use pnpm test:e2e to build the private process driver');
  return withHost(expected.id, async host => {
    await host.mount();
    await host.click('newInstall');
    const input = host.screen.root.findAll(node => node.type === 'TextField' && typeof node.props.onChange === 'function')[0];
    assert.ok(input, 'real repository input');
    await host.perform(async () => { input.props.onChange({target:{value:'fixture/target'}}); });
    await host.click('search');
    await host.eventually(() => host.actionEnabled('installPackage'), 'probed repository/release preview');
    await host.click('installPackage');
    if (expected.dispatches) {
      await host.eventually(() => host.modalScreen, 'actual preparation warning dialog');
      await host.confirmWarningModal();
      await host.eventually(() => host.prompt, 'downloaded/verified package reached native prompt');
      await host.perform(async () => { await host.loader.call('utilities/confirm_plugin_install', host.prompt.request_id); });
      await host.awaitConfirmationRecorded();
    }
    const operation = await host.operation(expected.stage);
    assert.equal(operation.outcome, expected.outcome);
    const metadata = await host.call('__metadata__');
    const log = history(host);
    for (const [kind,count] of Object.entries({dispatch:expected.dispatches,confirm:expected.confirms,cancel:expected.cancels}))
      assert.equal(log.filter(row=>row.kind===kind).length,count,`${expected.id}: exact native ${kind}`);
    assertHttpTrace(httpGoldens[expected.id], metadata.http);
    const home = join(host.directory,'home');
    const installed = tree(join(home,'plugins/fixture-target'));
    assert.deepEqual(installed, expected.outcome === 'success' ? goldens.packages.new.files : {});
    for (const [name,checksum] of Object.entries(goldens.protected)) assert.equal(sha(readFileSync(join(home,name))),checksum);
    assert.ok(host.rpcCalls.includes('probe_repository'));
    assert.ok(host.rpcCalls.includes('list_releases'));
    assert.ok(host.rpcCalls.includes('prepare_install'));
    if (expected.outcome === 'success') {
      assert.ok(host.rpcCalls.includes('submit_prepared'));
      assert.equal(host.modalCount,1,'license warning consumed exactly once');
      assert.equal(operation.binding_saved,true);
      const saved = JSON.parse(readFileSync(join(home,'settings/decky-sideloader/settings.json'),'utf8'));
      assert.equal(saved.bindings['Fixture Target'].repo,'fixture/target','downloaded target binding persisted');
      assert.equal(existsSync(metadata.fixture.artifact),false,'verified downloaded package removed');
      await host.eventually(() => host.visible().includes('Installation completed'), 'download-to-install success rendered');
    }
    return {id:expected.id, stage:operation.stage, outcome:operation.outcome,
      httpRequests:Object.values(metadata.http.counts).reduce((a,b)=>a+b,0), routes:metadata.http.counts,
      dispatches:log.filter(row=>row.kind==='dispatch').length, installedFiles:Object.keys(installed).length};
  });
}
async function baseline() {
  return withHost('baseline', async host => {
    await host.mount();
    assert.ok(host.observedEvents.includes('native_management_requested'));
    assert.ok(host.rpcCalls.includes('receive_native_management'));
    assert.ok(host.nativeCalls.some(row => row.route === 'loader/get_plugins'));
    assert.equal(await host.call('ping'), 'pong');
    assert.equal((await host.call('set_network_mode', 'ghfast')).networkMode, 'ghfast');
    await assert.rejects(host.call('set_network_mode', 'not-a-mode'));
    await assert.rejects(host.call('start_diagnostics_share', '127.0.0.1', 'a'.repeat(32), null, null), /所选地址无效/);
    assert.equal((await host.call('get_app_settings')).networkMode, 'ghfast');
    const persisted = JSON.parse(readFileSync(join(host.directory,'home/settings/decky-sideloader/settings.json'),'utf8'));
    assert.equal(persisted.networkMode, 'ghfast');
    await host.click('settings');
    const license = host.screen.root.findAll(node => typeof node.type === 'string' && typeof node.props.onClick === 'function').find(node => text(node).trim() === 'Open-source licenses');
    assert.ok(license, 'actual settings license action');
    await host.perform(async () => { license.props.onClick(); });
    await host.eventually(() => host.visible().includes('serde'), 'actual Rust offline license page');
    assert.ok(host.rpcCalls.includes('get_open_source_notices'));
    await host.click('license-entry-rust:serde:1.0.229');
    await host.click('license-document-0');
    const renderedLicense = host.screen.root.findAll(node => node.type === 'Field' && node.props.className === 'gss-license-paragraph').map(node => node.props.label).join('');
    assert.equal(sha(renderedLicense), '62c7a1e35f56406896d7aa7ca52d0cc0d272ac022b5d2796e7d6905db8a3636a', 'complete Apache license body rendered without truncation');
    await host.unmount();
    await host.restart();
    assert.equal((await host.call('get_app_settings')).networkMode, 'ghfast');
    await assert.rejects(host.call('__unconfigured_control__'), /Unknown fixture control/);
    await assert.rejects(host.call('not_a_real_rpc'), /Unsupported fixture RPC/);
    const priorNativeRequests = host.observedEvents.filter(name => name === 'native_management_requested').length;
    const blocked = [1,2,3].map(() => host.call('list_sideload_plugins'));
    const settled = Promise.allSettled(blocked);
    await host.eventually(() => host.observedEvents.filter(name => name === 'native_management_requested').length >= priorNativeRequests + 3, 'concurrent real native requests outstanding');
    const killed = await host.call('__kill_core__');
    assert.notEqual(killed.returncode, 0);
    const failures = await settled;
    assert.equal(failures.length, 3);
    assert.ok(failures.every(row => row.status === 'rejected'), 'concurrent IPC failures settled');
    await assert.rejects(host.call('ping'));
    assert.equal((await host.call('__metadata__')).core_pid, killed.core_pid, 'crash did not auto-restart/replay');
    await host.restart();
    assert.equal(await host.call('ping'), 'pong');
    assert.equal((await host.call('get_app_settings')).networkMode, 'ghfast');
    assert.equal(history(host).length, 0);
    return {id:'baseline', checks:['bundle render','native snapshot roundtrip','durable settings/restart','invalid mode','production loopback denied','offline licenses','unknown route rejection','concurrent crash settles','no automatic restart']};
  });
}
async function selfUpdate() {
  return withHost('baseline', async host => {
    await host.call('set_delete_package_after_install', false);
    const frontend = JSON.parse(readFileSync(join(root,'dist/build.json'),'utf8'));
    const checks = [];
    for (const [mode, compatible] of [['binary',true], ['backend',true], ['bundle',false], ['matching',true]]) {
      const replaced = await host.call('__seed_self_update__', mode);
      assert.notEqual(replaced.core_pid, replaced.previous_pid);
      assert.notEqual(replaced.boot, replaced.previous_boot, 'actual handshake generation changed');
      host.corePids.add(replaced.core_pid);
      const stale = await host.call('get_self_update_status', {version:frontend.version, fingerprint:'0'.repeat(64)});
      assertSelfUpdate({id:`${mode}/stale`, stage:'verifying', compatible:false}, stale);
      checks.push(`${mode}/stale`);
      const frontendReads = host.frontendCalls.filter(name => name === 'get_self_update_status').length;
      await host.mount();
      await host.eventually(() => host.frontendCalls.filter(name => name === 'get_self_update_status').length > frontendReads, 'frontend controller itself queried self-update status');
      const current = await host.call('get_self_update_status', {version:frontend.version, fingerprint:frontend.fingerprint});
      assertSelfUpdate({id:mode, stage:mode === 'matching' ? 'completed' : 'verifying', compatible}, current);
      await host.unmount();
      checks.push(mode);
    }
    assert.equal(history(host).length, 0, 'cross-instance verification never redispatches installation');
    return {id:'self-update', checks};
  });
}

let failure;
try {
  if (!selection || selection === 'baseline') { const result = await baseline(); results.push(result); console.log(JSON.stringify(result)); }
  for (const scenario of selectedDownloads) { const result = await download(scenario); results.push(result); console.log(JSON.stringify(result)); }
  for (const scenario of selectedCases) { const result = await installation(scenario); results.push(result); console.log(JSON.stringify(result)); }
  for (const scenario of selectedWorkflows) { const result = await withHost(scenario.id, host => workflow(host, scenario)); results.push(result); console.log(JSON.stringify(result)); }
  for (const scenario of selectedDiscovery) { const result = await withHost(scenario.id, host => discovery(host, scenario)); results.push(result); console.log(JSON.stringify(result)); }
  for (const scenario of selectedChecks) { const result = await withHost(scenario.id, host => checks(host, scenario, fixtureCore)); results.push(result); console.log(JSON.stringify(result)); }
  for (const scenario of selectedDiagnostics) { const result = await withHost(scenario.id, host => diagnosticUi(host, scenario)); results.push(result); console.log(JSON.stringify(result)); }
  for (const scenario of selectedSelf) { const result = await withHost(scenario.id, host => fullSelfUpdate(host, scenario, candidateRoot)); results.push(result); console.log(JSON.stringify(result)); }
  if (!selection || selection === 'self-update') { const result = await selfUpdate(); results.push(result); console.log(JSON.stringify(result)); }
  assertCoverage([...(selection ? [] : ['baseline','self-update']), ...selectedCases.map(row=>row.id), ...selectedDownloads.map(row=>row.id), ...selectedWorkflows.map(row=>row.id), ...selectedSelf.map(row=>row.id), ...selectedDiscovery.map(row=>row.id), ...selectedChecks.map(row=>row.id), ...selectedDiagnostics.map(row=>row.id), ...(selection === 'baseline' || selection === 'self-update' ? [selection] : [])], results.map(row=>row.id));
} catch (error) { failure = error; }
const report = {status:failure?'failed':'passed', selection:selection ?? 'all', layers:{frontend:'actual bundle/Node VM/React renderer', timers:'workflow UI intervals capped at 50 ms; product/native/network timeouts unchanged', adapter:'actual Python process', core:'release Rust child; download cases use cfg(test) driver sharing production entry/Engine', native:'strict fixture; real temporary file writes', network:'release cases deny non-Unix sockets; download driver pins Transport to strict loopback HTTP'}, results, oracleChecks, noDevice:true,
  ...(failure ? {failure:String(failure.stack ?? failure)} : {})};
if (reportPath) { await mkdir(dirname(reportPath),{recursive:true}); await writeFile(reportPath,JSON.stringify(report,null,2)+'\n'); }
console.log(JSON.stringify(report));
if (failure) throw failure;
