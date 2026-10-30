import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createHash, webcrypto} from 'node:crypto';
import {readFileSync, readdirSync} from 'node:fs';
import {mkdtemp, rm} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {tmpdir} from 'node:os';
import {dirname, join, relative, resolve} from 'node:path';
import {createInterface} from 'node:readline';
import {fileURLToPath} from 'node:url';
import {createContext, SourceTextModule} from 'node:vm';

export const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(join(root, 'package.json'));
const React = require('react');
const JSX = require('react/jsx-runtime');
const renderer = require('react-test-renderer');
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
export const sha = data => createHash('sha256').update(data).digest('hex');
export const text = node => typeof node === 'string' ? node : Array.isArray(node) ? node.map(text).join(' ') : node && node.type !== 'style' ? text(node.children ?? []) : '';
export async function bounded(promise, ms, message) {
  let timer;
  try { return await Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(Error(message)), ms); })]); }
  finally { clearTimeout(timer); }
}

export class Host {
  static async start(scenario = 'baseline', fixtureCore = null, candidateRoot = null) {
    const host = new Host();
    host.workflow = scenario.startsWith('workflow_') || scenario.startsWith('self_') || scenario.startsWith('discovery_') || scenario.startsWith('checks_') || scenario.startsWith('diagnostics_');
    host.allowWarningModal = Boolean(fixtureCore);
    host.directory = await mkdtemp(join(tmpdir(), 'sideloader-e2e-'));
    const environment = Object.fromEntries(['PATH', 'LANG', 'LC_ALL', 'LD_LIBRARY_PATH'].filter(key => process.env[key]).map(key => [key, process.env[key]]));
    environment.PYTHONHASHSEED = '0';
    host.child = spawn('python3', ['-B', join(root, 'e2e/bridge.py'), host.directory, scenario, ...(fixtureCore ? [fixtureCore] : []), ...(candidateRoot ? [candidateRoot] : [])], {env: environment, stdio: ['pipe', 'pipe', 'pipe']});
    host.exit = new Promise(resolve => host.child.once('close', (code, signal) => resolve({code, signal})));
    let resolveReady, rejectReady;
    const ready = new Promise((resolve, reject) => { resolveReady = resolve; rejectReady = reject; });
    host.child.stderr.on('data', data => { host.stderr = (host.stderr + data).slice(-1024 * 1024); });
    host.child.on('error', rejectReady);
    host.child.on('exit', (code, signal) => {
      const error = Error(`fixture exited (${code}, ${signal}): ${host.stderr}`);
      if (!host.closed) rejectReady(error);
      for (const item of host.pending.values()) { clearTimeout(item.timer); item.reject(error); }
      host.pending.clear();
    });
    host.lines = createInterface({input: host.child.stdout});
    host.lines.on('line', line => {
      let message;
      try { message = JSON.parse(line); }
      catch { rejectReady(Error('invalid fixture response')); return; }
      if (message.ready) resolveReady(message);
      else if (message.closed) host.closed = message;
      else if (message.event) {
        host.observedEvents.push(message.event);
        host.observedEventDetails.push({name:message.event,args:message.args});
        for (const fn of host.backendEvents.get(message.event) ?? []) Promise.resolve(fn(...message.args)).catch(error => host.eventErrors.push(String(error)));
      } else {
        const item = host.pending.get(message.id);
        if (!item) { host.protocolErrors.push('unexpected response id'); return; }
        host.pending.delete(message.id); clearTimeout(item.timer);
        if (message.error) item.reject(Error(message.error)); else item.resolve(message.result);
      }
    });
    try {
      host.ready = await bounded(ready, 20000, 'fixture startup timed out');
      assert.equal(host.ready.network_denied, !fixtureCore);
      host.corePids.add(host.ready.core_pid);
      return host;
    } catch (error) { await host.close(false); throw error; }
  }
  pending = new Map(); backendEvents = new Map(); loaderEvents = new Map(); windowEvents = new Map();
  observedEvents = []; observedEventDetails = []; rpcCalls = []; frontendCalls = []; frontendCallDetails = []; nativeCalls = []; eventErrors = []; protocolErrors = []; installEventAcks = []; selfEventAcks = [];
  timers = new Set(); corePids = new Set(); toasts = []; clipboardWrites = []; frontendMounts = 0; stderr = ''; nextId = 0; modalCount = 0;
  add(map, name, fn) { if (!map.has(name)) map.set(name, new Set()); map.get(name).add(fn); return fn; }
  remove(map, name, fn) { map.get(name)?.delete(fn); if (!map.get(name)?.size) map.delete(name); }
  call(method, ...args) {
    this.rpcCalls.push(method);
    const id = ++this.nextId;
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    const timer = setTimeout(() => { this.pending.delete(id); reject(Error(`RPC timeout: ${method}`)); }, 15000);
    this.pending.set(id, {resolve, reject, timer, promise, method});
    if (method === 'record_install_event') promise.then(value => {
      this.installEventAcks.push({id:args[0], kind:args[1], stage:value?.stage});
    }, () => {});
    if (method === 'record_self_update_event') promise.then(value => {
      this.selfEventAcks.push({id:args[0], kind:args[1], stage:value?.operation?.stage});
    }, () => {});
    this.child.stdin.write(JSON.stringify({id, method, args}) + '\n', error => {
      if (error && this.pending.delete(id)) { clearTimeout(timer); reject(error); }
    });
    return promise;
  }
  async restart(age = false) {
    const result = await this.call('__restart__', {age_operation: age});
    assert.notEqual(result.previous_pid, result.core_pid);
    this.corePids.add(result.core_pid);
    return result;
  }
  emit(event, ...args) {
    for (const fn of this.loaderEvents.get(event) ?? []) fn(...args);
  }
  async mount(frontendRoot = root) {
    assert.equal(this.plugin, undefined);
    const host = this;
    const mount = ++host.frontendMounts;
    const frontendCall = (method, ...args) => { host.frontendCalls.push(method); host.frontendCallDetails.push({method, args, mount}); return host.call(method, ...args); };
    const api = {_version: 2, call: frontendCall, callable: name => (...args) => frontendCall(name, ...args),
      addEventListener: (name, fn) => host.add(host.backendEvents, name, fn),
      removeEventListener: (name, fn) => host.remove(host.backendEvents, name, fn), toaster: {toast(options) { host.toasts.push(options); }}};
    this.loader = {
      async call(route, ...args) {
        host.nativeCalls.push({route, args});
        if (route === 'loader/get_plugins') return host.call('__snapshot__');
        if (route === 'utilities/settings/get' && args.length === 2 && args[0] === 'store' && args[1] === 0) return 0;
        if (route === 'utilities/install_plugin') {
          const prompt = await host.call('__native_request__', ...args);
          host.prompt = prompt;
          host.emit('loader/add_plugin_install_prompt', prompt.mode === 'wrong_name' ? 'Other Fixture' : prompt.name,
            prompt.version, prompt.request_id, prompt.mode === 'wrong_hash' ? 'f'.repeat(64) : prompt.sha256);
          if (prompt.mode === 'wrong_request') host.emit('loader/add_plugin_install_prompt', prompt.name, prompt.version, 'different-native-request', prompt.sha256);
          return null;
        }
        if (route === 'utilities/confirm_plugin_install') {
          if (host.selfNativeLifecycle) return host.selfNativeLifecycle(args);
          const result = await host.call('__native_confirm__', ...args);
          const name = result.mode === 'wrong_name' ? 'Other Fixture' : result.name;
          if (result.mode === 'hold_after_start') {
            let release;const promise=new Promise(resolve=>{release=resolve;});host.afterStartGate={release,promise};
          }
          if (result.mode !== 'missing_start') host.emit('loader/plugin_download_start', name);
          if (result.mode === 'hold_after_start') await host.afterStartGate.promise;
          host.emit('loader/plugin_download_finish', name);
          return null;
        }
        if (host.workflow && route === 'utilities/uninstall_plugin') return host.call('__native_uninstall__', ...args);
        if (route === 'utilities/cancel_plugin_install') return host.call('__native_cancel__', ...args);
        host.protocolErrors.push(`unconfigured native route: ${route}`);
        throw Error(`unconfigured native route: ${route}`);
      },
      addEventListener: (name, fn) => host.add(host.loaderEvents, name, fn),
      removeEventListener: (name, fn) => host.remove(host.loaderEvents, name, fn),
    };
    const native = name => props => React.createElement(name, props, props.icon, props.label, props.description, props.strTitle, props.strDescription, props.inlineControls, props.children);
    const ui = new Proxy({staticClasses: {Title: 'title'}, NavEntryPositionPreferences: {FIRST: 0, PREFERRED_CHILD: 4},
      gamepadDialogClasses: {}, updaterFieldClasses: {UpdaterFieldStatusSuccess: 'success'}, findClassByName: () => undefined,
      Navigation: {NavigateToExternalWeb() { throw Error('external navigation refused'); }},
      showModal(element) {
        if (!host.allowWarningModal || host.modalScreen || !host.workflow && host.modalCount) {
          host.protocolErrors.push('unexpected modal'); throw Error('unexpected modal');
        }
        ++host.modalCount;
        host.modalScreen = renderer.create(element);
        return {Close() { host.modalScreen?.unmount(); host.modalScreen = undefined; }};
      }},
    {get(target, name) { if (name === 'then') return undefined; return Object.prototype.hasOwnProperty.call(target, name) ? target[name] : native(String(name)); }});
    this.window = {location: {search: '?LANGUAGE=english'},
      __DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit: {connect(version, name) { assert.equal(version, 2); assert.equal(name, 'Sideloader'); return api; }},
      DeckyBackend: this.loader, DeckyPluginLoader: {deckyState: {closeActivePlugin() {}},
        updateVersion: async () => ({current:'fixture'}),
        frozenPluginsService: host.ready.fixture?.workflow?.store ? {update: names => host.call('__frozen_update__', names), invalidate: () => host.call('__frozen_invalidate__')} : undefined,
        uninstallPlugin(name, title, buttonText, description) {
        if (!host.workflow || host.uninstallPrompt || name !== 'Example') throw Error('unexpected uninstall');
        host.uninstallPrompt = {name, title, buttonText, description};
      }},
      addEventListener: (name, fn) => host.add(host.windowEvents, name, fn), removeEventListener: (name, fn) => host.remove(host.windowEvents, name, fn)};
    const setTimer = (fn, delay, interval = false) => {
      const wrapped = () => { if (!interval) host.timers.delete(id); fn(); };
      const timerDelay = interval && host.workflow ? Math.min(delay, 50) : delay;
      const id = interval ? setInterval(wrapped, timerDelay) : setTimeout(wrapped, timerDelay);
      host.timers.add(id); return id;
    };
    const clearTimer = id => { host.timers.delete(id); clearTimeout(id); clearInterval(id); };
    const context = createContext({window: this.window, SP_REACT: React, SP_JSX: JSX, DFL: ui, console,
      crypto: webcrypto, TextEncoder, TextDecoder, URL, URLSearchParams, AbortController,
      setTimeout: (fn, delay) => setTimer(fn, delay), setInterval: (fn, delay) => setTimer(fn, delay, true), clearTimeout: clearTimer, clearInterval: clearTimer,
      navigator: {language: 'en', languages: ['en'], ...(host.ready.fixture?.diagnostics ? {clipboard:{async writeText(value){host.clipboardWrites.push(value);}}} : {})}, document: {documentElement: {lang: 'en'}, createElement() { return {textContent: '', set innerHTML(value) { this.textContent = value; }}; }},
      localStorage: {getItem: () => null, setItem() {}, removeItem() {}}, async fetch(url, options = {}) {
        if (!host.ready.fixture?.workflow?.store || Object.keys(options).some(key=>!['headers','signal'].includes(key)) || options.signal?.aborted) throw Error('unconfigured frontend network request');
        const data = await host.call('__store_versions__', String(url), 'GET', options.headers);
        const bytes = new TextEncoder().encode(JSON.stringify(data));let consumed = false;
        return {ok:true,body:{getReader(){return {async read(){if(consumed)return {done:true};consumed=true;return {done:false,value:bytes};},async cancel(){consumed=true;}};}}};
      }});
    const code = readFileSync(join(frontendRoot, 'dist/index.js'), 'utf8');
    this.build = JSON.parse(readFileSync(join(frontendRoot, 'dist/build.json'), 'utf8'));
    assert.equal(sha(code), this.build.bundle, 'frontend bundle hash mismatch');
    const sources = directory => readdirSync(directory, {withFileTypes: true}).flatMap(entry => {
      const path = join(directory, entry.name);
      return entry.isDirectory() ? sources(path) : /\.(ts|tsx|css)$/.test(entry.name) ? [path] : [];
    });
    const files = [...sources(join(frontendRoot, 'src')), ...['package.json', 'pnpm-lock.yaml', 'rollup.config.js', 'scripts/build-identity.mjs', 'scripts/license-notices.mjs'].map(name => join(frontendRoot, name))].sort();
    const fingerprint = createHash('sha256');
    for (const path of files) fingerprint.update(relative(frontendRoot, path)).update('\0').update(readFileSync(path)).update('\0');
    assert.equal(fingerprint.digest('hex'), this.build.fingerprint, 'stale frontend: run pnpm build');
    const module = new SourceTextModule(code, {context, identifier: 'decky://Sideloader/index.js'});
    await module.link(specifier => { throw Error(`unexpected import ${specifier}`); });
    await module.evaluate({timeout: 10000});
    this.plugin = module.namespace.default();
    await React.act(async () => { this.screen = renderer.create(this.plugin.content); });
    await this.eventually(() => this.visible().includes('Example'), 'actual fixture plugin rendered');
  }
  visible() { return this.screen ? text(this.screen.toJSON()) : ''; }
  async perform(fn) { return React.act(fn); }
  async awaitConfirmationRecorded(offset = 0) {
    await this.eventually(() => this.installEventAcks.slice(offset).some(event => event.kind === 'confirm_returned'), 'complete native confirmation event queue acknowledged');
  }
  async respondModal(accept, expectedDescription) {
    await this.eventually(() => this.modalScreen, 'actual confirmation dialog opened');
    const dialog = this.modalScreen.root.findAll(node => node.type === 'ConfirmModal')[0];
    assert.ok(dialog);
    assert.ok(dialog.props.strDescription.includes(expectedDescription), 'unexpected confirmation description');
    if (accept) assert.equal(Boolean(dialog.props.bOKDisabled), false);
    await this.perform(async () => { dialog.props[accept ? 'onOK' : 'onCancel'](); });
  }
  async respondNativeUninstall(accept) {
    await this.eventually(() => this.uninstallPrompt, 'native uninstall confirmation requested');
    const prompt = this.uninstallPrompt;
    assert.equal(prompt.name, 'Example');
    assert.ok(prompt.description.includes('settings/data'));
    this.uninstallPrompt = null;
    if (accept) await this.perform(async () => { await this.loader.call('utilities/uninstall_plugin', prompt.name); });
  }
  async confirmWarningModal() {
    const dialog = this.modalScreen.root.findAll(node => node.type === 'ConfirmModal')[0];
    assert.ok(dialog);
    assert.ok(dialog.props.strDescription.includes('LICENSE'), 'expected license warning, not an unrelated consent');
    assert.equal(Boolean(dialog.props.bOKDisabled), false);
    await React.act(async () => { dialog.props.onOK(); });
  }
  actionEnabled(key) {
    const button = this.screen?.root.findAll(node => typeof node.type === 'string' && node.props['data-action-key'] === key)[0];
    return Boolean(button && !button.props.disabled);
  }
  async click(key) {
    const button = this.screen.root.findAll(node => typeof node.type === 'string' && node.props['data-action-key'] === key)[0];
    assert.ok(button, `missing action ${key}: ${this.visible().slice(0, 500)}`);
    assert.ok(!button.props.disabled, `disabled action ${key}`);
    await React.act(async () => { button.props.onClick(); });
  }
  async eventually(predicate, message, ms = 8000) {
    const deadline = Date.now() + ms;
    while (Date.now() < deadline) {
      let result;
      await React.act(async () => { result = await predicate(); await new Promise(resolve => setTimeout(resolve, 15)); });
      if (result) return result;
      if (this.protocolErrors.length || this.eventErrors.length) throw Error([...this.protocolErrors, ...this.eventErrors].join('\n'));
    }
    throw Error(`timed out: ${message}; UI: ${this.visible().slice(0, 2500)}; ${this.stderr}`);
  }
  async operation(stage) {
    return this.eventually(async () => { const value = await this.call('get_operation'); return value?.stage === stage && value; }, `operation ${stage}`);
  }
  async unmount(settle = true, pendingNativeUninstall = false) {
    if (this.modalScreen) { await React.act(async () => this.modalScreen.unmount()); this.modalScreen = undefined; }
    if (this.screen) { await React.act(async () => this.screen.unmount()); this.screen = undefined; }
    if (this.plugin) { const plugin = this.plugin; this.plugin = undefined; await React.act(async () => { plugin.onDismount(); }); }
    assert.equal(this.backendEvents.size, 0); assert.equal(this.loaderEvents.size, 0); assert.equal(this.windowEvents.size, 0);
    if (pendingNativeUninstall) {
      assert.equal([...this.pending.values()].filter(value=>value.method==='__native_uninstall__').length,1,'only an actual pending native uninstall may retain its bounded waiter');
      assert.equal(this.timers.size,1);
      assert.equal([...this.timers][0]._idleTimeout,120000,'unexpected timer beyond the native uninstall watchdog');
    } else assert.equal(this.timers.size, 0, 'application timer leak');
    if (settle) await bounded(Promise.allSettled([...this.pending.values()].map(item => item.promise).filter(Boolean)), 3000, 'pending fixture RPC cleanup');
  }
  async close(check = true) {
    try {
      await this.unmount(false);
      if (this.afterStartGate) await React.act(async()=>{this.afterStartGate.release();await this.afterStartGate.promise;});
      if (this.child?.exitCode === null && !this.child.killed) {
        this.child.stdin.end('{"method":"__shutdown__"}\n');
        await bounded(this.exit, 20000, 'fixture shutdown timed out');
      }
      if (check) {
        assert.equal(this.closed?.core_returncode, 0, this.stderr);
        assert.equal(this.pending.size, 0);
        assert.deepEqual(this.eventErrors, []); assert.deepEqual(this.protocolErrors, []);
        if (this.closed.http) {
          assert.deepEqual(this.closed.http.errors, [], 'strict HTTP fixture errors');
          assert.deepEqual(this.closed.http.mismatches, {}, 'required HTTP routes consumed exactly');
          assert.equal(this.closed.http.active_clients, 0, 'HTTP client cleanup');
          assert.equal(this.closed.http.listening, false, 'HTTP listener cleanup');
        }
        if (this.closed.phase_gate) {
          assert.deepEqual(this.closed.phase_gate.errors, []);
          assert.equal(this.closed.phase_gate.clients, 0);
          assert.equal(this.closed.phase_gate.listening, false);
        }
        for (const pid of this.corePids) {
          try { process.kill(pid, 0); assert.fail(`Rust core ${pid} survived cleanup`); }
          catch (error) { if (error.code !== 'ESRCH') throw error; }
        }
      }
    } finally {
      if (this.child?.exitCode === null && !this.child.killed) { this.child.kill('SIGTERM'); await bounded(this.exit, 5000, 'fixture termination timed out'); }
      this.lines?.close();
      for (const timer of this.timers) { clearTimeout(timer); clearInterval(timer); }
      for (const item of this.pending.values()) clearTimeout(item.timer);
      if (this.directory) await rm(this.directory, {recursive: true, force: true});
    }
  }
}
