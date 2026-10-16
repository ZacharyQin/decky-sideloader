import { afterEach, describe, expect, mock, test } from "bun:test";
import { cancelPendingInstall, installFromArtifact, InstallType, listenForInstallEvents, probeLoaderChannel, uninstallForReinstall } from "../src/loaderBridge";
import type { InstallOperation } from "../src/types";

const original = globalThis.window;
afterEach(() => { globalThis.window = original; });
function setup() {
  const call = mock(async (..._args: unknown[]) => undefined);
  const events = new Map<string, (...args: unknown[]) => void>();
  globalThis.window = { DeckyBackend: {
    call, addEventListener: (name: string, cb: (...args: unknown[]) => void) => events.set(name, cb),
    removeEventListener: (name: string) => events.delete(name),
  }, DeckyPluginLoader: { uninstallPlugin: mock() } } as unknown as Window;
  return { call, events };
}
const operation = (): InstallOperation => ({
  id: "operation", stage: "awaiting", repo: "acme/example", tag: "v2", existing_name: null, created_at: "now", error: null,
  artifact: "file:///home/deck/homebrew/data/github-sideload/installations/id/plugin.zip", directory: "", request_id: null, saw_finish: false,
  binding_saved: false, backup: null, package: { name: "Example", folder: "example", version: "2.0.0", sha256: "a".repeat(64) },
});
describe("Loader bridge", () => {
  test("sends verified local artifact and checksum to native confirmation only", async () => {
    const { call } = setup();
    const op = operation();
    await installFromArtifact(op, InstallType.INSTALL);
    expect(call.mock.calls).toEqual([["utilities/install_plugin", op.artifact, "Example", "2.0.0", "a".repeat(64), 0]]);
  });
  test("rejects unprepared, remote or unhashed artifacts", async () => {
    const { call } = setup();
    for (const op of [{ ...operation(), stage: "ready" as const }, { ...operation(), artifact: "https://github.com/a.zip" }, { ...operation(), package: null }]) {
      await expect(installFromArtifact(op, InstallType.INSTALL)).rejects.toThrow();
    }
    expect(call).not.toHaveBeenCalled();
  });
  test("subscribes to real Loader event names and removes listeners", () => {
    const { events } = setup();
    const notify = mock();
    const stop = listenForInstallEvents(notify);
    events.get("loader/add_plugin_install_prompt")!("Example", "2.0.0", "req", "sha");
    expect(notify).toHaveBeenCalledWith("prompt", "Example", "req", "sha");
    stop();
    expect(events.size).toBe(0);
  });
  test("cancellation targets only the known request", async () => {
    const { call } = setup();
    await cancelPendingInstall("known-request");
    expect(call).toHaveBeenCalledWith("utilities/cancel_plugin_install", "known-request");
  });
  test("missing event interface blocks mutation", () => {
    globalThis.window = { DeckyBackend: { call: mock() } } as unknown as Window;
    expect(probeLoaderChannel().ok).toBe(false);
  });
});


test("reinstall route requires persisted claim and timeout never reports completion", async () => {
  const { call } = setup();
  await expect(uninstallForReinstall({ ...operation(), mode: "reinstall" })).rejects.toThrow();
  expect(call).not.toHaveBeenCalled();
  call.mockImplementationOnce(() => new Promise(() => {}));
  const originalTimeout = globalThis.setTimeout;
  let timeout!: () => void;
  globalThis.setTimeout = ((callback: () => void, milliseconds: number) => {
    expect(milliseconds).toBe(120000); timeout = callback; return undefined;
  }) as any;
  try {
    const pending = uninstallForReinstall({ ...operation(), stage: "uninstalling", mode: "reinstall" });
    timeout();
    await expect(pending).rejects.toThrow("超时");
    expect(call).toHaveBeenCalledWith("utilities/uninstall_plugin", "Example");
  } finally { globalThis.setTimeout = originalTimeout; }
});

test("management uses real no-argument route without mutation channel requirements", async () => {
  const { readLoaderPlugins } = await import("../src/loaderBridge");
  const call = mock(async () => [{ name: "Example", version: null, disabled: true, load_type: 1 }]);
  globalThis.window = { DeckyBackend: { call } } as unknown as Window;
  expect(await readLoaderPlugins()).toMatchObject({ ok: true });
  expect(call.mock.calls).toEqual([["loader/get_plugins"]]);
  globalThis.window = {} as Window;
  expect(await readLoaderPlugins()).toMatchObject({ ok: false, error: "Loader 管理接口不可用" });
});
test("management timeout and corrupt response stay failures; late resolution cannot replace snapshot", async () => {
  const { readLoaderPlugins } = await import("../src/loaderBridge");
  let resolve!: (value: unknown) => void;
  globalThis.window = { DeckyBackend: { call: () => new Promise((done) => { resolve = done; }) } } as unknown as Window;
  const result = await readLoaderPlugins(1);
  expect(result).toMatchObject({ ok: false });
  resolve([]);
  await Promise.resolve();
  expect(result).toMatchObject({ ok: false });
  for (const value of [undefined, [{ name: "broken" }]]) {
    globalThis.window = { DeckyBackend: { call: async () => value } } as unknown as Window;
    expect(await readLoaderPlugins()).toMatchObject({ ok: false });
  }
});

test("frozen bridge uses verified service with receiver binding and no guessed backend settings RPC", async () => {
  const { invalidateFrozenPlugins, updateFrozenPlugins } = await import("../src/loaderBridge");
  const { call } = setup();
  const service = {
    count: 0,
    async invalidate() { this.count++; },
    async update(names: string[]) { this.count++; expect(names).toEqual(["One", "Other"]); },
  };
  (window as any).DeckyPluginLoader.frozenPluginsService = service;
  await invalidateFrozenPlugins(); await updateFrozenPlugins(["One", "Other"]);
  expect(service.count).toBe(2);
  expect(call).not.toHaveBeenCalled();
  delete (window as any).DeckyPluginLoader.frozenPluginsService;
  await expect(updateFrozenPlugins([])).rejects.toThrow("冻结接口不可用");
  expect(call).not.toHaveBeenCalled();
});
