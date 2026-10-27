import { afterEach, beforeEach, expect, mock, test } from "bun:test";
import React, { act } from "react";
import { create, type ReactTestRenderer } from "react-test-renderer";
import type { AppSettings, CheckReport, InstallOperation, PluginListResponse, PluginRow, ReleaseRow, ReleaseReadResult, ReleaseSnapshot, RepositoryRow, RepositoryProbe, DiagnosticStatus, DiagnosticShare } from "../src/types";
import { setLocale } from "../src/i18n";

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
const eventHandlers = new Map<string, (...args: any[]) => void>();
const addBackendListener = mock((name: string, handler: (...args: any[]) => void) => { eventHandlers.set(name, handler); return handler; });
const removeBackendListener = mock((name: string) => { eventHandlers.delete(name); });
const toast = mock(() => undefined);
let nativeModal: React.ReactElement<any> | null = null;
let nativeModalClose: (() => void) | null = null;
const native = (name: string) => (props: any) => React.createElement(name, props,
  name === "Field" ? <>{props.icon}{props.label}{props.description}{props.children}</> : name === "TextField" ? props.inlineControls : props.children);
mock.module("@decky/ui", () => ({
  ...Object.fromEntries([
    "ButtonItem", "DialogButton", "Field", "PanelSection", "PanelSectionRow", "TextField", "Dropdown", "DropdownItem", "ToggleField", "Focusable", "ScrollPanel", "ProgressBar",
  ].map((name) => [name, native(name)])),
  DialogButtonPrimary: native("DialogButton"),
  gamepadDialogClasses: {},
  findClassByName: (name: string) => ({ ErrorText: "native-error", Yellow: "native-yellow" }[name]),
  ConfirmModal: native("ConfirmModal"),
  showModal: (modal: React.ReactElement, _parent: unknown, props?: { fnOnClose?: () => void }) => { nativeModal = modal; nativeModalClose = () => props?.fnOnClose?.(); return { Close: nativeModalClose }; },
  updaterFieldClasses: { UpdaterFieldStatusSuccess: "native-updater-success" },
  NavEntryPositionPreferences: { FIRST: 0, PREFERRED_CHILD: 4 }, staticClasses: { Title: "title" },
  Navigation: { NavigateToExternalWeb: mock(() => undefined) },
}));
mock.module("@decky/api", () => ({
  definePlugin: (factory: unknown) => factory,
  addEventListener: addBackendListener, removeEventListener: removeBackendListener,
  callable: () => async () => {},
  toaster: { toast },
}));

const plugin: PluginRow = { name: "Example", folder: "example", version: "1.0.0", author: "", repo: "acme/example", repo_source: "user", is_self: false, in_store: false, sideload: true, ownership: { state: "nonstore", eligible: true, intent: null, error: null } };
const release: ReleaseRow = { repo: "acme/example", tag: "v2", title: "v2", body: "Release notes", published_at: "", comparison: 1, prerelease: false, has_zip: true, zips: [{ name: "plugin.zip", url: "https://github.com/acme/example/releases/download/v2/plugin.zip" }] };
const repository: RepositoryRow = { repo: "community/example-fork", description: "Example for Decky", stars: 42, fork: true, archived: false };
let probe: RepositoryProbe;
let repositories: RepositoryRow[];
let listing: PluginListResponse;
let releases: ReleaseRow[];
let operation: InstallOperation | null;
let settings: AppSettings;
function releaseResult(subject = plugin, rows = releases): ReleaseReadResult {
  const snapshot: ReleaseSnapshot = { name: subject.name, folder: subject.folder, repo: subject.repo!, current: subject.version, ownership_id: subject.ownership?.intent?.id ?? null,
    checkedAt: "2026-10-25T00:00:00Z", releases: rows };
  listing = { ...listing, release_snapshots: [...(listing.release_snapshots ?? []).filter(item => item.name !== subject.name), snapshot] };
  return { snapshot, error: null };
}
const diagnosticExport: DiagnosticStatus = { state: "success", file: "diagnostics-1790000000000000000-" + "a".repeat(32) + ".zip", path: "/synthetic/Downloads/decky-sideloader-diagnostics/diagnostics-1790000000000000000-" + "a".repeat(32) + ".zip", bytes: 1234 };
const api = {
  beginStoreOwnership: mock(async () => ({ id: "a".repeat(32), name: "Example", folder: "example", repo: "acme/example", prior_frozen: false, action: "takeover", state: "prepared" })),
  ownershipNativeStep: mock(async () => ({ intent: { id: "a".repeat(32), name: "Example", folder: "example", repo: "acme/example", prior_frozen: false, action: "takeover", state: "active" }, frozen: [] as string[] })),
  authorizePluginMutation: mock(async () => undefined),
  recordDiagnosticEvents: mock(async (events: unknown[]) => ({ accepted: events.length })),
  getDiagnosticsStatus: mock(async (): Promise<DiagnosticStatus> => ({ state: "idle" })),
  exportDiagnostics: mock(async (): Promise<DiagnosticStatus> => diagnosticExport),
  getDiagnosticsAddresses: mock(async () => ({ addresses: ["192.168.2.5"] })),
  getDiagnosticsShare: mock(async (): Promise<DiagnosticShare> => ({ state: "idle" })),
  startDiagnosticsShare: mock(async (_address: string, session: string): Promise<DiagnosticShare> => ({ state: "active", session, package: diagnosticExport as any, url: "http://192.168.2.5:12345/pickup/" + "b".repeat(64), remaining: 300, downloads: 0 })),
  stopDiagnosticsShare: mock(async (session: string): Promise<DiagnosticShare> => ({ state: "stopped", session })),
  probeRepository: mock(async () => probe),
  searchRepositories: mock(async () => repositories),
  listSideloadPlugins: mock(async (_refresh?: boolean) => listing), listReleases: mock(async () => releases), checkUpdates: mock(async () => listing),
  getPluginReleases: mock(async (_repo: string, _current: string | null, name: string) => releaseResult(listing.plugins.find(p => p.name === name) ?? plugin)),
  getAppSettings: mock(async () => settings), setCheckNotifications: mock(async () => settings), setShowHomeTaskNotice: mock(async (enabled: boolean) => (settings = { ...settings, showHomeTaskNotice: enabled })), setDeletePackageAfterInstall: mock(async (enabled: boolean) => (settings = { ...settings, deletePackageAfterInstall: enabled })), setDownloadTimeout: mock(async (minutes: number) => (settings = { ...settings, downloadTimeoutMinutes: minutes })), setSkippedRelease: mock(async () => listing), setAutoCheck: mock(async () => settings), setNetworkMode: mock(async () => settings),
  getRepositoryMapStatus: mock(async () => ({ revision: 1, updated_at: "2026-10-27", count: 35, source: "bundled" as const, warning: null })),
  updateRepositoryMap: mock(async () => ({ revision: 2, updated_at: "2026-10-28", count: 36, source: "downloaded" as const, warning: null, changed: true })),
  getOpenSourceNotices: mock(async () => ({ entries: [{ id: "sample", name: "Sample", version: "1.0", group: "transitive", host: false,
    license: "MIT", source: "https://example.com", attribution: "Example", documents: [{ title: "LICENSE", text: "Copyright Example\n\nPermission granted.\n" }] }] })),
  setBinding: mock(async () => ({ name: "Example", repo: "acme/example" })), clearBinding: mock(async () => undefined),
  getOperation: mock(async () => operation), prepareInstall: mock(async () => operation!),
  claimUninstall: mock(async () => (operation = { ...operation!, stage: "uninstalling" })),
  completeUninstall: mock(async () => (operation = { ...operation!, stage: "awaiting" })),
  submitPrepared: mock(async () => (operation = { ...operation!, stage: "awaiting" })),
  dismissOperation: mock(async (_id: string) => (operation = { ...operation!, notice_dismissed: true })),
  deleteInstallPackage: mock(async (_id: string) => (operation = { ...operation!, artifact_deleted: true })),
  deleteBackups: mock(async () => ({ deleted: 0, bytes: 0, skipped: 0, failed: 0 })),
  cancelOperation: mock(async () => (operation = { ...operation!, cancel_requested: true, stage: ["ready", "checking", "backup", "validating"].includes(operation!.stage) ? "cancelled" : operation!.stage })),
  continuePrepared: mock(async () => operation!), retryDownload: mock(async () => operation!),
  recordInstallEvent: mock(async () => operation), closeOperation: mock(async () => (operation = { ...operation!, stage: "closed" })),
};
mock.module("../src/api", () => api);
const { Home, StorePlugins } = await import("../src/Home");
const { InstalledTab } = await import("../src/InstalledTab");
const { InstallTab } = await import("../src/InstallTab");
const { BindingEditor } = await import("../src/BindingEditor");
const { CheckResults } = await import("../src/CheckResults");
const { NetworkSettings } = await import("../src/NetworkSettings");
const { SettingsTab, resetShowHomeTaskNotice } = await import("../src/SettingsTab");
const { OperationPanel } = await import("../src/OperationPanel");
const controller = await import("../src/installController");
const checks = await import("../src/checkController");
const { resetViewState } = await import("../src/viewState");
const { Action } = await import("../src/components");
const { DiagnosticsSettings } = await import("../src/DiagnosticsSettings");
const { default: makePlugin } = await import("../src/index");
let roots: ReactTestRenderer[] = [];
let stop: (() => void) | null = null;
let loaderCall: ReturnType<typeof mock>;
const originalWindow = globalThis.window;
const makeOperation = (): InstallOperation => ({
  id: "job", stage: "checking", repo: "acme/example", tag: "v2", existing_name: null, created_at: "now", error: null,
  artifact: "file:///prepared/plugin.zip", directory: "/prepared", request_id: null, saw_finish: false, binding_saved: false, backup: null,
  package: { name: "Example", folder: "example", version: "2.0.0", sha256: "a".repeat(64), warnings: [] },
});
beforeEach(async () => {
  setLocale("zh-cn");
  api.authorizePluginMutation.mockReset().mockImplementation(async () => undefined);
  api.beginStoreOwnership.mockReset().mockImplementation(async () => ({ id: "a".repeat(32), name: "Example", folder: "example", repo: "acme/example", prior_frozen: false, action: "takeover", state: "prepared" }));
  api.ownershipNativeStep.mockReset().mockImplementation(async () => ({ intent: { id: "a".repeat(32), name: "Example", folder: "example", repo: "acme/example", prior_frozen: false, action: "takeover", state: "active" }, frozen: [] as string[] }));
  nativeModal = null; nativeModalClose = null;
  api.getDiagnosticsStatus.mockReset().mockImplementation(async () => ({ state: "idle" }));
  api.exportDiagnostics.mockReset().mockImplementation(async () => diagnosticExport);
  api.getDiagnosticsAddresses.mockReset().mockImplementation(async () => ({ addresses: ["192.168.2.5"] }));
  api.getDiagnosticsShare.mockReset().mockImplementation(async () => ({ state: "idle" }));
  api.startDiagnosticsShare.mockReset().mockImplementation(async (_address, session) => ({ state: "active", session, package: diagnosticExport as any, url: "http://192.168.2.5:12345/pickup/" + "b".repeat(64), remaining: 300, downloads: 0 }));
  api.stopDiagnosticsShare.mockReset().mockImplementation(async (session) => ({ state: "stopped", session }));
  api.getOperation.mockReset().mockImplementation(async () => operation);
  api.prepareInstall.mockReset().mockImplementation(async () => operation!);
  api.submitPrepared.mockReset().mockImplementation(async () => (operation = { ...operation!, stage: "awaiting" }));
  api.claimUninstall.mockReset().mockImplementation(async () => (operation = { ...operation!, stage: "uninstalling" }));
  api.completeUninstall.mockReset().mockImplementation(async () => (operation = { ...operation!, stage: "awaiting" }));
  api.dismissOperation.mockReset().mockImplementation(async () => (operation = { ...operation!, notice_dismissed: true }));
  api.cancelOperation.mockReset().mockImplementation(async () => (operation = { ...operation!, cancel_requested: true, stage: ["ready", "checking", "backup", "validating"].includes(operation!.stage) ? "cancelled" : operation!.stage }));
  api.recordInstallEvent.mockReset().mockImplementation(async (_id, kind) => {
    if (kind === "uninstall_started") operation = { ...operation!, uninstall_started: true };
    if (kind === "uninstall_not_started" || kind === "dispatch_not_started" || kind === "cancelled") operation = { ...operation!, stage: "cancelled" };
    if (kind === "cancel_unknown") operation = { ...operation!, stage: "unknown" };
    return operation;
  });
  checks.disposeReleaseReads();
  api.getPluginReleases.mockReset().mockImplementation(async (_repo, _current, name) => releaseResult(listing.plugins.find(p => p.name === name) ?? plugin));
  resetViewState();
  resetShowHomeTaskNotice();
  checks.confirmScheduledNotifications(true);
  toast.mockClear();
  api.setCheckNotifications.mockReset().mockImplementation(async () => settings);
  api.setShowHomeTaskNotice.mockReset().mockImplementation(async (enabled: boolean) => (settings = { ...settings, showHomeTaskNotice: enabled }));
  api.deleteBackups.mockReset().mockImplementation(async () => ({ deleted: 0, bytes: 0, skipped: 0, failed: 0 }));
  api.getRepositoryMapStatus.mockReset().mockImplementation(async () => ({ revision: 1, updated_at: "2026-10-27", count: 35, source: "bundled", warning: null }));
  api.updateRepositoryMap.mockReset().mockImplementation(async () => ({ revision: 2, updated_at: "2026-10-28", count: 36, source: "downloaded", warning: null, changed: true }));
  listing = { native_snapshot: { ok: true, plugins: [] }, plugins: [plugin], store_ok: true, store_error: null, last_check: null };
  releases = [release]; repositories = [repository]; operation = null;
  probe = { repo: "acme/example", tree_sha: "a".repeat(40), plugins: [{ path: "", name: "Example", package_name: "example", version: "1.0.0", evidence: ["依赖 @decky/api"], requires_root: false }] };
  api.checkUpdates.mockReset().mockImplementation(async () => listing);
  api.listSideloadPlugins.mockReset().mockImplementation(async () => listing);
  api.setNetworkMode.mockReset().mockImplementation(async () => settings);
  api.probeRepository.mockReset().mockImplementation(async () => probe);
  api.searchRepositories.mockReset().mockImplementation(async () => repositories);
  settings = { bindings: {}, autoCheck: false, autoCheckMinutes: 360, lastCheck: null };
  await act(async () => { await checks.runCheck(); await checks.refreshPlugins(); });
  for (const fn of Object.values(api)) fn.mockClear();
  loaderCall = mock(async () => undefined);
  globalThis.window = { DeckyBackend: { call: loaderCall, addEventListener() {}, removeEventListener() {} }, DeckyPluginLoader: { uninstallPlugin() {} } } as unknown as Window;
  await act(async () => { await controller.refreshOperation(); });
});
afterEach(async () => {
  await act(async () => { stop?.(); stop = null; for (const root of roots) root.unmount(); });
  roots = []; globalThis.window = originalWindow;
});
async function mount(element: React.ReactElement) {
  let root!: ReactTestRenderer;
  await act(async () => { root = create(element); });
  roots.push(root);
  return root;
}
async function unmount(root: ReactTestRenderer) {
  await act(async () => root.unmount());
  roots = roots.filter((item) => item !== root);
}
// Locate actions by accessible labels; SVG layout is covered independently below.
const buttons = (root: ReactTestRenderer) => [...root.root.findAllByType("DialogButton"), ...root.root.findAllByType("Field")]
  .filter((node) => node.props["data-action-key"])
  .map((node) => ({ props: { ...node.props, children: node.props["aria-label"] ?? node.props.children } }));
const navigation = (root: ReactTestRenderer) => root.root.findAllByType("Focusable").find((node) => node.props.onCancel)!;
const text = (root: ReactTestRenderer) => {
  const visit = (node: any): string => typeof node === "string" ? node : Array.isArray(node) ? node.map(visit).join(" ") : visit(node?.children ?? []);
  return visit(root.toJSON());
};


test("root access notice follows the selected new-install project and stays out of installed details", async () => {
  const { t } = await import("../src/i18n");
  listing.plugins = [];
  const regular = { ...probe.plugins[0], path: "regular" };
  probe = { ...probe, plugins: [{ ...regular, path: "privileged", name: "Privileged", requires_root: true }, regular] };
  const props = { onExisting() {}, onOperation() {} };
  const root = await mount(<InstallTab {...props} />);
  await act(async () => root.root.findByType("TextField").props.onChange({ target: { value: "acme/example" } }));
  await act(async () => { await buttons(root).find(button => button.props.children === "读取仓库")!.props.onClick(); });
  await act(async () => root.root.findByType("DropdownItem").props.onChange({ data: "privileged" }));
  await act(async () => { await buttons(root).find(button => button.props.children === "读取版本")!.props.onClick(); });
  expect(text(root)).toContain(t("此插件可以完全访问你的 Steam Deck。"));
  await unmount(root);
  const reopened = await mount(<InstallTab {...props} />);
  expect(text(reopened)).toContain(t("此插件可以完全访问你的 Steam Deck。"));
  await act(async () => buttons(reopened).find(button => button.props.children === "更换插件")!.props.onClick());
  await act(async () => reopened.root.findByType("DropdownItem").props.onChange({ data: "regular" }));
  await act(async () => { await buttons(reopened).find(button => button.props.children === "读取版本")!.props.onClick(); });
  expect(text(reopened)).not.toContain(t("此插件可以完全访问你的 Steam Deck。"));
  await unmount(reopened);
  listing.plugins = [plugin];
  await act(async () => { await checks.refreshPlugins(); });
  const installed = await mount(<InstalledTab plugin={plugin} onBinding={() => {}} onOperation={() => {}} />);
  expect(text(installed)).not.toContain(t("此插件可以完全访问你的 Steam Deck。"));
});

test("standalone uninstall clears the removed target after completion across QAM remount", async () => {
  const { getViewState, updateViewState, createBindingDraft } = await import("../src/viewState");
  updateViewState(current => ({ ...current, page: "maintenance", plugin,
    detailSelection: { key: "old", tag: "v2", zip: "example.zip" }, bindingDraft: createBindingDraft(plugin) }));
  const definition = makePlugin(); stop = definition.onDismount!;
  const root = await mount(definition.content as React.ReactElement);
  await act(async () => { await controller.requestUninstall(plugin.name); });
  expect(getViewState().page).toBe("maintenance");
  let finish!: () => void;
  loaderCall.mockImplementationOnce(() => new Promise<void>(resolve => { finish = resolve; }));
  const pending = (window as any).DeckyBackend.call("utilities/uninstall_plugin", plugin.name);
  await unmount(root);
  expect(getViewState().plugin).toBe(plugin);
  listing = { ...listing, plugins: [] };
  await act(async () => { finish(); await pending; });
  expect(getViewState()).toMatchObject({ page: "home", plugin: null,
    detailSelection: { key: "", tag: null, zip: null }, bindingDraft: { key: "", value: "" } });
  const reopened = await mount(definition.content as React.ReactElement);
  expect(reopened.root.findAllByType("Field").some(row => row.props.label === plugin.name)).toBe(false);
  expect(text(reopened)).not.toContain("身份已变化");
  expect(text(reopened)).not.toContain("读取失败");
});

test("standalone uninstall rejects stale lists and does not redirect a newer selection", async () => {
  const { getViewState, updateViewState } = await import("../src/viewState");
  updateViewState(current => ({ ...current, page: "maintenance", plugin }));
  stop = controller.startInstallController();
  await controller.refreshOperation();
  const stale = listing;
  let finishRead!: (value: PluginListResponse) => void;
  api.listSideloadPlugins.mockImplementationOnce(() => new Promise(resolve => { finishRead = resolve; }));
  const reading = checks.refreshPlugins();
  await act(async () => { await Promise.resolve(); });
  await (window as any).DeckyBackend.call("utilities/uninstall_plugin", plugin.name);
  const other = { ...plugin, name: "Other", folder: "other" };
  updateViewState(current => ({ ...current, page: "details", plugin: other }));
  listing = { ...listing, plugins: [other] };
  await act(async () => { finishRead(stale); await reading; });
  expect(getViewState()).toMatchObject({ page: "details", plugin: other });
  const root = await mount(<Home selected={null} onOpen={() => {}} />);
  expect(root.root.findAllByType("Field").some(row => row.props.label === plugin.name)).toBe(false);
  expect(root.root.findAllByType("Field").some(row => row.props.label === other.name)).toBe(true);
});

test("standalone uninstall failure or a still-present target preserves its maintenance page", async () => {
  const { getViewState, updateViewState } = await import("../src/viewState");
  updateViewState(current => ({ ...current, page: "maintenance", plugin }));
  stop = controller.startInstallController();
  await controller.refreshOperation();
  loaderCall.mockRejectedValueOnce(new Error("disk failure"));
  await expect((window as any).DeckyBackend.call("utilities/uninstall_plugin", plugin.name)).rejects.toThrow("disk failure");
  expect(getViewState()).toMatchObject({ page: "maintenance", plugin });
  await act(async () => { await (window as any).DeckyBackend.call("utilities/uninstall_plugin", plugin.name); });
  expect(getViewState()).toMatchObject({ page: "maintenance", plugin });
});

test("reinstall removal does not discard the selected target before its installation", async () => {
  const { getViewState, updateViewState } = await import("../src/viewState");
  updateViewState(current => ({ ...current, page: "operation", plugin, operationOrigin: "details" }));
  operation = { ...makeOperation(), mode: "reinstall", stage: "uninstalling" };
  stop = controller.startInstallController();
  await controller.refreshOperation();
  listing = { ...listing, plugins: [] };
  await act(async () => { await (window as any).DeckyBackend.call("utilities/uninstall_plugin", plugin.name); });
  expect(getViewState()).toMatchObject({ page: "operation", plugin, operationOrigin: "details" });
  expect(operation.stage).toBe("uninstalling");
});

test("page navigation consumes native B and requests native focus for the new page", async () => {
  const definition = makePlugin();
  stop = definition.onDismount!;
  const root = await mount(definition.content as React.ReactElement);
  const shell = navigation(root);
  await act(async () => root.root.findAllByType("Field").find((item) => item.props.label === "Example")!.props.onActivate());
  const cancel = { preventDefault: mock(), stopPropagation: mock() };
  await act(async () => navigation(root).props.onCancel(cancel));
  expect(cancel.preventDefault).toHaveBeenCalledTimes(1);
  expect(cancel.stopPropagation).toHaveBeenCalledTimes(1);
  const returnedShell = navigation(root);
  expect(returnedShell).not.toBe(shell);
  expect(returnedShell.props.autoFocus).toBe(true);
  expect(root.root.findAllByType("Field").find((item) => item.props.label === "Example")!.props.preferredFocus).toBe(true);
});

for (const [comparison, skipped] of [[0, false], [0, true], [1, false], [-1, false], [null, false]] as const) {
  test(`version skip is unavailable for the installed selection: comparison=${comparison}, skipped=${skipped}`, async () => {
    const subject = { ...plugin, skipped_tags: skipped ? [release.tag] : [] };
    releases = [{ ...release, comparison }];
    listing = { ...listing, plugins: [subject] };
    releaseResult(subject);
    await act(async () => checks.refreshPlugins());
    const root = await mount(<InstalledTab plugin={subject} onBinding={() => {}} onOperation={() => {}} />);
    expect(buttons(root).some(button => button.props["data-action-key"] === "skipRelease")).toBe(comparison !== 0);
  });
}

for (const status of ["current", "error"] as const) {
  test(`detail completion after returning home publishes ${status} without losing another plugin`, async () => {
    const other = { ...plugin, name: "Another", folder: "another", repo: "acme/another" };
    listing = { ...listing, plugins: [plugin, other], last_check: { checkedAt: "2026-10-23T12:00:00Z", items: [{ name: other.name, repo: other.repo, current: other.version, latest: "2.0.0", status: "update", error: null }] } };
    const { promise, resolve: finish, reject: fail } = Promise.withResolvers<ReleaseReadResult>();
    api.getPluginReleases.mockImplementationOnce(() => promise);
    const definition = makePlugin(); stop = definition.onDismount!;
    const root = await mount(definition.content as React.ReactElement);
    await act(async () => root.root.findAllByType("Field").find(row => row.props.label === plugin.name)!.props.onActivate());
    await act(async () => navigation(root).props.onCancel({ preventDefault() {}, stopPropagation() {} }));
    await act(async () => {
      listing = { ...listing, last_check: { checkedAt: "2026-10-23T13:00:00Z", items: [...listing.last_check!.items, { name: plugin.name, repo: plugin.repo, current: plugin.version, latest: status === "current" ? plugin.version : null, status, error: status === "error" ? "offline" : null }] } };
      if (status === "error") fail(new Error("offline")); else finish(releaseResult());
    });
    const row = root.root.findAllByType("Field").find(row => row.props.label === plugin.name)!;
    expect(row.findAllByType("span").some(node => node.props.className === (status === "current" ? "native-updater-success" : "native-error"))).toBe(true);
    expect(text(root)).toContain("2.0.0");
  });
}

test("home rows are single focus targets with a preferred return location", async () => {
  const open = mock();
  const root = await mount(<Home selected="example" onOpen={open} onInstall={() => {}} onSettings={() => {}} />);
  const row = root.root.findAllByType("Field").find((item) => item.props.label === "Example")!;
  expect(row.props.preferredFocus).toBe(true);
  expect(row.findAllByType("ButtonItem")).toHaveLength(0);
  await act(async () => row.props.onActivate());
  expect(open).toHaveBeenCalledWith(plugin);
  expect(text(root)).toContain("尚未检查更新");
});

test("store failure is not rendered as an empty plugin list", async () => {
  listing = { native_snapshot: { ok: true, plugins: [] }, plugins: [], store_ok: false, store_error: "offline", last_check: null };
  const root = await mount(<Home selected={null} onOpen={() => {}} />);
  expect(text(root)).not.toContain("来源识别失败");
  expect(text(root)).not.toContain("尚无可管理的插件");
});


test("new install preview blocks a latest release without assets", async () => {
  listing.plugins = [];
  releases = [{ ...release, zips: [], has_zip: false }, { ...release, tag: "v1" }];
  const root = await mount(<InstallTab onExisting={() => {}} onOperation={() => {}} />);
  await act(async () => root.root.findByType("TextField").props.onChange({ target: { value: "acme/example" } }));
  await act(async () => { await buttons(root).find((b) => b.props.children === "读取仓库")!.props.onClick(); });
  expect(root.root.findByType("DropdownItem").props.selectedOption).toBe("v2");
  expect(buttons(root).find((b) => b.props.children === "继续安装")!.props.disabled).toBe(true);
});

test("Loader Python exceptions keep their server message visible in install preview", async () => {
  listing.plugins = [];
  const error = Object.assign(new Error(), {
    name: "Python Exception",
    pythonTraceback: "Traceback (most recent call last):\n  File \"loader.py\", line 44\nException: GitHub 请求已限流，请稍后重试\n",
  });
  api.listReleases.mockRejectedValueOnce(error);
  const root = await mount(<InstallTab onExisting={() => {}} onOperation={() => {}} />);
  await act(async () => root.root.findByType("TextField").props.onChange({ target: { value: "acme/example" } }));
  await act(async () => { await buttons(root).find((b) => b.props.children === "读取仓库")!.props.onClick(); });
  expect(text(root)).toContain("操作失败");
  expect(text(root)).toContain("GitHub 请求已限流，请稍后重试");
  expect(text(root)).not.toContain("loader.py");
});

test("error text preserves ordinary messages and never returns a blank error", async () => {
  const { errorText } = await import("../src/errors");
  expect(errorText(new Error("offline"))).toBe("offline");
  expect(errorText("read failed")).toBe("read failed");
  expect(errorText(new Error())).toBe("操作失败，请重试");
});

test("historical version choice survives QAM remount", async () => {
  releases = [release, { ...release, tag: "v0.9.0", comparison: -1 }];
  const props = { plugin, section: "versions" as const, onBinding() {}, onOperation() {} };
  const first = await mount(<InstalledTab {...props} />);
  const select = first.root.findAllByType("Field").find(node => node.props["data-release-tag"] === "v0.9.0")!.props.onActivate;
  await unmount(first);
  const reopened = await mount(<InstalledTab {...props} />);
  await act(async () => select());
  expect(reopened.root.findAllByType("Field").find(node => node.props["aria-current"])!.props["data-release-tag"]).toBe("v0.9.0");
  await unmount(reopened);
  const again = await mount(<InstalledTab {...props} />);
  expect(again.root.findAllByType("Field").find(node => node.props["aria-current"])!.props["data-release-tag"]).toBe("v0.9.0");
});

test("missing remembered release does not silently select the latest", async () => {
  releases = [release, { ...release, tag: "v0.9.0", comparison: -1 }];
  const props = { plugin, section: "versions" as const, onBinding() {}, onOperation() {} };
  const first = await mount(<InstalledTab {...props} />);
  await act(async () => first.root.findAllByType("Field").find(node => node.props["data-release-tag"] === "v0.9.0")!.props.onActivate());
  await unmount(first);
  releases = [release];
  const reopened = await mount(<InstalledTab {...props} />);
  await act(async () => { await checks.readPluginReleases(plugin, true); });
  expect(reopened.root.findAllByType("Field").some(node => node.props["aria-current"])).toBe(false);
  expect(buttons(reopened).some((b) => b.props.children === "更新到 v2")).toBe(false);
});

test("changed installed identity resets the remembered version choice", async () => {
  releases = [release, { ...release, tag: "v0.9.0", comparison: -1 }];
  const first = await mount(<InstalledTab section="versions" plugin={plugin} onBinding={() => {}} onOperation={() => {}} />);
  await act(async () => first.root.findAllByType("Field").find(node => node.props["data-release-tag"] === "v0.9.0")!.props.onActivate());
  await unmount(first);
  listing = { ...listing, plugins: [{ ...plugin, version: "1.5.0" }] };
  await act(async () => { await checks.refreshPlugins(); });
  const reopened = await mount(<InstalledTab section="versions" plugin={{ ...plugin, version: "1.5.0" }} onBinding={() => {}} onOperation={() => {}} />);
  expect(reopened.root.findAllByType("Field").find(node => node.props["aria-current"])!.props["data-release-tag"]).toBe("v2");
});

test("full-check snapshots serve overview, versions and maintenance without detail RPC", async () => {
  releaseResult();
  await act(async () => { await checks.runCheck(); });
  for (const section of ["overview", "versions", "maintenance", "overview"] as const) {
    const root = await mount(<InstalledTab section={section} plugin={plugin} onBinding={() => {}} onOperation={() => {}} />);
    expect(text(root)).toContain("v2");
    expect(root.root.findAllByType("Field").some(row => row.props["aria-busy"])).toBe(false);
    await unmount(root);
  }
  expect(api.getPluginReleases).not.toHaveBeenCalled();
});

test("release download totals follow version navigation and survive remount without fetching", async () => {
  releases = [{ ...release, download_count: 1234 }, { ...release, tag: "v0.9.0", comparison: -1, download_count: 56 }];
  releaseResult();
  await act(async () => { await checks.runCheck(); });
  const definition = makePlugin(); stop = definition.onDismount!;
  const root = await mount(definition.content as React.ReactElement);
  await act(async () => root.root.findAllByType("Field").find(row => row.props.label === plugin.name)!.props.onActivate());
  expect(text(root)).toContain("1,234");
  await act(async () => buttons(root).find(button => button.props["data-action-key"] === "readReleases")!.props.onClick());
  await act(async () => root.root.findAllByType("Field").find(row => row.props["data-release-tag"] === "v0.9.0")!.props.onActivate());
  expect(text(root)).toContain("版本下载量：56");
  expect(text(root)).not.toContain("1,234");
  await unmount(root);
  const reopened = await mount(definition.content as React.ReactElement);
  expect(text(reopened)).toContain("版本下载量：56");
  expect(api.getPluginReleases).not.toHaveBeenCalled();
  expect(api.listReleases).not.toHaveBeenCalled();
});

test("old cached download totals remain unknown until refresh and failed refresh retains a known zero", async () => {
  releaseResult();
  await act(async () => { await checks.runCheck(); });
  const root = await mount(<InstalledTab plugin={plugin} onBinding={() => {}} onOperation={() => {}} />);
  expect(text(root)).toContain("版本下载量：未知");
  expect(api.getPluginReleases).not.toHaveBeenCalled();
  releases = [{ ...release, download_count: 0 }];
  await act(async () => { await checks.readPluginReleases(plugin, true); });
  expect(text(root)).toContain("版本下载量：0");
  api.getPluginReleases.mockRejectedValueOnce(new Error("offline"));
  await act(async () => { await checks.readPluginReleases(plugin, true); });
  expect(text(root)).toContain("offline");
  expect(text(root)).toContain("版本下载量：0");
  expect(api.getPluginReleases).toHaveBeenCalledTimes(2);
});

test("cold detail reads coalesce across navigation and retain the completed release", async () => {
  const gate = Promise.withResolvers<ReleaseReadResult>();
  api.getPluginReleases.mockImplementationOnce(() => gate.promise);
  const first = await mount(<InstalledTab plugin={plugin} onBinding={() => {}} onOperation={() => {}} />);
  await unmount(first);
  const next = await mount(<InstalledTab section="versions" plugin={plugin} onBinding={() => {}} onOperation={() => {}} />);
  expect(api.getPluginReleases).toHaveBeenCalledTimes(1);
  await act(async () => gate.resolve(releaseResult()));
  expect(next.root.findAllByType("Field").some(row => row.props["data-release-tag"] === "v2")).toBe(true);
  await unmount(next);
  await mount(<InstalledTab plugin={plugin} onBinding={() => {}} onOperation={() => {}} />);
  expect(api.getPluginReleases).toHaveBeenCalledTimes(1);
});

test("failed refresh retains release and timestamp through local reads and remount", async () => {
  const first = await mount(<InstalledTab plugin={plugin} onBinding={() => {}} onOperation={() => {}} />);
  const stamp = listing.release_snapshots![0].checkedAt;
  api.getPluginReleases.mockRejectedValueOnce(new Error("connection lost"));
  await act(async () => { await checks.readPluginReleases(plugin, true); await checks.refreshPlugins(); });
  expect(text(first)).toContain("connection lost");
  expect(buttons(first).some(button => button.props.children === "更新到 v2")).toBe(true);
  await unmount(first);
  const reopened = await mount(<InstalledTab plugin={plugin} onBinding={() => {}} onOperation={() => {}} />);
  expect(text(reopened)).toContain("connection lost");
  expect(text(reopened)).toContain(new Date(stamp).toLocaleString());
  expect(api.getPluginReleases).toHaveBeenCalledTimes(2);
  await act(async () => { await checks.readPluginReleases(plugin, true); });
  expect(text(reopened)).not.toContain("connection lost");
});

test("cold failure does not retry on remount or invent a success", async () => {
  api.getPluginReleases.mockRejectedValueOnce(new Error("offline"));
  const first = await mount(<InstalledTab plugin={plugin} onBinding={() => {}} onOperation={() => {}} />);
  await unmount(first);
  const reopened = await mount(<InstalledTab plugin={plugin} onBinding={() => {}} onOperation={() => {}} />);
  expect(text(reopened)).toContain("offline");
  expect(buttons(reopened).some(button => button.props["data-action-key"] === "replace")).toBe(false);
  expect(api.getPluginReleases).toHaveBeenCalledTimes(1);
});

test("late old-identity read cannot replace current successful snapshot", async () => {
  const gate = Promise.withResolvers<ReleaseReadResult>();
  const old = releaseResult();
  listing = { ...listing, release_snapshots: [] };
  api.getPluginReleases.mockImplementationOnce(() => gate.promise);
  const first = await mount(<InstalledTab plugin={plugin} onBinding={() => {}} onOperation={() => {}} />);
  await unmount(first);
  const changed = { ...plugin, version: "2.0.0" };
  listing = { ...listing, plugins: [changed] };
  releaseResult(changed, [{ ...release, tag: "v3" }]);
  await act(async () => { await checks.refreshPlugins(); gate.resolve(old); });
  const reopened = await mount(<InstalledTab plugin={changed} onBinding={() => {}} onOperation={() => {}} />);
  expect(buttons(reopened).some(button => button.props.children === "更新到 v3")).toBe(true);
  expect(text(reopened)).not.toContain("更新到 v2");
});

test("new-install draft and preview survive QAM popup remount", async () => {
  listing.plugins = [];
  releases = [release, { ...release, tag: "v0.9.0" }];
  const props = { onExisting() {}, onOperation() {} };
  const first = await mount(<InstallTab {...props} />);
  await act(async () => first.root.findByType("TextField").props.onChange({ target: { value: "acme/example" } }));
  await unmount(first);
  const reopened = await mount(<InstallTab {...props} />);
  expect(reopened.root.findByType("TextField").props.value).toBe("acme/example");
  await act(async () => { await buttons(reopened).find((b) => b.props.children === "读取仓库")!.props.onClick(); });
  const select = reopened.root.findByType("DropdownItem").props.onChange;
  await unmount(reopened);
  const preview = await mount(<InstallTab {...props} />);
  await act(async () => select({ data: "v0.9.0" }));
  expect(preview.root.findByType("DropdownItem").props.selectedOption).toBe("v0.9.0");
});

test("binding draft survives keyboard remount without leaking to another plugin", async () => {
  const first = await mount(<BindingEditor plugin={plugin} onSaved={() => {}} />);
  await act(async () => buttons(first).find((b) => b.props.children === "直接填写仓库")!.props.onClick());
  await act(async () => first.root.findByType("TextField").props.onChange({ target: { value: "acme/new-repo" } }));
  await unmount(first);
  const reopened = await mount(<BindingEditor plugin={plugin} onSaved={() => {}} />);
  expect(reopened.root.findByType("TextField").props.value).toBe("acme/new-repo");
  await unmount(reopened);
  const other = await mount(<BindingEditor plugin={{ ...plugin, folder: "other", repo: "acme/other" }} onSaved={() => {}} />);
  await act(async () => buttons(other).find((b) => b.props.children === "直接填写仓库")!.props.onClick());
  expect(other.root.findByType("TextField").props.value).toBe("acme/other");
});

test("repository search defaults to the plugin name and only saves after confirmation", async () => {
  const saved = mock();
  const root = await mount(<BindingEditor plugin={plugin} onSaved={saved} />);
  expect(root.root.findByType("TextField").props.value).toBe("Example");
  await act(async () => buttons(root).find((b) => b.props.children === "搜索仓库")!.props.onClick());
  expect(api.searchRepositories).toHaveBeenCalledWith("Example");
  expect(text(root)).toContain("42");
  expect(text(root)).toContain("Fork");
  await act(async () => root.root.findAllByType("Field").find((r) => r.props.label === repository.repo)!.props.onActivate());
  expect(api.setBinding).not.toHaveBeenCalled();
  await act(async () => buttons(root).find((b) => b.props.children === "保存关联")!.props.onClick());
  expect(api.setBinding).toHaveBeenCalledWith("Example", repository.repo);
  expect(saved).toHaveBeenCalledTimes(1);
});

test("repository results and selection survive QAM remount and an old row callback", async () => {
  const props = { plugin, onSaved() {} };
  const first = await mount(<BindingEditor {...props} />);
  await act(async () => buttons(first).find((b) => b.props.children === "搜索仓库")!.props.onClick());
  const select = first.root.findAllByType("Field").find((r) => r.props.label === repository.repo)!.props.onActivate;
  await unmount(first);
  const reopened = await mount(<BindingEditor {...props} />);
  expect(reopened.root.findAllByType("Field").some((r) => r.props.label === repository.repo)).toBe(true);
  await act(async () => select());
  expect(text(reopened)).toContain("已选择仓库");
  expect(text(reopened)).toContain(repository.repo);
  expect(api.searchRepositories).toHaveBeenCalledTimes(1);
});

test("late repository search cannot overwrite results for edited keywords", async () => {
  let resolveOld!: (rows: RepositoryRow[]) => void;
  api.searchRepositories.mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }));
  const root = await mount(<BindingEditor plugin={plugin} onSaved={() => {}} />);
  await act(async () => { buttons(root).find((b) => b.props.children === "搜索仓库")!.props.onClick(); });
  await act(async () => root.root.findByType("TextField").props.onChange({ target: { value: "new query" } }));
  const newer = { ...repository, repo: "acme/new-result" };
  repositories = [newer];
  await act(async () => buttons(root).find((b) => b.props.children === "搜索仓库")!.props.onClick());
  await act(async () => resolveOld([repository]));
  expect(text(root)).toContain(newer.repo);
  expect(text(root)).not.toContain(repository.repo);
});

test("repository search failure is visible after QAM remount and is not an empty result", async () => {
  let rejectSearch!: (error: unknown) => void;
  api.searchRepositories.mockImplementationOnce(() => new Promise((_, reject) => { rejectSearch = reject; }));
  const props = { plugin, onSaved() {} };
  const first = await mount(<BindingEditor {...props} />);
  await act(async () => { buttons(first).find((b) => b.props.children === "搜索仓库")!.props.onClick(); });
  await unmount(first);
  const reopened = await mount(<BindingEditor {...props} />);
  await act(async () => rejectSearch({ message: "", pythonTraceback: "RuntimeError: GitHub 请求已限流" }));
  expect(text(reopened)).toContain("GitHub 请求已限流");
  expect(text(reopened)).not.toContain("未找到仓库");
});

test("empty repository search keeps manual entry available", async () => {
  repositories = [];
  const root = await mount(<BindingEditor plugin={plugin} onSaved={() => {}} />);
  await act(async () => buttons(root).find((b) => b.props.children === "搜索仓库")!.props.onClick());
  expect(text(root)).toContain("未找到仓库");
  await act(async () => buttons(root).find((b) => b.props.children === "直接填写仓库")!.props.onClick());
  expect(root.root.findByType("TextField").props.value).toBe(plugin.repo);
  expect(api.setBinding).not.toHaveBeenCalled();
});

test("manual entry ignores an abandoned search failure", async () => {
  let rejectSearch!: (error: unknown) => void;
  api.searchRepositories.mockImplementationOnce(() => new Promise((_, reject) => { rejectSearch = reject; }));
  const root = await mount(<BindingEditor plugin={plugin} onSaved={() => {}} />);
  await act(async () => { buttons(root).find((b) => b.props.children === "搜索仓库")!.props.onClick(); });
  await act(async () => buttons(root).find((b) => b.props.children === "直接填写仓库")!.props.onClick());
  await act(async () => rejectSearch(new Error("stale search failure")));
  expect(text(root)).not.toContain("stale search failure");
  expect(root.root.findByType("TextField").props.label).toBe("GitHub 仓库");
});

test("B returns from repository selection to remembered results without saving", async () => {
  const root = await mount(<BindingEditor plugin={plugin} onSaved={() => {}} />);
  await act(async () => buttons(root).find((b) => b.props.children === "搜索仓库")!.props.onClick());
  await act(async () => root.root.findAllByType("Field").find((r) => r.props.label === repository.repo)!.props.onActivate());
  const cancel = { preventDefault: mock(), stopPropagation: mock() };
  await act(async () => navigation(root).props.onCancel(cancel));
  expect(cancel.preventDefault).toHaveBeenCalledTimes(1);
  expect(root.root.findAllByType("Field").find((r) => r.props.label === repository.repo)!.props.preferredFocus).toBe(true);
  expect(api.setBinding).not.toHaveBeenCalled();
});

test("new-install result selection probes after remount and preserves explicit installation", async () => {
  listing.plugins = [];
  const props = { onExisting: mock(), onOperation: mock() };
  const first = await mount(<InstallTab {...props} />);
  await act(async () => first.root.findByType("TextField").props.onChange({ target: { value: "Decky Example" } }));
  await act(async () => buttons(first).find((b) => b.props.children === "搜索仓库")!.props.onClick());
  const choose = first.root.findAllByType("Field").find((row) => row.props.label === repository.repo)!.props.onActivate;
  expect(api.probeRepository).not.toHaveBeenCalled();
  await unmount(first);
  probe = { ...probe, repo: repository.repo };
  releases = [{ ...release, repo: repository.repo }];
  const reopened = await mount(<InstallTab {...props} />);
  await act(async () => choose());
  expect(text(reopened)).toContain("安装预览");
  expect(api.prepareInstall).not.toHaveBeenCalled();
  await act(async () => buttons(reopened).find((b) => b.props.children === "更换仓库")!.props.onClick());
  expect(reopened.root.findByType("TextField").props.value).toBe("Decky Example");
  expect(reopened.root.findAllByType("Field").find((row) => row.props.label === repository.repo)!.props.preferredFocus).toBe(true);
  await act(async () => choose());
  operation = makeOperation();
  await act(async () => buttons(reopened).find((b) => b.props.children === "继续安装")!.props.onClick());
  expect(api.prepareInstall).toHaveBeenCalledWith(repository.repo, "v2", release.zips[0].url, null, "");
  expect(props.onOperation).toHaveBeenCalledTimes(1);
});

test.each([
  ["https://github.com/acme/example", true], ["github.com/acme/example", true], ["acme/example", true],
  ["git+https://github.com/acme/example.git", true], ["git@github.com:acme/example.git", true],
  [" Decky Example ", false], ["decky in:name fork:true", false], ["https://", false],
])("new-install Enter routes %s to the appropriate discovery flow", async (value, address) => {
  listing.plugins = [];
  const root = await mount(<InstallTab onExisting={() => {}} onOperation={() => {}} />);
  await act(async () => root.root.findByType("TextField").props.onChange({ target: { value } }));
  await act(async () => root.root.findByType("TextField").props.onKeyDown({ key: "Enter", currentTarget: { blur() {} }, nativeEvent: { isComposing: false }, repeat: false, preventDefault() {}, stopPropagation() {} }));
  if (address) {
    expect(text(root)).toContain("安装预览");
    expect(api.searchRepositories).not.toHaveBeenCalled();
  } else {
    expect(text(root)).toContain(repository.description);
    expect(api.probeRepository).not.toHaveBeenCalled();
  }
  expect(api.prepareInstall).not.toHaveBeenCalled();
});

test("a non-GitHub URL stays an address error rather than becoming a search", async () => {
  api.probeRepository.mockRejectedValueOnce(new Error("GitHub 链接无效"));
  const root = await mount(<InstallTab onExisting={() => {}} onOperation={() => {}} />);
  await act(async () => root.root.findByType("TextField").props.onChange({ target: { value: "https://example.com/acme/plugin" } }));
  await act(async () => buttons(root).find((b) => b.props.children === "读取仓库")!.props.onClick());
  expect(text(root)).toContain("GitHub 链接无效");
  expect(api.searchRepositories).not.toHaveBeenCalled();
  expect(api.listReleases).not.toHaveBeenCalled();
});

test("IME Enter and repeated submits do not launch duplicate repository searches", async () => {
  let finish!: (rows: RepositoryRow[]) => void;
  api.searchRepositories.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  const root = await mount(<InstallTab onExisting={() => {}} onOperation={() => {}} />);
  await act(async () => root.root.findByType("TextField").props.onChange({ target: { value: "Decky Example" } }));
  const enter = root.root.findByType("TextField").props.onKeyDown;
  const event = { key: "Enter", currentTarget: { blur() {} }, nativeEvent: { isComposing: false }, repeat: false, preventDefault() {}, stopPropagation() {} };
  await act(async () => { enter({ ...event, nativeEvent: { isComposing: true } }); enter({ ...event, repeat: true }); });
  expect(api.searchRepositories).not.toHaveBeenCalled();
  const click = buttons(root).find((b) => b.props.children === "搜索仓库")!.props.onClick;
  await act(async () => { enter(event); click(); enter(event); });
  expect(api.searchRepositories).toHaveBeenCalledTimes(1);
  await unmount(root);
  const reopened = await mount(<InstallTab onExisting={() => {}} onOperation={() => {}} />);
  await act(async () => finish([repository]));
  expect(text(reopened)).toContain(repository.description);
  expect(reopened.root.findByType("TextField").props.value).toBe("Decky Example");
});

test("new-install negative probe blocks releases and installation", async () => {
  probe.plugins = [];
  const root = await mount(<InstallTab onExisting={() => {}} onOperation={() => {}} />);
  await act(async () => root.root.findByType("TextField").props.onChange({ target: { value: "https://github.com/acme/example" } }));
  await act(async () => buttons(root).find((b) => b.props.children === "读取仓库")!.props.onClick());
  expect(api.probeRepository).toHaveBeenCalledWith("https://github.com/acme/example");
  expect(text(root)).toContain("未确认 Decky 插件");
  expect(api.listReleases).not.toHaveBeenCalled();
  expect(api.prepareInstall).not.toHaveBeenCalled();
});

test("monorepo requires explicit plugin selection and does not redirect to a different installed sibling", async () => {
  const existing = mock();
  probe.plugins = [{ ...probe.plugins[0], path: "plugins/example" }, { ...probe.plugins[0], path: "plugins/other", name: "Other", package_name: "other" }];
  const props = { onExisting: existing, onOperation() {} };
  const first = await mount(<InstallTab {...props} />);
  await act(async () => first.root.findByType("TextField").props.onChange({ target: { value: "acme/example" } }));
  await act(async () => buttons(first).find((b) => b.props.children === "读取仓库")!.props.onClick());
  expect(api.listReleases).not.toHaveBeenCalled();
  expect(first.root.findByType("DropdownItem").props.selectedOption).toBeNull();
  expect(buttons(first).find((b) => b.props.children === "读取版本")!.props.disabled).toBe(true);
  const choose = first.root.findByType("DropdownItem").props.onChange;
  await unmount(first);
  const reopened = await mount(<InstallTab {...props} />);
  await act(async () => choose({ data: "plugins/other" }));
  await act(async () => buttons(reopened).find((b) => b.props.children === "读取版本")!.props.onClick());
  expect(existing).not.toHaveBeenCalled();
  expect(text(reopened)).toContain("已识别：Other");
  operation = makeOperation();
  await act(async () => buttons(reopened).find((b) => b.props.children === "继续安装")!.props.onClick());
  expect(api.prepareInstall).toHaveBeenCalledWith("acme/example", "v2", release.zips[0].url, null, "plugins/other");
});

test("probe failure remains an error instead of declaring a non-plugin repository", async () => {
  api.probeRepository.mockRejectedValueOnce(new Error("GitHub 请求已限流"));
  const root = await mount(<InstallTab onExisting={() => {}} onOperation={() => {}} />);
  await act(async () => root.root.findByType("TextField").props.onChange({ target: { value: "acme/example" } }));
  await act(async () => buttons(root).find((b) => b.props.children === "读取仓库")!.props.onClick());
  expect(text(root)).toContain("GitHub 请求已限流");
  expect(text(root)).not.toContain("未确认 Decky 插件");
  expect(api.listReleases).not.toHaveBeenCalled();
});

test("late probe cannot overwrite a newer repository input", async () => {
  let resolveProbe!: (result: RepositoryProbe) => void;
  api.probeRepository.mockImplementationOnce(() => new Promise((resolve) => { resolveProbe = resolve; }));
  const root = await mount(<InstallTab onExisting={() => {}} onOperation={() => {}} />);
  await act(async () => root.root.findByType("TextField").props.onChange({ target: { value: "acme/example" } }));
  await act(async () => { buttons(root).find((b) => b.props.children === "读取仓库")!.props.onClick(); });
  await act(async () => root.root.findByType("TextField").props.onChange({ target: { value: "acme/new-repo" } }));
  await act(async () => resolveProbe(probe));
  expect(root.root.findByType("TextField").props.value).toBe("acme/new-repo");
  expect(text(root)).not.toContain("已识别");
  expect(api.listReleases).not.toHaveBeenCalled();
});

test("editing new-install keywords rejects stale results and stale row activation", async () => {
  let resolveSearch!: (rows: RepositoryRow[]) => void;
  const root = await mount(<InstallTab onExisting={() => {}} onOperation={() => {}} />);
  await act(async () => root.root.findByType("TextField").props.onChange({ target: { value: "Decky" } }));
  await act(async () => buttons(root).find((b) => b.props.children === "搜索仓库")!.props.onClick());
  const choose = root.root.findAllByType("Field").find((row) => row.props.label === repository.repo)!.props.onActivate;
  api.searchRepositories.mockImplementationOnce(() => new Promise((resolve) => { resolveSearch = resolve; }));
  await act(async () => { buttons(root).find((b) => b.props.children === "搜索仓库")!.props.onClick(); });
  await act(async () => root.root.findByType("TextField").props.onChange({ target: { value: "https://github.com/acme/new" } }));
  await act(async () => { resolveSearch([repository]); choose(); });
  expect(root.root.findByType("TextField").props.value).toBe("https://github.com/acme/new");
  expect(text(root)).not.toContain(repository.description);
  expect(api.probeRepository).not.toHaveBeenCalled();
});

test.each([false, true])("preview and installed detail return to retained search with either back control (installed=%s)", async (installed) => {
  listing.plugins = installed ? [plugin] : [];
  repositories = [{ ...repository, repo: plugin.repo! }];
  const definition = makePlugin(); stop = definition.onDismount!;
  const root = await mount(definition.content as React.ReactElement);
  await act(async () => buttons(root).find(b => b.props["data-action-key"] === "newInstall")!.props.onClick());
  await act(async () => root.root.findByType("TextField").props.onChange({ target: { value: "Decky Example" } }));
  await act(async () => buttons(root).find(b => b.props["data-action-key"] === "search")!.props.onClick());
  for (const viaButton of [true, false]) {
    await act(async () => root.root.findAllByType("Field").find(row => row.props.label === plugin.repo)!.props.onActivate());
    expect(root.root.findAllByType("TextField")).toHaveLength(0);
    await act(async () => viaButton ? buttons(root).find(b => b.props["data-action-key"] === "back")!.props.onClick()
      : navigation(root).props.onCancel({ preventDefault() {}, stopPropagation() {} }));
    expect(root.root.findByType("TextField").props.value).toBe("Decky Example");
    expect(root.root.findAllByType("Field").find(row => row.props.label === plugin.repo)!.props.preferredFocus).toBe(true);
  }
  expect(api.searchRepositories).toHaveBeenCalledTimes(1);
  expect(api.prepareInstall).not.toHaveBeenCalled();
});

test("returning while preview releases load rejects the stale completion", async () => {
  listing.plugins = [];
  let finish!: (rows: ReleaseRow[]) => void;
  api.listReleases.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  const definition = makePlugin(); stop = definition.onDismount!;
  const root = await mount(definition.content as React.ReactElement);
  await act(async () => buttons(root).find(b => b.props["data-action-key"] === "newInstall")!.props.onClick());
  await act(async () => root.root.findByType("TextField").props.onChange({ target: { value: "acme/example" } }));
  await act(async () => buttons(root).find(b => b.props["data-action-key"] === "search")!.props.onClick());
  await act(async () => buttons(root).find(b => b.props["data-action-key"] === "back")!.props.onClick());
  await act(async () => finish([release]));
  expect(root.root.findByType("TextField").props.value).toBe("acme/example");
  expect(root.root.findAllByType("DropdownItem")).toHaveLength(0);
});

test("installation shows preparation immediately then live download progress without losing search", async () => {
  listing.plugins = [];
  let finish!: (op: InstallOperation) => void;
  api.prepareInstall.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  const definition = makePlugin(); stop = definition.onDismount!;
  const root = await mount(definition.content as React.ReactElement);
  await act(async () => buttons(root).find(b => b.props["data-action-key"] === "newInstall")!.props.onClick());
  await act(async () => root.root.findByType("TextField").props.onChange({ target: { value: "acme/example" } }));
  await act(async () => buttons(root).find(b => b.props["data-action-key"] === "search")!.props.onClick());
  await act(async () => buttons(root).find(b => b.props["data-action-key"] === "installPackage")!.props.onClick());
  expect(root.root.findAllByType(OperationPanel)).toHaveLength(1);
  expect(root.root.findAllByType(InstallTab)).toHaveLength(0);
  operation = { ...makeOperation(), download: { status: "downloading", received: 1048576, total: 4194304, route: "direct", attempt: 1 } };
  await act(async () => finish(operation!));
  expect(root.root.findByType("ProgressBar").props.nProgress).toBe(25);
  operation = { ...operation, download: { ...operation.download!, received: 3145728 } };
  await act(async () => controller.refreshOperation());
  expect(root.root.findByType("ProgressBar").props.nProgress).toBe(75);
  await act(async () => buttons(root).find(b => b.props["data-action-key"] === "cancelRequest")!.props.onClick());
  expect(operation!.stage).toBe("cancelled");
  await act(async () => buttons(root).find(b => b.props["data-action-key"] === "back")!.props.onClick());
  expect(root.root.findAllByType(InstallTab)).toHaveLength(1);
  await act(async () => buttons(root).find(b => b.props["data-action-key"] === "back")!.props.onClick());
  expect(root.root.findByType("TextField").props.value).toBe("acme/example");
  expect(loaderCall).not.toHaveBeenCalled();
});

test.each([false, true])("completed installation refreshes details before B return despite an older list read (remount=%s)", async (remount) => {
  releaseResult();
  const definition = makePlugin(); stop = definition.onDismount!;
  let root = await mount(definition.content as React.ReactElement);
  await act(async () => root.root.findAllByType("Field").find(row => row.props.label === plugin.name)!.props.onActivate());
  operation = { ...makeOperation(), existing_name: plugin.name };
  await act(async () => buttons(root).find(button => button.props["data-action-key"] === "replace")!.props.onClick());
  const oldListing = listing;
  const gate = Promise.withResolvers<PluginListResponse>();
  api.listSideloadPlugins.mockImplementationOnce(() => gate.promise);
  let reading!: Promise<void>;
  await act(async () => { reading = checks.refreshPlugins(); });
  if (remount) await unmount(root);
  listing = { ...listing, plugins: [{ ...plugin, version: "2.0.0" }], release_snapshots: [] };
  releases = [{ ...release, comparison: 0 }];
  operation = { ...operation!, stage: "closed", outcome: "success", saw_start: true, saw_finish: true, binding_saved: true };
  await act(async () => controller.refreshOperation());
  if (remount) root = await mount(definition.content as React.ReactElement);
  await act(async () => { gate.resolve(oldListing); await reading; });
  await act(async () => navigation(root).props.onCancel({ preventDefault() {}, stopPropagation() {} }));
  expect(text(root)).toContain("2.0.0");
  expect(text(root)).not.toContain("1.0.0");
  expect(buttons(root).some(button => button.props["data-action-key"] === "replace")).toBe(false);
  expect(root.root.findAllByType(InstalledTab)).toHaveLength(1);
  expect(api.submitPrepared).not.toHaveBeenCalled();
});

test("probe completion after QAM remount updates the new component", async () => {
  listing.plugins = [];
  let resolveProbe!: (result: RepositoryProbe) => void;
  api.probeRepository.mockImplementationOnce(() => new Promise((resolve) => { resolveProbe = resolve; }));
  const props = { onExisting() {}, onOperation() {} };
  const first = await mount(<InstallTab {...props} />);
  await act(async () => first.root.findByType("TextField").props.onChange({ target: { value: "acme/example" } }));
  await act(async () => { buttons(first).find((b) => b.props.children === "读取仓库")!.props.onClick(); });
  await unmount(first);
  const reopened = await mount(<InstallTab {...props} />);
  await act(async () => resolveProbe(probe));
  expect(text(reopened)).toContain("已识别：Example");
});

test("old release popup cannot change a new-install preview for another repository", async () => {
  listing.plugins = [];
  releases = [release, { ...release, tag: "v0.9.0" }];
  const root = await mount(<InstallTab onExisting={() => {}} onOperation={() => {}} />);
  await act(async () => root.root.findByType("TextField").props.onChange({ target: { value: "acme/example" } }));
  await act(async () => buttons(root).find((b) => b.props.children === "读取仓库")!.props.onClick());
  const chooseOld = root.root.findByType("DropdownItem").props.onChange;
  await act(async () => buttons(root).find((b) => b.props.children === "更换仓库")!.props.onClick());
  await act(async () => root.root.findByType("TextField").props.onChange({ target: { value: "acme/new" } }));
  probe = { ...probe, repo: "acme/new" };
  releases = [{ ...release, repo: "acme/new" }];
  await act(async () => buttons(root).find((b) => b.props.children === "读取仓库")!.props.onClick());
  await act(async () => chooseOld({ data: "v0.9.0" }));
  expect(root.root.findByType("DropdownItem").props.selectedOption).toBe("v2");
});

test("known installed repository enters version management", async () => {
  const existing = mock();
  const root = await mount(<InstallTab onExisting={existing} onOperation={() => {}} />);
  await act(async () => root.root.findByType("TextField").props.onChange({ target: { value: "acme/example" } }));
  await act(async () => { await buttons(root).find((b) => b.props.children === "读取仓库")!.props.onClick(); });
  expect(existing).toHaveBeenCalledWith(plugin);
});

test("unlink only calls binding RPC", async () => {
  const saved = mock();
  const root = await mount(<BindingEditor plugin={plugin} onSaved={saved} />);
  await act(async () => { await buttons(root).find((b) => b.props.children === "解除关联")!.props.onClick(); });
  expect(api.clearBinding).toHaveBeenCalledWith("Example");
  expect(saved).toHaveBeenCalled();
  expect(loaderCall).not.toHaveBeenCalled();
});

test("settings preserve interval when enabling automatic checks", async () => {
  settings.autoCheckMinutes = 720;
  const root = await mount(<SettingsTab />);
  await act(async () => { await root.root.findAllByType("ToggleField").find((field) => field.props.label === "定期检查更新")!.props.onChange(true); });
  expect(api.setAutoCheck).toHaveBeenCalledWith(true, 720);
});

test("controller dispatches once after preparation, never replays on reload", async () => {
  stop = controller.startInstallController();
  await controller.refreshOperation();
  operation = makeOperation();
  await controller.startInstallation(release, release.zips[0].url, null);
  expect(loaderCall).not.toHaveBeenCalled();
  operation = { ...operation, stage: "ready" };
  await controller.refreshOperation();
  expect(loaderCall).toHaveBeenCalledTimes(1);
  expect(api.submitPrepared).toHaveBeenCalledTimes(1);
  await controller.refreshOperation();
  stop(); stop = controller.startInstallController();
  await controller.refreshOperation();
  expect(loaderCall).toHaveBeenCalledTimes(1);
});

test("reloaded ready operation requires a user action", async () => {
  operation = { ...makeOperation(), stage: "ready" };
  stop = controller.startInstallController();
  await controller.refreshOperation();
  expect(loaderCall).not.toHaveBeenCalled();
});

test("package warnings open a confirmation before submitting and preserve the selected install type", async () => {
  stop = controller.startInstallController();
  await controller.refreshOperation();
  operation = makeOperation();
  await controller.startInstallation(release, release.zips[0].url, plugin);
  operation = { ...operation, stage: "ready", package: { ...operation.package!, warnings: ["此插件声明了 root 权限，请仅安装可信作者的发布。", "安装包未附带 LICENSE，请自行核对作者的分发许可。"] } };
  await act(async () => { await controller.refreshOperation(); for (let i = 0; i < 12; i++) await Promise.resolve(); });
  expect(nativeModal!.props.strDescription).toContain("root 权限");
  expect(nativeModal!.props.strDescription).toContain("LICENSE");
  expect(api.submitPrepared).not.toHaveBeenCalled();
  expect(loaderCall).not.toHaveBeenCalled();
  await act(async () => { nativeModal!.props.onOK(); for (let i = 0; i < 20; i++) await Promise.resolve(); });
  expect(api.submitPrepared).toHaveBeenCalledTimes(1);
  expect(loaderCall).toHaveBeenCalledWith("utilities/install_plugin", operation!.artifact, "Example", "2.0.0", "a".repeat(64), 2);
});

test("dismissing package warning leaves the task ready and allows an explicit retry", async () => {
  stop = controller.startInstallController(); await controller.refreshOperation();
  operation = makeOperation(); await controller.startInstallation(release, release.zips[0].url, null);
  operation = { ...operation, stage: "ready", package: { ...operation.package!, warnings: ["requires root"] } };
  await act(async () => { await controller.refreshOperation(); for (let i = 0; i < 12; i++) await Promise.resolve(); });
  await act(async () => { nativeModal!.props.onCancel(); for (let i = 0; i < 12; i++) await Promise.resolve(); });
  expect(api.submitPrepared).not.toHaveBeenCalled();
  expect(loaderCall).not.toHaveBeenCalled();
  await controller.refreshOperation();
  const root = await mount(<OperationPanel />);
  const retry = buttons(root).find(button => button.props["data-action-key"] === "confirmInstall")!;
  await act(async () => { retry.props.onClick(); for (let i = 0; i < 12; i++) await Promise.resolve(); });
  expect(nativeModal!.props.strDescription).toContain("requires root");
  await act(async () => { nativeModal!.props.onOK(); for (let i = 0; i < 20; i++) await Promise.resolve(); });
  expect(api.submitPrepared).toHaveBeenCalledTimes(1);
  expect(loaderCall).toHaveBeenCalledTimes(1);
});

test("late package warning approval after cancellation or controller restart cannot install", async () => {
  for (const action of ["cancel", "restart"] as const) {
    stop = controller.startInstallController(); await controller.refreshOperation();
    operation = makeOperation(); await controller.startInstallation(release, release.zips[0].url, null);
    operation = { ...operation, stage: "ready", package: { ...operation.package!, warnings: ["requires root"] } };
    await act(async () => { await controller.refreshOperation(); for (let i = 0; i < 12; i++) await Promise.resolve(); });
    const approve = nativeModal!.props.onOK;
    if (action === "cancel") await controller.cancelCurrentOperation();
    else { stop(); stop = controller.startInstallController(); await controller.refreshOperation(); }
    await act(async () => { approve(); for (let i = 0; i < 20; i++) await Promise.resolve(); });
    expect(api.submitPrepared).not.toHaveBeenCalled();
    expect(loaderCall).not.toHaveBeenCalled();
    stop(); stop = null; operation = null;
  }
});

test("one check survives QAM remount and prevents reentry", async () => {
  const { runCheck } = await import("../src/checkController");
  let finish!: (value: PluginListResponse) => void;
  api.checkUpdates.mockImplementationOnce(() => new Promise<PluginListResponse>((resolve) => { finish = resolve; }));
  const props = { selected: null, onOpen: () => {}, onInstall: () => {}, onSettings: () => {} };
  const first = await mount(<Home {...props} />);
  const trigger = buttons(first).find((b) => b.props["data-action-key"] === "checkUpdates")!;
  await act(async () => trigger.props.onClick());
  const pending = runCheck();
  expect(runCheck()).toBe(pending);
  await unmount(first);
  const second = await mount(<Home {...props} />);
  expect(buttons(second).find((b) => b.props["data-action-key"] === "checkUpdates")!.props.disabled).toBe(true);
  await act(async () => { await trigger.props.onClick(); });
  expect(api.checkUpdates).toHaveBeenCalledTimes(1);
  await act(async () => { finish(listing); await pending; });
  expect(buttons(second).find((b) => b.props["data-action-key"] === "checkUpdates")!.props.disabled).toBe(false);
});

test("check failure after QAM remount always stops loading and retains error", async () => {
  const { runCheck } = await import("../src/checkController");
  let fail!: (reason: Error) => void;
  api.checkUpdates.mockImplementationOnce(() => new Promise<PluginListResponse>((_resolve, reject) => { fail = reject; }));
  const props = { selected: null, onOpen: () => {}, onInstall: () => {}, onSettings: () => {} };
  const first = await mount(<Home {...props} />);
  let pending!: Promise<void>;
  await act(async () => { pending = runCheck(); });
  await unmount(first);
  const second = await mount(<Home {...props} />);
  await act(async () => { fail(new Error("GitHub timeout")); await pending; });
  expect(text(second)).toContain("GitHub timeout");
  const action = buttons(second).find((b) => b.props["data-action-key"] === "checkUpdates")!;
  expect(action.props.disabled).toBe(false);
  expect(action.props["aria-busy"]).toBe(false);
});

test("native download bar uses actual known-length percentage and channel", async () => {
  operation = { ...makeOperation(), network_mode: "auto", download: { received: 262144, total: 1048576, status: "downloading", route: "gh-proxy", attempt: 2 } };
  await act(async () => controller.refreshOperation());
  const root = await mount(<OperationPanel />);
  const progress = root.root.findByType("ProgressBar");
  expect(progress.props.nProgress).toBe(25);
  expect(progress.props.indeterminate).toBe(false);
  expect(progress.props.focusable).toBe(false);
  expect(progress.props.nTransitionSec).toBe(0.2);
  const field = root.root.findAllByType("Field").find(node => node.findAllByType("ProgressBar").includes(progress))!;
  expect(field.props.childrenLayout).toBe("below");
  expect(field.props.focusable).toBe(false);
  expect(field.findAllByType("br")).toHaveLength(1);
  expect(text(root)).toContain("0.25 MiB / 1.00 MiB");
  expect(text(root)).toContain("GH-Proxy，第 2 次尝试");
  expect(text(root)).not.toContain("安装文件已核验");
});

test("dependency progress remains independent of completed main ZIP and stops on failure", async () => {
  operation = { ...makeOperation(), download: { received: 10, total: 10, status: "complete", route: "direct", attempt: 1 },
    dependency_download: { name: "engine.tar.gz", index: 1, count: 2, received: 25, total: 100, status: "downloading", route: "gh-proxy", attempt: 2 },
    observations: [{ id: "dependencies", label: "额外依赖准备", status: "running", updated_at: "now" }] };
  await act(async () => controller.refreshOperation());
  const root = await mount(<OperationPanel />);
  expect(root.root.findAllByType("ProgressBar").map(bar => bar.props.nProgress)).toEqual([100, 25]);
  operation = { ...operation, dependency_download: { ...operation.dependency_download!, total: null } };
  await act(async () => controller.refreshOperation());
  expect(root.root.findAllByType("ProgressBar")[1].props.indeterminate).toBe(true);
  operation = { ...operation, stage: "failed", error: "engine.tar.gz SHA-256 mismatch", dependency_download: { ...operation.dependency_download!, status: "failed" },
    observations: [{ id: "dependencies", label: "额外依赖准备", status: "failed", detail: "engine.tar.gz SHA-256 mismatch", updated_at: "now" }] };
  await act(async () => controller.refreshOperation());
  expect(root.root.findAllByType("ProgressBar").some(bar => bar.props.indeterminate)).toBe(false);
  expect(text(root)).toContain("engine.tar.gz SHA-256 mismatch");
  expect(buttons(root).some(button => button.props["data-action-key"] === "continueInstall")).toBe(true);
});

test("step dots stop on failed dependencies without activating later steps", async () => {
  operation = { ...makeOperation(), observations: [{ id: "download", label: "主安装包下载", status: "running", updated_at: "2026-10-25T10:00:00Z" }] };
  await act(async () => controller.refreshOperation());
  const root = await mount(<OperationPanel />);
  const active = () => root.root.findAllByType("li").filter(node => node.props["data-state"] !== "future").map(node => node.props["data-step"]);
  expect(active()).toEqual(["download"]);
  operation = { ...operation, observations: [
    { id: "download", label: "主安装包下载", status: "complete", updated_at: "2026-10-25T10:01:00Z" },
    { id: "dependencies", label: "额外依赖准备", status: "running", updated_at: "2026-10-25T10:02:00Z" },
  ] };
  await act(async () => controller.refreshOperation());
  expect(active()).toEqual(["download", "archive", "dependencies"]);
  const failed = { ...operation.observations![1], status: "failed" as const, updated_at: "2026-10-25T10:03:00Z" };
  operation = { ...operation, stage: "failed", error: "dependency hash mismatch", observations: [operation.observations![0], failed] };
  await act(async () => controller.refreshOperation());
  expect(active()).toEqual(["download", "archive", "dependencies"]);
  expect(root.root.findByType("ol").props["data-result"]).toBe("error");
  expect(text(root)).toContain("dependency hash mismatch");
});

test("manual closure and missing evidence do not activate the success step", async () => {
  operation = { ...makeOperation(), stage: "closed", close_reason: "manual", observations: [
    { id: "verification", label: "安装文件核验", status: "failed", updated_at: "2026-10-25T10:00:00Z" },
  ] };
  await act(async () => controller.refreshOperation());
  const root = await mount(<OperationPanel />);
  const state = (id: string) => root.root.findAllByType("li").find(node => node.props["data-step"] === id)!.props["data-state"];
  expect(state("verification")).toBe("current");
  expect(state("verified")).toBe("future");
  operation = { ...operation, outcome: "success", close_reason: "verified", binding_saved: true, saw_start: true, saw_finish: true };
  await act(async () => controller.refreshOperation());
  expect(state("verified")).toBe("current");
  expect(root.root.findByType("ol").props["data-result"]).toBe("success");
  operation = { ...makeOperation(), id: "new-task", stage: "unknown" };
  await act(async () => controller.refreshOperation());
  expect(root.root.findAllByType("li").every(node => node.props["data-state"] === "future")).toBe(true);
});

test("explicit reinstall places its unload step before native installation confirmation", async () => {
  operation = { ...makeOperation(), stage: "uninstalling", mode: "reinstall", uninstall_started: true };
  await act(async () => controller.refreshOperation());
  const root = await mount(<OperationPanel />);
  const current = () => root.root.findAllByType("li").find(node => node.props["aria-current"] === "step")!;
  expect(current().props["data-step"]).toBe("uninstalling");
  expect(root.root.findAllByType("li").filter(node => node.props["data-state"] !== "future").map(node => node.props["data-step"])).toEqual(["download", "archive", "dependencies", "backup", "uninstalling"]);
  operation = { ...operation, stage: "awaiting", uninstall_result_received: true };
  await act(async () => controller.refreshOperation());
  expect(current().props["data-step"]).toBe("awaiting");
});

test("unknown length is indeterminate while active, then stops with actual bytes on failure", async () => {
  operation = { ...makeOperation(), download: { received: 524288, total: null, status: "downloading", route: "direct", attempt: 1 } };
  await act(async () => controller.refreshOperation());
  const root = await mount(<OperationPanel />);
  let progress = root.root.findByType("ProgressBar");
  expect(progress.props.indeterminate).toBe(true);
  expect(progress.props.nProgress).toBe(0);
  expect(text(root)).toContain("0.50 MiB，总量未知");
  expect(text(root)).toContain("直连，第 1 次尝试");
  operation = { ...operation, stage: "failed", download: { ...operation.download!, status: "failed" }, error: "timeout" };
  await act(async () => controller.refreshOperation());
  expect(root.root.findAllByType("ProgressBar")).toHaveLength(0);
  expect(text(root)).toContain("0.50 MiB，总量未知");
  expect(text(root)).toContain("下载已停止");
});

test("old operations without download fields remain readable", async () => {
  operation = { ...makeOperation(), stage: "failed" };
  await act(async () => controller.refreshOperation());
  const root = await mount(<OperationPanel />);
  expect(root.root.findAllByType("ProgressBar")).toHaveLength(0);
});

test("finished update can delete its package without offering deletion while the zip is still required", async () => {
  operation = { ...makeOperation(), stage: "ready", download: { received: 10, total: 10, status: "complete", route: "direct", attempt: 1 } };
  await act(async () => controller.refreshOperation());
  let root = await mount(<OperationPanel />);
  expect(root.root.findAllByType("DialogButton").some((button) => button.props["data-action-key"] === "deletePackage")).toBe(false);
  operation = { ...operation, stage: "closed", outcome: "success", saw_finish: true, binding_saved: true };
  await act(async () => controller.refreshOperation());
  const button = root.root.findAllByType("DialogButton").find((item) => item.props["data-action-key"] === "deletePackage")!;
  expect(button.props["aria-label"]).toBe("删除安装包");
  await act(async () => button.props.onClick());
  expect(api.deleteInstallPackage).toHaveBeenCalledWith("job");
  expect(text(root)).toContain("安装包已删除");
  expect(root.root.findAllByType("DialogButton").some((item) => item.props["data-action-key"] === "deletePackage")).toBe(false);
});

test("package deletion setting defaults on and can be saved off", async () => {
  const root = await mount(<SettingsTab />);
  const toggle = root.root.findAllByType("ToggleField").find((field) => field.props.label === "安装完成后删除安装包")!;
  expect(toggle.props.checked).toBe(true);
  await act(async () => toggle.props.onChange(false));
  expect(api.setDeletePackageAfterInstall).toHaveBeenCalledWith(false);
  expect(toggle.props.checked).toBe(false);
});




test("unavailable catalog keeps unknown plugins visible and exposes source retry", async () => {
  listing = { native_snapshot: { ok: true, plugins: [] }, plugins: [{ ...plugin, sideload: false, repo_source: "package.json", in_store: null }], store_ok: false, store_error: "both offline", last_check: null,
    catalog: { source: "unavailable", timestamp: null, stale: false, warning: "both offline" } };
  const root = await mount(<Home selected={null} onOpen={() => {}} />);
  expect(text(root)).not.toContain("来源：完全不可用");
  expect(text(root)).not.toContain("商店来源未知");
  const settingsPage = await mount(<SettingsTab />);
  const retry = settingsPage.root.findAllByType("Field").find((b) => b.props["data-action-key"] === "refreshRecognition")!;
  expect(retry).toBeTruthy();
  api.listSideloadPlugins.mockClear();
  await act(async () => retry.props.onClick());
  expect(api.listSideloadPlugins).toHaveBeenCalledTimes(1);
});



for (const change of ["rebind", "unlink"] as const) {
  test(`checkController queues current listing after ${change} and repeated QAM remounts`, async () => {
    const oldReport = { checkedAt: "2026-10-15T00:00:00Z", items: [{ name: plugin.name, repo: plugin.repo, current: plugin.version, latest: "2.0.0", status: "update" as const, error: null }] };
    const oldListing = { ...listing, last_check: oldReport };
    let finish!: (value: PluginListResponse) => void;
    api.checkUpdates.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
    const definition = makePlugin();
    stop = definition.onDismount!;
    let root = await mount(definition.content as React.ReactElement);
    let pending!: Promise<void>;
    await act(async () => { pending = checks.runCheck(); });
    await act(async () => root.root.findAllByType("Field").find((row) => row.props.label === "Example")!.props.onActivate());
    await act(async () => buttons(root).find((b) => b.props["data-action-key"] === "maintenance")!.props.onClick());
    await act(async () => buttons(root).find((b) => b.props.children === "管理仓库关联")!.props.onClick());
    const nextPlugin = { ...plugin, repo: change === "rebind" ? "acme/rebound" : null };
    if (change === "rebind") {
      api.setBinding.mockImplementationOnce(async () => {
        listing = { ...listing, plugins: [nextPlugin] };
        return { name: plugin.name, repo: "acme/rebound" };
      });
      await act(async () => buttons(root).find((b) => b.props.children === "直接填写仓库")!.props.onClick());
      await act(async () => root.root.findByType("TextField").props.onChange({ target: { value: "acme/rebound" } }));
      await act(async () => buttons(root).find((b) => b.props.children === "保存关联")!.props.onClick());
    } else {
      api.clearBinding.mockImplementationOnce(async () => { listing = { ...listing, plugins: [nextPlugin] }; });
      await act(async () => buttons(root).find((b) => b.props.children === "解除关联")!.props.onClick());
    }
    await act(async () => navigation(root).props.onCancel({ preventDefault() {}, stopPropagation() {} }));
    const queued = checks.refreshPlugins();
    expect(checks.refreshPlugins()).toBe(queued);
    expect(queued).not.toBe(pending);
    api.listSideloadPlugins.mockClear();
    for (let i = 0; i < 3; i++) {
      await unmount(root);
      root = await mount(definition.content as React.ReactElement);
    }
    expect(api.listSideloadPlugins).not.toHaveBeenCalled();
    expect(api.checkUpdates).toHaveBeenCalledTimes(1);
    // The backend report is still from A; only a fresh list can show B/unbound.
    listing = { ...listing, last_check: oldReport };
    await act(async () => { finish(oldListing); await pending; await queued; });
    expect(api.listSideloadPlugins).toHaveBeenCalledTimes(1);
    api.getPluginReleases.mockClear();
    await act(async () => root.root.findAllByType("Field").find((row) => row.props.label === "Example")!.props.onActivate());
    expect(api.listSideloadPlugins.mock.calls.every(args => args[0] !== true)).toBe(true);
    if (change === "unlink") expect(api.getPluginReleases).not.toHaveBeenCalled();
  });
}

test("checkController retains failure completed while Home is unmounted through a successful list refresh", async () => {
  let fail!: (reason: Error) => void;
  api.checkUpdates.mockImplementationOnce(() => new Promise<PluginListResponse>((_resolve, reject) => { fail = reject; }));
  const props = { selected: null, onOpen() {}, onInstall() {}, onSettings() {} };
  const first = await mount(<Home {...props} />);
  let pending!: Promise<void>;
  await act(async () => { pending = checks.runCheck(); });
  await unmount(first);
  await act(async () => { fail(new Error("background check failed")); await pending; });
  api.listSideloadPlugins.mockClear();
  const reopened = await mount(<Home {...props} />);
  expect(api.listSideloadPlugins).toHaveBeenCalledTimes(1);
  expect(text(reopened)).toContain("background check failed");
  expect(buttons(reopened).find((b) => b.props["data-action-key"] === "checkUpdates")!.props.disabled).toBe(false);
  let finish!: (value: PluginListResponse) => void;
  api.checkUpdates.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  await act(async () => { pending = checks.runCheck(); });
  expect(text(reopened)).toContain("background check failed");
  await act(async () => { finish(listing); await pending; });
  expect(text(reopened)).not.toContain("background check failed");
});

test("checkController owns list and check errors separately", async () => {
  const root = await mount(<Home selected={null} onOpen={() => {}} />);
  api.listSideloadPlugins.mockRejectedValueOnce(new Error("listing failed"));
  await act(async () => checks.refreshPlugins());
  expect(text(root)).toContain("listing failed");
  api.checkUpdates.mockRejectedValueOnce(new Error("check failed"));
  await act(async () => checks.runCheck());
  expect(text(root)).toContain("check failed");
  await act(async () => checks.runCheck());
  expect(text(root)).not.toContain("check failed");
  expect(text(root)).toContain("listing failed");
  await act(async () => checks.refreshPlugins());
  expect(text(root)).not.toContain("listing failed");
});

test("checkController discards an older queued list without clearing a newer check or its finally state", async () => {
  const root = await mount(<Home selected={null} onOpen={() => {}} />);
  let finishFirst!: (value: PluginListResponse) => void;
  let finishSecond!: (value: PluginListResponse) => void;
  let finishList!: (value: PluginListResponse) => void;
  api.checkUpdates.mockImplementationOnce(() => new Promise((resolve) => { finishFirst = resolve; }));
  let first!: Promise<void>;
  let queued!: Promise<void>;
  await act(async () => { first = checks.runCheck(); queued = checks.refreshPlugins(); });
  api.listSideloadPlugins.mockImplementationOnce(() => new Promise((resolve) => { finishList = resolve; }));
  await act(async () => { finishFirst(listing); await first; });
  api.checkUpdates.mockImplementationOnce(() => new Promise((resolve) => { finishSecond = resolve; }));
  let second!: Promise<void>;
  await act(async () => { second = checks.runCheck(); });
  const oldListing = { ...listing, plugins: [{ ...plugin, name: "Stale row" }] };
  await act(async () => { finishList(oldListing); await queued; });
  expect(text(root)).not.toContain("Stale row");
  expect(buttons(root).find((b) => b.props["data-action-key"] === "checkUpdates")!.props.disabled).toBe(true);
  expect(checks.runCheck()).toBe(second);
  await act(async () => { finishSecond(listing); await second; });
  expect(buttons(root).find((b) => b.props["data-action-key"] === "checkUpdates")!.props.disabled).toBe(false);
});


test("checkController requeues a list invalidated by a newer check only when refresh is requested", async () => {
  const root = await mount(<Home selected={null} onOpen={() => {}} />);
  let finishList!: (value: PluginListResponse) => void;
  let finishCheck!: (value: PluginListResponse) => void;
  api.listSideloadPlugins.mockClear();
  api.listSideloadPlugins.mockImplementationOnce(() => new Promise((resolve) => { finishList = resolve; }));
  let refreshing!: Promise<void>;
  await act(async () => { refreshing = checks.refreshPlugins(); });
  api.checkUpdates.mockImplementationOnce(() => new Promise((resolve) => { finishCheck = resolve; }));
  let checking!: Promise<void>;
  await act(async () => { checking = checks.runCheck(); });
  expect(checks.refreshPlugins()).toBe(refreshing);
  expect(checks.refreshPlugins()).toBe(refreshing);
  await act(async () => finishList({ ...listing, plugins: [{ ...plugin, name: "Discarded row" }] }));
  expect(api.listSideloadPlugins).toHaveBeenCalledTimes(1);
  expect(text(root)).not.toContain("Discarded row");
  expect(buttons(root).find((b) => b.props["data-action-key"] === "checkUpdates")!.props.disabled).toBe(true);
  listing = { ...listing, plugins: [{ ...plugin, name: "Current row" }] };
  await act(async () => { finishCheck(listing); await checking; await refreshing; });
  expect(api.listSideloadPlugins).toHaveBeenCalledTimes(2);
  expect(text(root)).toContain("Current row");
  expect(buttons(root).find((b) => b.props["data-action-key"] === "checkUpdates")!.props.disabled).toBe(false);
});



test("scheduled reports clear stale update headings without moving focused rows", async () => {
  for (const status of ["current", "error", "whole"] as const) {
    const item = { name: plugin.name, repo: plugin.repo, current: plugin.version, latest: "2.0.0", status: "update" as const, error: null };
    listing.last_check = { checkedAt: "2026-10-15T00:00:00+00:00", items: [item] };
    await act(async () => checks.runCheck());
    const root = await mount(<Home selected={plugin.folder} onOpen={() => {}} />);
    const row = root.root.findAllByType("Field").find((field) => field.props.label === plugin.name)!;
    expect(root.root.findAllByType("PanelSection").some((section) => section.props.title === "可更新")).toBe(true);
    const report = { checkedAt: "2026-10-15T01:00:00+00:00", source: "scheduled" as const,
      items: status === "whole" ? [] : [{ ...item, status, error: status === "error" ? "network failed" : null }],
      ...(status === "whole" ? { error: "whole failed" } : {}) };
    listing.last_check = report;
    await act(async () => { checks.receiveScheduledCheck({ report, checkNotifications: false }); await checks.refreshPlugins(); });
    expect(root.root.findAllByType("PanelSection").some((section) => section.props.title === "可更新")).toBe(false);
    expect(root.root.findAllByType("PanelSection").some((section) => section.props.title === "检查结果")).toBe(true);
    const retained = root.root.findAllByType("Field").find((field) => field.props.label === plugin.name)!;
    expect(retained).toBe(row);
    expect(retained.props.preferredFocus).toBe(true);
    expect(retained.props.icon.props.className).toBeUndefined();
    await unmount(root);
  }
});

test("home distinguishes skipped latest releases without hiding errors or older available updates", async () => {
  const scenarios = [
    { skipped: ["v2"], status: "current", rows: [release], snapshot: true, text: "已跳过最新版本", warning: true },
    { skipped: ["v0"], status: "current", rows: [{ ...release, comparison: 0 }], snapshot: true, text: "已是最新", warning: false },
    { skipped: ["v2"], status: "ahead", rows: [release], snapshot: true, text: "已跳过最新版本", warning: true },
    { skipped: ["v2"], status: "update", rows: [release, { ...release, tag: "v1.5" }], snapshot: true, text: "可更新", warning: false },
    { skipped: ["v2"], status: "error", rows: [release], snapshot: true, text: "暂时无法检查更新", warning: false },
    { skipped: ["v2"], status: "current", rows: [release], snapshot: false, text: "已跳过版本", warning: true },
  ] as const;
  for (const scenario of scenarios) {
    await act(async () => checks.disposeReleaseReads());
    const subject = { ...plugin, skipped_tags: [...scenario.skipped] };
    listing = { native_snapshot: { ok: true, plugins: [] }, plugins: [subject], store_ok: true, store_error: null, last_check: {
      checkedAt: "2026-10-25T00:00:00Z", items: [{ name: subject.name, repo: subject.repo, current: subject.version,
        latest: scenario.status === "update" ? "v1.5" : "v1", status: scenario.status, error: scenario.status === "error" ? "offline" : null }],
    } };
    if (scenario.snapshot) releaseResult(subject, [...scenario.rows]);
    await act(async () => checks.refreshPlugins());
    const root = await mount(<Home selected={null} onOpen={() => {}} />);
    const row = root.root.findAllByType("Field").find(field => field.props.label === subject.name)!;
    const statuses = row.findAll(node => node.type === "span" && node.props["data-status-tone"]);
    expect(statuses.map(node => node.children.filter(child => typeof child === "string").join("")).join("")).toContain(scenario.text);
    expect(statuses.some(node => node.props["data-status-tone"] === "warning")).toBe(scenario.warning);
    expect(api.getPluginReleases).not.toHaveBeenCalled();
    await unmount(root);
  }
});

test("undoing a skip retains cached notes and the selected ZIP across detail remounts", async () => {
  const skipped = { ...plugin, skipped_tags: [release.tag] };
  const secondZip = { name: "alternate.zip", url: "https://github.com/acme/example/releases/download/v2/alternate.zip" };
  releases = [{ ...release, zips: [...release.zips, secondZip] }];
  listing = { ...listing, plugins: [skipped] };
  releaseResult(skipped);
  await act(async () => checks.refreshPlugins());
  const props = { plugin: skipped, onBinding: () => {}, onOperation: () => {} };
  let root = await mount(<InstalledTab {...props} />);
  const picker = () => root.root.findAllByType("DropdownItem").find(node => node.props.label === "安装包")!;
  await act(async () => picker().props.onChange({ data: secondZip.url }));
  api.setSkippedRelease.mockImplementationOnce(async () => {
    listing = { ...listing, plugins: [{ ...plugin, skipped_tags: [] }] };
    return listing;
  });
  await act(async () => buttons(root).find(button => button.props["data-action-key"] === "skipRelease")!.props.onActivate());
  expect(picker().props.selectedOption).toBe(secondZip.url);
  expect(text(root)).toContain("Release notes");
  expect(text(root)).not.toContain("已跳过此版本");
  expect(api.getPluginReleases).not.toHaveBeenCalled();
  await unmount(root);
  root = await mount(<InstalledTab {...props} />);
  expect(picker().props.selectedOption).toBe(secondZip.url);
  expect(api.getPluginReleases).not.toHaveBeenCalled();
});

test("scheduled listener toasts once outside QAM, synchronizes mounted views, and cleans up", async () => {
  const definition = makePlugin(); stop = definition.onDismount!;
  const handler = eventHandlers.get("scheduled_check_completed")!;
  expect(handler).toBeDefined();
  for (const status of ["update", "current", "error", "no_asset", "no_release", "uncomparable", "unbound"] as const) {
    const report = { checkedAt: new Date().toISOString(), source: "scheduled" as const, items: [{ name: plugin.name, repo: plugin.repo, current: plugin.version, latest: "2.0.0", status, error: status === "error" ? "network failed" : null }] };
    listing.last_check = report;
    await act(async () => handler({ report, checkNotifications: true }));
    expect(toast).toHaveBeenCalledTimes(["update", "current", "error", "no_asset", "no_release", "uncomparable", "unbound"].indexOf(status) + 1);
    if (["no_asset", "no_release", "uncomparable"].includes(status)) expect((toast.mock.calls.at(-1) as any)[0].body).toContain("无法确认 1 个");
  }
  const report = { checkedAt: new Date().toISOString(), source: "scheduled" as const, items: [], error: "whole failed" };
  settings.lastScheduledCheck = report; settings.lastCheck = report; listing.last_check = report;
  const root = await mount(<SettingsTab />);
  await act(async () => handler({ report, checkNotifications: false }));
  expect(toast).toHaveBeenCalledTimes(7);
  expect(text(root)).toContain("检查失败，查看原因");
  expect(text(root)).not.toContain("whole failed");
  await unmount(root);
  const reopened = await mount(<SettingsTab />);
  expect(text(reopened)).toContain("检查失败，查看原因");
  await act(async () => handler({ report, checkNotifications: true }));
  expect(toast).toHaveBeenCalledTimes(8);
  expect((toast.mock.calls.at(-1) as any)[0].body).toContain("整轮检查失败：whole failed");
  stop(); stop = null;
  expect(eventHandlers.has("scheduled_check_completed")).toBe(false);
  expect(removeBackendListener).toHaveBeenCalledWith("scheduled_check_completed", handler);
});

test("notification saving keeps confirmed preference, suppresses pending disable toast and shows failed save", async () => {
  settings.checkNotifications = true;
  const definition = makePlugin(); stop = definition.onDismount!;
  const handler = eventHandlers.get("scheduled_check_completed")!;
  let finish!: (value: AppSettings) => void;
  api.setCheckNotifications.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  const root = await mount(<SettingsTab />);
  const toggle = () => root.root.findAllByType("ToggleField").find((field) => field.props.label === "定时检查通知")!;
  let save!: Promise<void>;
  await act(async () => { save = toggle().props.onChange(false); });
  expect(toggle().props.checked).toBe(true);
  expect(toggle().props.disabled).toBe(true);
  const report = { checkedAt: new Date().toISOString(), source: "scheduled" as const, items: [] };
  listing.last_check = report;
  await act(async () => handler({ report, checkNotifications: true }));
  expect(toast).not.toHaveBeenCalled();
  await act(async () => { finish({ ...settings, checkNotifications: false }); await save; });
  expect(toggle().props.checked).toBe(false);
  await act(async () => handler({ report, checkNotifications: true }));
  expect(toast).not.toHaveBeenCalled();
  expect(text(root)).toContain("最近定时检查");
  api.setCheckNotifications.mockRejectedValueOnce(new Error("cannot save"));
  await act(async () => { toggle().props.onChange(true); });
  expect(toggle().props.checked).toBe(false);
  expect(text(root)).toContain("cannot save");
  expect(api.setAutoCheck).not.toHaveBeenCalled();
});


test("settings has one compact scheduled row and never repeats manual report or item metadata", async () => {
  const report: CheckReport = { checkedAt: new Date(Date.now() + 60000).toISOString(), source: "scheduled", items: [
    { name: plugin.name, repo: plugin.repo, current: plugin.version, latest: "2.0.0", status: "update", error: null },
    { name: "Diagnostic", repo: "private/long-repository", current: "1", latest: null, status: "error", error: "very long diagnostic" },
  ] };
  settings.lastScheduledCheck = report;
  settings.lastCheck = { ...report, source: "manual", checkedAt: new Date(Date.now() + 120000).toISOString(), items: [] };
  const open = mock();
  await act(async () => { checks.receiveScheduledCheck({ report, checkNotifications: false }); await checks.refreshPlugins(); });
  const root = await mount(<SettingsTab onResults={open} focusResults />);
  const rows = root.root.findAllByType("Field").filter((field) => field.props["data-action-key"] === "checkResults");
  expect(rows).toHaveLength(1);
  expect(rows[0].props.description).toContain("可更新 1，失败 1");
  expect(rows[0].props.preferredFocus).toBe(true);
  expect(root.root.findAllByType("Field").some((field) => field.props.label === "最近检查")).toBe(false);
  for (const value of [plugin.name, "private/long-repository", "very long diagnostic", "无需更新", "未关联 0"]) expect(text(root)).not.toContain(value);
  await act(async () => rows[0].props.onActivate());
  expect(open).toHaveBeenCalledWith(report);
  expect(api.setAutoCheck).not.toHaveBeenCalled();
  expect(api.setCheckNotifications).not.toHaveBeenCalled();
});

test("compact results prioritize exceptions, omit metadata and keep row positions during scheduled refresh", async () => {
  const items: CheckReport["items"] = [
    { name: "Normal", repo: "acme/normal", current: "1", latest: "1", status: "current", error: null },
    { name: "Error", repo: "acme/error", current: "1", latest: null, status: "error", error: "full error detail" },
    { name: "Update", repo: "acme/update", current: "1", latest: "2", status: "update", error: null },
  ];
  listing.plugins = items.map((i) => ({ ...plugin, folder: i.name, name: i.name, repo: i.repo, version: i.current }));
  const report: CheckReport = { checkedAt: new Date(Date.now() + 60000).toISOString(), source: "scheduled", items };
  await act(async () => { checks.receiveScheduledCheck({ report, checkNotifications: false }); await checks.refreshPlugins(); });
  const open = mock();
  const root = await mount(<CheckResults report={report} selected={null} onOpen={open} />);
  const rows = () => root.root.findAllByType("Field").filter((field) => field.props["data-check-key"]);
  expect(rows().map((field) => field.props["aria-label"])).toEqual(["Update", "Error", "Normal"]);
  expect(text(root)).not.toContain("acme/");
  expect(text(root)).not.toContain("full error detail");
  expect(rows().every((field) => field.props.focusable)).toBe(true);
  const retained = rows()[0];
  const next: CheckReport = { ...report, checkedAt: new Date(Date.now() + 120000).toISOString(), items: items.map((i) => ({ ...i, status: i.name === "Update" ? "current" : "update" })) };
  await act(async () => { checks.receiveScheduledCheck({ report: next, checkNotifications: false }); await checks.refreshPlugins(); });
  expect(rows()[0]).toBe(retained);
  expect(rows().map((field) => field.props["aria-label"])).toEqual(["Update", "Error", "Normal"]);
  await act(async () => rows()[0].props.onActivate());
  expect(open.mock.calls[0][0].status).toBe("current");
});

test("return summary exists before async settings resolve so native preferred focus can enter it", async () => {
  const report: CheckReport = { checkedAt: new Date(Date.now() + 180000).toISOString(), source: "scheduled", items: [] };
  let finish!: (value: AppSettings) => void;
  api.getAppSettings.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  const root = await mount(<SettingsTab focusResults report={report} />);
  const row = root.root.findAllByType("Field").find((f) => f.props["data-action-key"] === "checkResults")!;
  expect(row.props.preferredFocus).toBe(true);
  expect(row.props.focusable).toBe(true);
  expect(text(root)).toContain("正在读取设置");
  await act(async () => finish({ ...settings, lastScheduledCheck: report }));
  expect(root.root.findAllByType("Field").find((f) => f.props["data-action-key"] === "checkResults")).toBe(row);
  expect(text(root)).not.toContain("正在读取设置");
});

test("result navigation preserves full diagnostics, QAM page, return focus and native B handling", async () => {
  const report: CheckReport = { checkedAt: new Date(Date.now() + 180000).toISOString(), source: "scheduled", items: [
    { name: plugin.name, repo: plugin.repo, current: plugin.version, latest: null, status: "error", error: "complete failure reason" },
  ] };
  settings.lastScheduledCheck = report;
  await act(async () => { checks.receiveScheduledCheck({ report, checkNotifications: false }); await checks.refreshPlugins(); });
  const definition = makePlugin(); stop = definition.onDismount!;
  let root = await mount(definition.content!);
  await act(async () => buttons(root).find((b) => b.props["data-action-key"] === "settings")!.props.onClick());
  await act(async () => root.root.findAllByType("Field").find((f) => f.props["data-action-key"] === "checkResults")!.props.onActivate());
  const row = root.root.findAllByType("Field").find((f) => f.props["data-check-key"])!;
  await act(async () => row.props.onActivate());
  expect(text(root)).toContain("complete failure reason");
  expect(text(root)).toContain(plugin.repo!);
  await unmount(root); root = await mount(definition.content!);
  expect(text(root)).toContain("complete failure reason");
  const event = { preventDefault: mock(), stopPropagation: mock() };
  await act(async () => navigation(root).props.onCancel(event));
  expect(root.root.findAllByType("Field").find((f) => f.props["data-check-key"])!.props.preferredFocus).toBe(true);
  await act(async () => navigation(root).props.onCancel(event));
  expect(root.root.findAllByType("Field").find((f) => f.props["data-action-key"] === "checkResults")!.props.preferredFocus).toBe(true);
  expect(event.preventDefault).toHaveBeenCalledTimes(2);
  expect(event.stopPropagation).toHaveBeenCalledTimes(2);
  expect(api.prepareInstall).not.toHaveBeenCalled();
  expect(api.submitPrepared).not.toHaveBeenCalled();
});

test("changed plugin identity opens a read-only historical result instead of version actions", async () => {
  const report: CheckReport = { checkedAt: new Date(Date.now() + 240000).toISOString(), source: "scheduled", items: [
    { name: plugin.name, repo: "old/repo", current: "0.1", latest: "0.2", status: "update", error: "historical detail" },
  ] };
  settings.lastScheduledCheck = report;
  await act(async () => { checks.receiveScheduledCheck({ report, checkNotifications: false }); await checks.refreshPlugins(); });
  const definition = makePlugin(); stop = definition.onDismount!;
  const root = await mount(definition.content!);
  await act(async () => buttons(root).find((b) => b.props["data-action-key"] === "settings")!.props.onClick());
  await act(async () => root.root.findAllByType("Field").find((f) => f.props["data-action-key"] === "checkResults")!.props.onActivate());
  const row = root.root.findAllByType("Field").find((f) => f.props["data-check-key"])!;
  expect(row.props.icon.props.className).toBeUndefined();
  expect(text(root)).toContain("历史记录");
  await act(async () => row.props.onActivate());
  expect(text(root)).toContain("历史检查记录");
  expect(text(root)).toContain("old/repo");
  expect(text(root)).toContain("historical detail");
  const record = root.root.findAllByType("PanelSection").find((section) => section.props.title === "定时检查记录")!;
  const status = record.findAllByType("span").find((span) => span.props["data-status-tone"]);
  expect(status!.props["data-status-tone"]).toBe("muted");
  expect(status!.props.className).toBeUndefined();
  expect(status!.children).toEqual(["可更新"]);
  expect(text(root).replace(/\s+/g, " ")).toContain("0.1 → 0.2");
  expect(api.listReleases).not.toHaveBeenCalled();
  expect(api.prepareInstall).not.toHaveBeenCalled();
});

test("scheduled event invalidates an in-flight list, keeps check error, and reads current binding", async () => {
  api.checkUpdates.mockRejectedValueOnce(new Error("manual error"));
  await act(async () => checks.runCheck());
  let finish!: (value: PluginListResponse) => void;
  const old = { ...listing, plugins: [{ ...plugin, repo: "old/repo" }] };
  api.listSideloadPlugins.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  let refresh!: Promise<void>;
  await act(async () => { refresh = checks.refreshPlugins(); });
  listing = { ...listing, plugins: [{ ...plugin, repo: "new/repo" }] };
  const report = { checkedAt: new Date().toISOString(), source: "scheduled" as const, items: [] };
  listing.last_check = report;
  await act(async () => { checks.receiveScheduledCheck({ report, checkNotifications: false }); finish(old); await refresh; });
  const root = await mount(<Home selected="example" onOpen={() => {}} onInstall={() => {}} onSettings={() => {}} />);
  expect(text(root)).toContain("manual error");
  const open = mock();
  const next = await mount(<Home selected="example" onOpen={open} onInstall={() => {}} onSettings={() => {}} />);
  const row = next.root.findAllByType("Field").find((field) => field.props.label === plugin.name)!;
  await act(async () => row.props.onActivate());
  expect(open.mock.calls[0][0].repo).toBe("new/repo");
});

const networkChoice = (root: ReactTestRenderer, mode?: string) => root.root.findAllByType("Focusable").find(node => node.props.role === "radio" && (mode ? node.props["data-choice"] === mode : node.props["aria-checked"]));
async function acceptNetworkConsent() {
  const modal = await mount(nativeModal!);
  await act(async () => modal.root.findByType("ToggleField").props.onChange(true));
  await act(async () => modal.root.findByType("ConfirmModal").props.onOK());
  await unmount(modal);
}

test("main settings network entry opens its page and receives return focus", async () => {
  settings.networkMode = "auto";
  settings.lastNetworkRoute = { route: "direct", timestamp: 1729000000 };
  const open = mock();
  const root = await mount(<SettingsTab onNetwork={open} focusNetwork />);
  expect(networkChoice(root)).toBeUndefined();
  const row = root.root.findAllByType("Field").find((node) => node.props["data-action-key"] === "network")!;
  expect(row.props.preferredFocus).toBe(true);
  await act(async () => row.props.onActivate());
  expect(open).toHaveBeenCalledTimes(1);
});

test("network navigation and B return preserve page and preferred row across QAM", async () => {
  const definition = makePlugin(); stop = definition.onDismount!;
  let root = await mount(definition.content!);
  await act(async () => buttons(root).find((node) => node.props["data-action-key"] === "settings")!.props.onClick());
  await act(async () => root.root.findAllByType("Field").find((node) => node.props["data-action-key"] === "network")!.props.onActivate());
  expect(networkChoice(root)).toBeDefined();
  await unmount(root); root = await mount(definition.content!);
  expect(networkChoice(root)).toBeDefined();
  const event = { preventDefault: mock(), stopPropagation: mock() };
  await act(async () => navigation(root).props.onCancel(event));
  expect(event.preventDefault).toHaveBeenCalledTimes(1);
  expect(event.stopPropagation).toHaveBeenCalledTimes(1);
  const row = root.root.findAllByType("Field").find((node) => node.props["data-action-key"] === "network")!;
  expect(row.props.preferredFocus).toBe(true);
  expect(root.root.findAllByType("Field").find((node) => node.props["data-action-key"] === "checkResults")!.props.preferredFocus).toBe(false);
  await unmount(root); root = await mount(definition.content!);
  expect(root.root.findAllByType("Field").find((node) => node.props["data-action-key"] === "network")!.props.preferredFocus).toBe(true);
});

for (const resolveDuringSave of [true, false]) test(`network ignores stale read ${resolveDuringSave ? "during" : "after"} saving and retains selection across QAM`, async () => {
  let root = await mount(<NetworkSettings />);
  await unmount(root);
  let finishRead!: (value: AppSettings) => void;
  api.getAppSettings.mockImplementationOnce(() => new Promise((resolve) => { finishRead = resolve; }));
  root = await mount(<NetworkSettings />);
  let finishSave!: (value: AppSettings) => void;
  api.setNetworkMode.mockImplementationOnce(() => new Promise((resolve) => { finishSave = resolve; }));
  const selectMode = networkChoice(root, "gh-proxy")!.props.onActivate;
  await act(async () => networkChoice(root, "auto")!.props.onActivate());
  await acceptNetworkConsent();
  expect(networkChoice(root)!.props["data-choice"]).toBe("auto");
  expect(networkChoice(root)!.props["aria-disabled"]).toBe(true);
  await unmount(root); root = await mount(<NetworkSettings />);
  expect(networkChoice(root)!.props["data-choice"]).toBe("auto");
  expect(networkChoice(root)!.props["aria-disabled"]).toBe(true);
  await act(async () => selectMode());
  expect(api.setNetworkMode).toHaveBeenCalledTimes(1);
  if (resolveDuringSave) await act(async () => finishRead({ ...settings, networkMode: "direct" }));
  await act(async () => { settings = { ...settings, networkMode: "auto" }; finishSave(settings); });
  if (!resolveDuringSave) await act(async () => finishRead({ ...settings, networkMode: "direct" }));
  expect(networkChoice(root)!.props["data-choice"]).toBe("auto");
  expect(networkChoice(root)!.props["aria-disabled"]).toBe(false);
  expect(api.setAutoCheck).not.toHaveBeenCalled();
  expect(api.setCheckNotifications).not.toHaveBeenCalled();
});

test("network failed save restores confirmed mode and retains error after QAM", async () => {
  let root = await mount(<NetworkSettings />);
  let fail!: (error: Error) => void;
  api.setNetworkMode.mockImplementationOnce(() => new Promise((_, reject) => { fail = reject; }));
  await act(async () => networkChoice(root, "gh-proxy")!.props.onActivate());
  await acceptNetworkConsent();
  await unmount(root); root = await mount(<NetworkSettings />);
  await act(async () => fail(new Error("network save rejected")));
  expect(networkChoice(root)!.props["data-choice"]).toBe("direct");
  expect(networkChoice(root)!.props["aria-disabled"]).toBe(false);
  expect(text(root)).toContain("network save rejected");
});

test("network failed read retains risk and retries without writing a mode", async () => {
  api.getAppSettings.mockRejectedValueOnce(new Error("read rejected"));
  const root = await mount(<NetworkSettings />);
  expect(text(root)).toContain("read rejected");
  expect(networkChoice(root)).toBeUndefined();
  await act(async () => buttons(root).find((node) => node.props.children === "重试")!.props.onClick());
  expect(networkChoice(root)!.props["data-choice"]).toBe("direct");
  expect(api.setNetworkMode).not.toHaveBeenCalled();
});


test("unknown task navigation remains secondary and never resubmits or closes the task", async () => {
  operation = { ...makeOperation(), stage: "unknown", request_id: "existing-request" };
  await act(async () => controller.refreshOperation());
  const definition = makePlugin(); stop = definition.onDismount!;
  const root = await mount(definition.content!);
  const row = root.root.findAllByType("Field").find((node) => node.props["data-action-key"] === "viewTask")!;
  expect(row.props.focusable).toBe(true);
  await act(async () => row.props.onActivate());
  expect(text(root)).toContain("安装结果尚未确认");
  expect(api.submitPrepared).not.toHaveBeenCalled();
  expect(api.closeOperation).not.toHaveBeenCalled();
  expect(operation.stage).toBe("unknown");
  expect(operation.request_id).toBe("existing-request");
});



test("explicit reinstall button prepares its mode without replacing ordinary installation", async () => {
  stop = controller.startInstallController(); await controller.refreshOperation();
  operation = { ...makeOperation(), mode: "reinstall", existing_name: plugin.name };
  const root = await mount(<InstalledTab section="maintenance" plugin={plugin} onBinding={() => {}} onOperation={() => {}} />);
  const actions = buttons(root);
  expect(actions.some((button) => button.props["data-action-key"] === "replace")).toBe(false);
  await act(async () => actions.find((button) => button.props["data-action-key"] === "reinstall")!.props.onClick());
  expect(api.prepareInstall).not.toHaveBeenCalled();
  expect(nativeModal!.props.strDescription).toContain("现在尚未备份");
  await act(async () => { nativeModal!.props.onOK(); for (let i = 0; i < 12; i++) await Promise.resolve(); });
  expect(api.prepareInstall).toHaveBeenCalledWith(release.repo, release.tag, release.zips[0].url, plugin.name, null, "reinstall");
  expect(api.claimUninstall).not.toHaveBeenCalled();
  expect(loaderCall).not.toHaveBeenCalled();
});

async function beginReinstall() {
  if (!stop) { stop = controller.startInstallController(); await controller.refreshOperation(); }
  operation = { ...makeOperation(), stage: "ready", mode: "reinstall", existing_name: plugin.name,
    backup: { paths: ["settings/example"], files: 1, bytes: 10, archive: "/backup.zip", sha256: "b".repeat(64), scope: "standard only", created_at: "2026-10-15" } };
  await act(async () => controller.refreshOperation());
  const pending = controller.dispatchPrepared();
  await act(async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); });
  return { pending };
}

test("reinstall native confirmation cancellation invokes neither uninstall nor install", async () => {
  const { pending } = await beginReinstall();
  expect(nativeModal!.props.strDescription).toContain("settings/example");
  expect(nativeModal!.props.strDescription).toContain("卸载钩子");
  expect(api.claimUninstall).not.toHaveBeenCalled();
  nativeModal!.props.onCancel();
  await pending;
  expect(api.recordInstallEvent).toHaveBeenCalledWith("job", "uninstall_cancelled", "Example");
  expect(api.claimUninstall).not.toHaveBeenCalled();
  expect(loaderCall).not.toHaveBeenCalled();
});

test("reinstall modal dismissal is cancellation and preparation failure never opens it", async () => {
  let { pending } = await beginReinstall();
  nativeModalClose!(); await pending;
  expect(loaderCall).not.toHaveBeenCalled();
  nativeModal = null;
  operation = { ...makeOperation(), stage: "failed", mode: "reinstall" };
  await controller.refreshOperation(); await controller.dispatchPrepared();
  expect(nativeModal).toBeNull();
  expect(api.claimUninstall).not.toHaveBeenCalled();
});

test("reinstall claims once, survives QAM remount and installs only after backend absence check", async () => {
  let finish!: () => void;
  loaderCall.mockImplementationOnce(() => new Promise<void>((resolve) => { finish = resolve; }));
  const { pending } = await beginReinstall();
  nativeModal!.props.onOK();
  await act(async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); });
  expect(loaderCall.mock.calls[0]).toEqual(["utilities/uninstall_plugin", "Example"]);
  expect(api.completeUninstall).not.toHaveBeenCalled();
  await controller.dispatchPrepared();
  expect(api.claimUninstall).toHaveBeenCalledTimes(1);
  let root = await mount(<OperationPanel />);
  await unmount(root); root = await mount(<OperationPanel />);
  expect(text(root)).toContain("正在卸载");
  finish(); await act(async () => pending);
  expect(api.completeUninstall).toHaveBeenCalledWith("job");
  expect(loaderCall.mock.calls[1]).toEqual(["utilities/install_plugin", operation!.artifact, "Example", "2.0.0", "a".repeat(64), 0]);
  await controller.dispatchPrepared();
  expect(loaderCall).toHaveBeenCalledTimes(2);
});

for (const failure of ["claim", "uninstall", "absence"]) test(`reinstall ${failure} failure never opens install confirmation`, async () => {
  if (failure === "claim") api.claimUninstall.mockRejectedValueOnce(new Error("changed package or backup"));
  if (failure === "uninstall") loaderCall.mockRejectedValueOnce(new Error("loader failed"));
  if (failure === "absence") api.completeUninstall.mockRejectedValueOnce(new Error("target still exists"));
  const { pending } = await beginReinstall();
  nativeModal!.props.onOK(); await pending;
  expect(loaderCall.mock.calls.filter((call) => call[0] === "utilities/install_plugin")).toHaveLength(0);
  if (failure === "claim") expect(loaderCall).not.toHaveBeenCalled();
  if (failure === "uninstall") expect(api.completeUninstall).not.toHaveBeenCalled();
});

test("controller reload during uninstall never continues or replays the claimed call", async () => {
  stop = controller.startInstallController(); await controller.refreshOperation();
  let finish!: () => void;
  loaderCall.mockImplementationOnce(() => new Promise<void>((resolve) => { finish = resolve; }));
  const { pending } = await beginReinstall();
  nativeModal!.props.onOK(); await act(async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); });
  expect(loaderCall).toHaveBeenCalledTimes(1);
  stop(); stop = controller.startInstallController(); await controller.refreshOperation();
  finish(); await pending;
  expect(api.completeUninstall).not.toHaveBeenCalled();
  expect(loaderCall).toHaveBeenCalledTimes(1);
  await controller.refreshOperation();
  expect(loaderCall).toHaveBeenCalledTimes(1);
});

test("verified remains active while finalization retries and closed success retains result access without manual finish", async () => {
  operation = { ...makeOperation(), stage: "verified", saw_finish: true, download: { received: 1048576, total: 1048576, status: "complete", route: "gh-proxy", attempt: 1 } };
  await controller.refreshOperation();
  expect(controller.isActive(operation)).toBe(true);
  let root = await mount(<OperationPanel />);
  expect(text(root)).toContain("收尾尚未完成");
  expect(text(root)).toContain("下载完成");
  expect(text(root)).not.toContain("安装仍须核验");
  expect(buttons(root).some((button) => button.props.children === "结束本次任务")).toBe(false);
  operation = { ...operation, stage: "closed", binding_saved: true, outcome: "success", close_reason: "verified", completed_at: "2026-10-15" };
  await act(async () => controller.refreshOperation());
  expect(controller.isActive(operation)).toBe(false);
  expect(text(root)).toContain("任务已自动结束");
  expect(text(root)).toContain("GitHub 关联已保存");
  expect(text(root)).not.toContain("安装仍须核验");
  expect(api.closeOperation).not.toHaveBeenCalled();
  await unmount(root);
  const definition = makePlugin(); stop = definition.onDismount;
  root = await mount(definition.content!);
  const task = buttons(root).find((button) => button.props.children === "安装已完成，查看任务")!;
  expect(task).toBeDefined();
  await act(async () => task.props.onClick());
  expect(text(root)).toContain("任务已自动结束");
  expect(api.submitPrepared).not.toHaveBeenCalled();
});

test("source retry publishes independent busy state, deduplicates clicks and survives QAM remount", async () => {
  listing = { ...listing, store_ok: false, store_error: "store offline" };
  const element = <SettingsTab />;
  let root = await mount(element);
  api.listSideloadPlugins.mockClear();
  let finish!: (value: PluginListResponse) => void;
  api.listSideloadPlugins.mockImplementationOnce(() => new Promise<PluginListResponse>((resolve) => { finish = resolve; }));
  const retry = root.root.findAllByType("Field").find((node) => node.props["data-action-key"] === "refreshRecognition")!;
  const activate = retry.props.onClick;
  await act(async () => { activate(); activate(); });
  let current = root.root.findAllByType("Field").find((node) => node.props["data-action-key"] === "refreshRecognition")!;
  expect(current.props["aria-busy"]).toBe(true);
  expect(current.props.disabled).toBe(true);
  expect(api.listSideloadPlugins).toHaveBeenCalledTimes(1);
  expect(api.listSideloadPlugins.mock.calls[0]).toEqual([true]);
  expect(api.checkUpdates).not.toHaveBeenCalled();
  await unmount(root); root = await mount(element);
  current = root.root.findAllByType("Field").find((node) => node.props["data-action-key"] === "refreshRecognition")!;
  expect(current.props["aria-busy"]).toBe(true);
  expect(api.listSideloadPlugins).toHaveBeenCalledTimes(1);
  await act(async () => { finish(listing); await checks.refreshPlugins(); });
  current = root.root.findAllByType("Field").find((node) => node.props["data-action-key"] === "refreshRecognition")!;
  expect(current.props["aria-busy"]).toBe(false);
  expect(current.props.disabled).toBe(false);
  expect(text(root)).not.toContain("store offline");
});

test("source read failure is labelled separately and a successful retry preserves check failure", async () => {
  // A previously successful catalog must not hide retry after an RPC failure.
  const root = await mount(<><Home selected={null} onOpen={() => {}} /><SettingsTab /></>);
  api.checkUpdates.mockRejectedValueOnce(new Error("check failure retained"));
  await act(async () => checks.runCheck());
  api.listSideloadPlugins.mockRejectedValueOnce(new Error("source RPC timeout"));
  await act(async () => checks.refreshPlugins(true));
  expect(text(root)).toContain("刷新失败");
  expect(text(root)).toContain("source RPC timeout");
  expect(text(root)).toContain("check failure retained");
  expect(root.root.findAllByType("Field").find((node) => node.props["data-action-key"] === "refreshRecognition")!.props.disabled).toBe(false);
  await act(async () => checks.refreshPlugins(true));
  expect(text(root)).not.toContain("刷新失败");
  expect(text(root)).not.toContain("source RPC timeout");
  expect(root.root.findAllByType("Field").filter((node) => node.props["data-action-key"] === "refreshRecognition")).toHaveLength(1);
  expect(text(root)).toContain("检查失败");
  expect(text(root)).toContain("check failure retained");
});

test("source refresh waiting for a check stays busy until its queued list read finishes", async () => {
  listing = { ...listing, store_ok: false, store_error: "store offline" };
  const root = await mount(<SettingsTab />);
  api.listSideloadPlugins.mockClear();
  let finishCheck!: (value: PluginListResponse) => void, finishRead!: (value: PluginListResponse) => void;
  api.checkUpdates.mockImplementationOnce(() => new Promise<PluginListResponse>((resolve) => { finishCheck = resolve; }));
  api.listSideloadPlugins.mockImplementationOnce(() => new Promise<PluginListResponse>((resolve) => { finishRead = resolve; }));
  let check!: Promise<void>, refresh!: Promise<void>;
  await act(async () => { check = checks.runCheck(); refresh = checks.refreshPlugins(true); });
  expect(api.listSideloadPlugins).not.toHaveBeenCalled();
  expect(root.root.findAllByType("Field").find((node) => node.props["data-action-key"] === "refreshRecognition")!.props["aria-busy"]).toBe(true);
  await act(async () => { finishCheck(listing); await check; });
  expect(api.listSideloadPlugins).toHaveBeenCalledTimes(1);
  expect(api.listSideloadPlugins.mock.calls[0]).toEqual([true]);
  expect(root.root.findAllByType("Field").find((node) => node.props["data-action-key"] === "refreshRecognition")!.props["aria-busy"]).toBe(true);
  await act(async () => { finishRead(listing); await refresh; });
  expect(root.root.findAllByType("Field").find((node) => node.props["data-action-key"] === "refreshRecognition")!.props["aria-busy"]).toBe(false);
});

function installEventBus() {
  const events = new Map<string, (...args: any[]) => void>();
  (window as any).DeckyBackend.addEventListener = (name: string, callback: (...args: any[]) => void) => events.set(name, callback);
  (window as any).DeckyBackend.removeEventListener = (name: string) => events.delete(name);
  return events;
}

test("Loader finish during delayed initial read is buffered for that initial operation", async () => {
  operation = { ...makeOperation(), stage: "installing", saw_finish: false };
  const initial = operation;
  let finishRead!: (value: InstallOperation) => void;
  api.getOperation.mockClear();
  api.getOperation.mockImplementationOnce(() => new Promise((resolve) => { finishRead = resolve; }));
  api.recordInstallEvent.mockImplementation(async () => (operation = { ...initial, saw_finish: true }));
  const events = installEventBus(); stop = controller.startInstallController();
  const refresh = controller.refreshOperation();
  await Promise.resolve();
  expect(api.getOperation).toHaveBeenCalledTimes(1);
  events.get("loader/plugin_download_finish")!("Example");
  expect(api.recordInstallEvent).not.toHaveBeenCalled();
  finishRead(initial); await refresh;
  await controller.refreshOperation();
  expect(api.recordInstallEvent.mock.calls).toEqual([["job", "finish", "Example", "", ""]]);
  expect(operation!.saw_finish).toBe(true);
  expect(loaderCall).not.toHaveBeenCalled();
});

test("initial Loader buffer survives a failed status read until identity is known", async () => {
  operation = { ...makeOperation(), stage: "installing" };
  api.getOperation.mockRejectedValueOnce(new Error("initial read failed"));
  const events = installEventBus(); stop = controller.startInstallController();
  events.get("loader/plugin_download_finish")!("Example");
  await controller.refreshOperation();
  expect(api.recordInstallEvent).not.toHaveBeenCalled();
  await controller.refreshOperation();
  await controller.refreshOperation();
  expect(api.recordInstallEvent.mock.calls).toEqual([["job", "finish", "Example", "", ""]]);
  expect(loaderCall).not.toHaveBeenCalled();
});

test("buffered initial events capture the first task id before a later same-name task", async () => {
  operation = { ...makeOperation(), stage: "awaiting" };
  const initial = operation;
  let finishRead!: (value: InstallOperation) => void;
  let finishEvent!: (value: InstallOperation) => void;
  api.getOperation.mockImplementationOnce(() => new Promise((resolve) => { finishRead = resolve; }));
  api.recordInstallEvent.mockImplementationOnce(() => new Promise((resolve) => { finishEvent = resolve; }));
  const events = installEventBus(); stop = controller.startInstallController();
  const refresh = controller.refreshOperation(); await Promise.resolve();
  events.get("loader/plugin_download_start")!("Example");
  events.get("loader/plugin_download_finish")!("Example");
  finishRead(initial); await refresh;
  operation = { ...makeOperation(), id: "new-job" };
  await controller.startInstallation(release, release.zips[0].url, plugin);
  api.recordInstallEvent.mockImplementationOnce(async () => initial);
  finishEvent(initial);
  await act(async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); });
  expect(api.recordInstallEvent.mock.calls.map((call) => [call[0], call[1]])).toEqual([["job", "start"], ["job", "finish"]]);
  const root = await mount(<OperationPanel />);
  expect(text(root)).toContain("正在下载并检查安装包");
  expect(loaderCall).not.toHaveBeenCalled();
});

test("initial events without a persisted task never flow into a newly created same-name task", async () => {
  let finishRead!: (value: null) => void;
  api.getOperation.mockImplementationOnce(() => new Promise((resolve) => { finishRead = resolve; }));
  const events = installEventBus(); stop = controller.startInstallController();
  const refresh = controller.refreshOperation(); await Promise.resolve();
  events.get("loader/plugin_download_finish")!("Example");
  finishRead(null); await refresh;
  operation = { ...makeOperation(), id: "new-job", stage: "awaiting" };
  await controller.startInstallation(release, release.zips[0].url, plugin);
  await controller.refreshOperation();
  expect(api.recordInstallEvent).not.toHaveBeenCalled();
  expect(operation.saw_finish).toBe(false);
  expect(loaderCall).not.toHaveBeenCalled();
});

test("creating a task before initial status resolves discards events from before its creation", async () => {
  let finishRead!: (value: InstallOperation) => void;
  api.getOperation.mockImplementationOnce(() => new Promise((resolve) => { finishRead = resolve; }));
  const events = installEventBus(); stop = controller.startInstallController();
  const refresh = controller.refreshOperation(); await Promise.resolve();
  events.get("loader/plugin_download_finish")!("Example");
  operation = { ...makeOperation(), id: "new-job", stage: "awaiting" };
  await controller.startInstallation(release, release.zips[0].url, plugin);
  finishRead(operation); await refresh;
  await controller.refreshOperation();
  expect(api.recordInstallEvent).not.toHaveBeenCalled();
  expect(operation.saw_finish).toBe(false);
  expect(loaderCall).not.toHaveBeenCalled();
});

test("controller disposal discards its initialization buffer before the next generation", async () => {
  operation = { ...makeOperation(), stage: "installing" };
  const initial = operation;
  let finishRead!: (value: InstallOperation) => void;
  api.getOperation.mockImplementationOnce(() => new Promise((resolve) => { finishRead = resolve; }));
  const events = installEventBus(); stop = controller.startInstallController();
  const refresh = controller.refreshOperation(); await Promise.resolve();
  events.get("loader/plugin_download_finish")!("Example");
  stop();
  operation = { ...initial, id: "new-job" };
  stop = controller.startInstallController(); await controller.refreshOperation();
  finishRead(initial); await refresh;
  await controller.refreshOperation();
  expect(api.recordInstallEvent).not.toHaveBeenCalled();
  expect(operation.id).toBe("new-job");
  expect(loaderCall).not.toHaveBeenCalled();
});

test("rapid Loader events record captured id directly despite a failed get and retry evidence writes only", async () => {
  operation = { ...makeOperation(), stage: "awaiting" };
  await controller.refreshOperation();
  const events = installEventBus();
  stop = controller.startInstallController(); await controller.refreshOperation();
  api.getOperation.mockRejectedValueOnce(new Error("read failed"));
  await controller.refreshOperation();
  api.getOperation.mockClear();
  api.recordInstallEvent.mockRejectedValueOnce(new Error("record failed once"));
  events.get("loader/add_plugin_install_prompt")!("Example", "2", "request-1", "a".repeat(64));
  events.get("loader/plugin_download_start")!("Example");
  events.get("loader/plugin_download_finish")!("Example");
  await act(async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); });
  expect(api.getOperation).not.toHaveBeenCalled();
  expect(api.recordInstallEvent.mock.calls.map((call) => [call[0], call[1]])).toEqual([["job", "prompt"], ["job", "prompt"], ["job", "start"], ["job", "finish"]]);
  expect(loaderCall).not.toHaveBeenCalled();
});

test("queued old Loader evidence never selects a later same-name task", async () => {
  operation = { ...makeOperation(), stage: "awaiting" };
  await controller.refreshOperation();
  const events = installEventBus(); stop = controller.startInstallController(); await controller.refreshOperation();
  const old = operation;
  let finish!: (value: InstallOperation) => void;
  api.recordInstallEvent.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  events.get("loader/plugin_download_start")!("Example");
  events.get("loader/plugin_download_finish")!("Example");
  await Promise.resolve();
  operation = { ...makeOperation(), id: "new-job" };
  await controller.startInstallation(release, release.zips[0].url, plugin);
  api.recordInstallEvent.mockImplementationOnce(async () => old);
  finish(old);
  await act(async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); });
  expect(api.recordInstallEvent.mock.calls.every((call) => call[0] === "job")).toBe(true);
  const root = await mount(<OperationPanel />);
  expect(text(root)).toContain("正在下载并检查安装包");
  expect(loaderCall).not.toHaveBeenCalled();
});


test("return focus is consumed on first native focus and cannot override later visible-order entry", async () => {
  listing.plugins = [{ ...plugin, name: "Needs repo", folder: "unbound", repo: null }, plugin];
  await checks.refreshPlugins();
  const definition = makePlugin(); stop = definition.onDismount;
  let root = await mount(definition.content!);
  expect(navigation(root).props.navEntryPreferPosition).toBe(0);
  await act(async () => root.root.findAllByType("Field").find((field) => field.props.label === plugin.name)!.props.onActivate());
  await act(async () => navigation(root).props.onCancel({ preventDefault() {}, stopPropagation() {} }));
  expect(navigation(root).props.navEntryPreferPosition).toBe(4);
  expect(root.root.findAllByType("Field").find((field) => field.props.label === plugin.name)!.props.preferredFocus).toBe(true);
  await act(async () => navigation(root).props.onGamepadFocus({}));
  expect(navigation(root).props.navEntryPreferPosition).toBe(0);
  expect(root.root.findAllByType("Field").filter((field) => field.props.preferredFocus)).toHaveLength(0);
  await unmount(root); root = await mount(definition.content!);
  expect(navigation(root).props.autoFocus).toBe(true);
  expect(navigation(root).props.navEntryPreferPosition).toBe(0);
  expect(root.root.findAllByType("Field").filter((field) => field.props.preferredFocus)).toHaveLength(0);
});


test("native-only and ambiguous details preserve historical records without management actions", async () => {
  for (const row of [
    { ...plugin, folder: null, version: null, management: { state: "enabled" as const, version: "1", loadType: 0, versionMismatch: false } },
    { ...plugin, management: { state: "ambiguous" as const, version: "1", loadType: 0, versionMismatch: false } },
  ]) {
    api.getPluginReleases.mockClear();
    const binding = mock();
    const root = await mount(<InstalledTab plugin={row} onBinding={binding} onOperation={() => {}} checkRecord={{ item: { name: row.name, repo: row.repo, current: row.version, latest: "2", status: "update", error: null }, checkedAt: "now" }} />);
    expect(text(root)).toContain("仅供查看");
    expect(buttons(root)).toHaveLength(0);
    expect(api.getPluginReleases).not.toHaveBeenCalled();
    expect(binding).not.toHaveBeenCalled();
    const record = root.root.findAllByType("PanelSection").find((section) => section.props.title === "定时检查记录")!;
    expect(record.findAllByType("span").find((span) => span.props["data-status-tone"])!.props["data-status-tone"]).toBe("muted");
    expect(text(root)).toContain("可更新");
  }
});
test("ambiguous result navigation keeps full historical diagnostics and remains read-only", async () => {
  listing.plugins = [plugin, { ...plugin, folder: "duplicate" }].map((row) => ({ ...row, management: { state: "ambiguous" as const, version: "1", loadType: 0, versionMismatch: false } }));
  const checkedAt = new Date(Date.now() + 300000).toISOString();
  const report: CheckReport = { checkedAt, source: "scheduled", items: [
    { name: plugin.name, repo: plugin.repo, current: plugin.version, latest: null, status: "error", error: "complete ambiguous failure reason\nfull diagnostic detail" },
  ] };
  settings.lastScheduledCheck = report;
  await act(async () => { checks.receiveScheduledCheck({ report, checkNotifications: false }); await checks.refreshPlugins(); });
  const definition = makePlugin(); stop = definition.onDismount!;
  const root = await mount(definition.content!);
  await act(async () => buttons(root).find((b) => b.props["data-action-key"] === "settings")!.props.onClick());
  await act(async () => root.root.findAllByType("Field").find((f) => f.props["data-action-key"] === "checkResults")!.props.onActivate());
  expect(text(root)).toContain("历史记录");
  await act(async () => root.root.findAllByType("Field").find((f) => f.props["data-check-key"])!.props.onActivate());
  expect(text(root)).toContain("同名磁盘目录多个");
  expect(text(root)).toContain(report.items[0].error!);
  expect(text(root)).toContain(plugin.repo!);
  const { shortCheckTime } = await import("../src/viewModel");
  expect(text(root)).toContain(shortCheckTime(checkedAt));
  const record = root.root.findAllByType("PanelSection").find((section) => section.props.title === "定时检查记录")!;
  expect(record.findAllByType("span").find((span) => span.props["data-status-tone"])!.props["data-status-tone"]).toBe("muted");
  expect(api.getPluginReleases).not.toHaveBeenCalled();
  expect(api.prepareInstall).not.toHaveBeenCalled();
  expect(api.submitPrepared).not.toHaveBeenCalled();
});
test("new install finds same-name native-only before reading releases", async () => {
  const existing = { ...plugin, folder: null, version: null, repo: null };
  listing.plugins = [existing];
  const open = mock();
  const root = await mount(<InstallTab onExisting={open} onOperation={() => {}} />);
  await act(async () => root.root.findByType("TextField").props.onChange({ target: { value: "acme/example" } }));
  await act(async () => { await buttons(root).find((b) => b.props.children === "读取仓库")!.props.onClick(); });
  expect(open).toHaveBeenCalledWith(existing);
  expect(api.listReleases).not.toHaveBeenCalled();
});
test("disk and Native-only cross-collision names retain separate grouping and return focus", async () => {
  const { mergeLoaderPlugins, pluginKey } = await import("../src/loaderManagement");
  listing = mergeLoaderPlugins({ ...listing, plugins: [{ ...plugin, folder: 'loader:"Native"' }] }, { ok: true, plugins: [{ name: "Native", version: "1", disabled: false, load_type: 0 }] });
  await checks.refreshPlugins();
  const root = await mount(<Home selected={pluginKey(listing.plugins[1])} onOpen={() => {}} />);
  const fields = root.root.findAllByType("Field").filter((f) => [plugin.name, "Native"].includes(f.props.label));
  expect(fields).toHaveLength(2);
  expect(fields.filter((f) => f.props.preferredFocus).map((f) => f.props.label)).toEqual(["Native"]);
});
test("native-only rows have separate stable return-focus identities", async () => {
  listing.plugins = ["Native A", "Native B"].map((name) => ({ ...plugin, name, folder: null, version: null, repo: null, in_store: true, sideload: false }));
  await act(async () => { await checks.refreshPlugins(); });
  const { pluginKey } = await import("../src/loaderManagement");
  const root = await mount(<Home selected={pluginKey(listing.plugins[1])} onOpen={() => {}} />);
  const fields = root.root.findAllByType("Field").filter((f) => ["Native A", "Native B"].includes(f.props.label));
  expect(fields.map((f) => f.props.label)).toEqual(["Native A", "Native B"]);
  expect(fields.map((f) => f.props.preferredFocus)).toEqual([false, true]);
});


test("plugin icons reuse exact loaded identity as inert decoration and preserve original styling", async () => {
  const { PluginIcon } = await import("../src/pluginIcons");
  const icon = <svg data-own-icon="exact" viewBox="0 0 8 8" style={{ opacity: 0.7 }} tabIndex={0}><path d="M0 0h8v8z" /></svg>;
  (window as any).DeckyPluginLoader.plugins = [{ name: "Other", icon: <svg data-wrong-icon /> }, { name: plugin.name, icon }];
  const root = await mount(<PluginIcon plugin={plugin} />);
  const wrapper = root.root.findByProps({ "data-plugin-icon": plugin.name });
  expect(wrapper.props.inert).toBe(true);
  expect(wrapper.props["aria-hidden"]).toBe(true);
  expect(wrapper.props.tabIndex).toBe(-1);
  expect(wrapper.props.style.pointerEvents).toBe("none");
  const svg = root.root.findByProps({ "data-own-icon": "exact" });
  expect(svg.props.viewBox).toBe("0 0 8 8");
  expect(svg.props.style.opacity).toBe(0.7);
  expect(svg.props.tabIndex).toBe(-1);
  expect(icon.props.tabIndex).toBe(0);
  expect(root.root.findAllByProps({ "data-wrong-icon": true })).toHaveLength(0);
  expect(loaderCall).not.toHaveBeenCalled();
});

test("missing invalid duplicate and throwing loaded icon metadata use the generic fallback", async () => {
  const { PluginIcon } = await import("../src/pluginIcons");
  const { FaPuzzlePiece } = await import("react-icons/fa");
  const throwing = { name: plugin.name, get icon() { throw Error("bad icon getter"); } };
  for (const plugins of [undefined, [], [{ name: "other", icon: <svg /> }], [{ name: plugin.name, icon: "invalid" }], [{ name: plugin.name, icon: <svg /> }, { name: plugin.name, icon: <svg /> }], [throwing]]) {
    (window as any).DeckyPluginLoader.plugins = plugins;
    const root = await mount(<PluginIcon plugin={plugin} />);
    expect(root.root.findAllByType(FaPuzzlePiece)).toHaveLength(1);
    await unmount(root);
  }
});

test("ambiguous disabled and unregistered rows do not borrow stale loaded icons", async () => {
  const { PluginIcon } = await import("../src/pluginIcons");
  const { FaPuzzlePiece } = await import("react-icons/fa");
  (window as any).DeckyPluginLoader.plugins = [{ name: plugin.name, icon: <svg data-own-icon="stale" /> }];
  for (const state of ["ambiguous", "disabled", "unregistered"] as const) {
    const root = await mount(<PluginIcon plugin={{ ...plugin, management: { state, version: plugin.version, loadType: 1, versionMismatch: false } }} />);
    expect(root.root.findAllByType(FaPuzzlePiece)).toHaveLength(1);
    expect(root.root.findAllByProps({ "data-own-icon": "stale" })).toHaveLength(0);
    await unmount(root);
  }
});

test("a loaded icon render failure cannot remove the row and a replacement icon can recover", async () => {
  const { PluginIcon } = await import("../src/pluginIcons");
  const { FaPuzzlePiece } = await import("react-icons/fa");
  const BadIcon = (): React.ReactElement => { throw Error("intentional icon rendering failure"); };
  (window as any).DeckyPluginLoader.plugins = [{ name: plugin.name, icon: <BadIcon /> }];
  const root = await mount(<PluginIcon plugin={plugin} />);
  expect(root.root.findAllByType(FaPuzzlePiece)).toHaveLength(1);
  (window as any).DeckyPluginLoader.plugins = [{ name: plugin.name, icon: <svg data-own-icon="recovered" /> }];
  await act(async () => root.update(<PluginIcon plugin={plugin} />));
  expect(root.root.findAllByType(FaPuzzlePiece)).toHaveLength(0);
  expect(root.root.findAllByProps({ "data-own-icon": "recovered" })).toHaveLength(1);
});

test("native-only and unknown-management icons remain decoration without inventing disk identity", async () => {
  const { PluginIcon } = await import("../src/pluginIcons");
  (window as any).DeckyPluginLoader.plugins = [{ name: plugin.name, icon: <svg data-own-icon="native" /> }];
  for (const row of [{ ...plugin, folder: null }, { ...plugin, management: { state: "unknown" as const, version: null, loadType: null, versionMismatch: false } }]) {
    const before = JSON.stringify(row);
    const root = await mount(<PluginIcon plugin={row} />);
    expect(root.root.findAllByProps({ "data-own-icon": "native" })).toHaveLength(1);
    expect(JSON.stringify(row)).toBe(before);
    expect(api.prepareInstall).not.toHaveBeenCalled();
    await unmount(root);
  }
});


test("diagnostic pickup has one intent across QAM mounts, short status, local QR and hidden file details, preserving other errors", async () => {
  let finish!: (value: DiagnosticShare) => void;
  api.startDiagnosticsShare.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  api.getAppSettings.mockImplementationOnce(async () => { throw new Error("settings read remains failed"); });
  api.checkUpdates.mockImplementationOnce(async () => { throw new Error("check remains failed"); });
  await act(async () => checks.runCheck());
  const definition = makePlugin(); stop = definition.onDismount!;
  const settingsRoot = await mount(<SettingsTab />);
  let root = await mount(<DiagnosticsSettings />);
  expect(text(root)).toContain("可信局域网");
  expect(text(root)).toContain("未加密 HTTP");
  expect(text(root)).toContain("持有链接的人");
  const action = root.root.findAllByType("DialogButton").find((button) => button.props["data-action-key"] === "shareDiagnostics")!;
  await act(async () => { action.props.onClick(); action.props.onClick(); });
  expect(api.startDiagnosticsShare).toHaveBeenCalledTimes(1);
  expect(action.props.disabled).toBe(true);
  expect(action.props["aria-busy"]).toBe(true);
  expect(text(settingsRoot)).toContain("settings read remains failed");
  await unmount(root);
  root = await mount(<DiagnosticsSettings />);
  expect(text(root)).toContain("正在准备诊断包");
  expect(api.startDiagnosticsShare).toHaveBeenCalledTimes(1);
  const session = api.startDiagnosticsShare.mock.calls[0][1];
  await act(async () => finish({ state: "active", session, package: diagnosticExport as any, url: "http://192.168.2.5:12345/pickup/" + "b".repeat(64), remaining: 300, downloads: 0 }));
  expect(text(root)).toContain("诊断包已准备");
  expect(text(root)).toContain("1.2 KiB");
  expect(text(root)).not.toContain((diagnosticExport as any).file);
  expect(text(root)).not.toContain((diagnosticExport as any).path);
  expect(text(root)).toContain("不会自动上传");
  expect(text(root)).toContain("保留最近 3 包");
  const qr = root.root.findAllByType("svg").find((s) => s.props.shapeRendering === "crispEdges")!;
  expect(qr.props["aria-hidden"]).toBe("true");
  expect(qr.props.focusable).toBe("false");
  expect(qr.props.style.width).toBe("100%");
  expect(qr.props.style.height).toBe("auto");
  expect(qr.props.style.display).toBe("block");
  expect(qr.findAllByType("path")[0].props.d.length).toBeGreaterThan(100);
  expect(root.root.findAllByType("img")).toHaveLength(0);
  const detail = root.root.findAllByType("Field").find((f) => f.props["data-action-key"] === "diagnosticDetails")!;
  await act(async () => detail.props.onActivate());
  expect(text(root)).toContain((diagnosticExport as any).path);
  const home = await mount(<Home onOpen={() => {}} onInstall={() => {}} onSettings={() => {}} />);
  expect(text(home)).toContain("check remains failed");
  expect(api.exportDiagnostics).not.toHaveBeenCalled();
  expect(api.prepareInstall).not.toHaveBeenCalled();
  expect(api.submitPrepared).not.toHaveBeenCalled();
});

test("diagnostic pickup failure retries explicitly without changing preferences, and clipboard failure is truthful", async () => {
  const definition = makePlugin(); stop = definition.onDismount!;
  api.startDiagnosticsShare.mockImplementationOnce(async () => { throw new Error("synthetic private failure"); });
  let root = await mount(<DiagnosticsSettings />);
  const click = () => root.root.findAllByType("DialogButton").find((button) => button.props["data-action-key"] === "shareDiagnostics")!.props.onClick();
  await act(async () => click());
  expect(text(root)).toContain("下载启动未确认");
  expect(text(root)).not.toContain("synthetic private failure");
  await unmount(root);
  root = await mount(<DiagnosticsSettings />);
  expect(api.startDiagnosticsShare).toHaveBeenCalledTimes(1);
  const refresh = root.root.findAllByType("Field").find((f) => f.props["data-action-key"] === "reloadSettings" && f.props["aria-label"] === "刷新状态")!;
  await act(async () => refresh.props.onActivate());
  await act(async () => click());
  expect(api.startDiagnosticsShare).toHaveBeenCalledTimes(2);
  expect(text(root)).toContain("诊断包已准备");
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, "navigator");
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: { clipboard: { writeText: async () => { throw new Error("denied"); } } } });
  try {
    const copy = root.root.findAllByType("DialogButton").find((button) => button.props["data-action-key"] === "copyDiagnostics")!;
    await act(async () => copy.props.onClick());
    expect(text(root)).toContain("复制失败");
    expect(text(root)).not.toContain("链接已复制");
    Object.defineProperty(globalThis, "navigator", { configurable: true, value: { clipboard: { writeText: async () => undefined } } });
    await act(async () => copy.props.onClick());
    expect(text(root)).toContain("链接已复制");
    const halt = root.root.findAllByType("Field").find((f) => f.props["data-action-key"] === "stopDiagnostics")!;
    await act(async () => halt.props.onActivate());
    expect(api.stopDiagnosticsShare).toHaveBeenCalledTimes(1);
    expect(text(root)).toContain("下载已停止");
    expect(text(root)).not.toContain("http://");
  } finally {
    if (descriptor) Object.defineProperty(globalThis, "navigator", descriptor);
    else Reflect.deleteProperty(globalThis, "navigator");
  }
  expect(api.setAutoCheck).not.toHaveBeenCalled();
  expect(api.setCheckNotifications).not.toHaveBeenCalled();
  expect(api.setNetworkMode).not.toHaveBeenCalled();
});

const storePlugin = (): PluginRow => ({ ...plugin, in_store: true, sideload: false,
  ownership: { state: "store", eligible: false, intent: null, error: null } });
const ownershipIntent = (action: "takeover" | "return" = "takeover", prior = false) => ({
  id: "a".repeat(32), name: plugin.name, folder: plugin.folder!, repo: plugin.repo!, prior_frozen: prior,
  action, state: "prepared" as const,
});
const installFrozenService = (update: (names: string[]) => Promise<void>, invalidate = async () => {}) => {
  (window as any).DeckyPluginLoader.frozenPluginsService = { update, invalidate };
};

test("normal store legacy binding and old update report stay out of default management", async () => {
  const store = storePlugin();
  listing = { ...listing, plugins: [store], last_check: { checkedAt: "2026-10-16T00:00:00Z", items: [{ name: store.name, repo: store.repo, current: store.version, latest: "2", status: "update", error: null }] } };
  const open = mock();
  const root = await mount(<Home selected={null} onOpen={open} />);
  expect(root.root.findAllByType("Field").some((f) => f.props.label === store.name)).toBe(false);
  expect(text(root)).not.toContain("待关联仓库");
  expect(text(root)).not.toContain("1 个可更新");
  const storePage = await mount(<StorePlugins selected={null} onOpen={open} />);
  const row = storePage.root.findAllByType("Field").find((f) => f.props.label === store.name)!;
  await act(async () => row.props.onActivate());
  expect(open).toHaveBeenCalledWith(store);
  expect(api.setBinding).not.toHaveBeenCalled();
  expect(api.beginStoreOwnership).not.toHaveBeenCalled();
});

test("store detail, binding and new-install match cannot expose mutation before explicit consent", async () => {
  const store = storePlugin(); listing = { ...listing, plugins: [store] };
  const details = await mount(<InstalledTab plugin={store} onBinding={() => {}} onOperation={() => {}} />);
  expect(text(details)).toContain("由商店负责");
  expect(buttons(details).map((b) => b.props.children)).toEqual(["接管商店插件"]);
  expect(api.getPluginReleases).not.toHaveBeenCalled();
  const existing = mock();
  const install = await mount(<InstallTab onExisting={existing} onOperation={() => {}} />);
  if (!install.root.findAllByType("TextField").length) await act(async () => buttons(install).find((b) => b.props.children === "直接填写仓库")!.props.onClick());
  await act(async () => install.root.findByType("TextField").props.onChange({ target: { value: "other/repo" } }));
  await act(async () => buttons(install).find((b) => b.props.children === "读取仓库")!.props.onClick());
  expect(existing).toHaveBeenCalledWith(store);
  expect(api.listReleases).not.toHaveBeenCalled();
});

test("takeover repository confirmation is explicit", async () => {
  const store = storePlugin();
  const root = await mount(<BindingEditor plugin={store} onSaved={() => {}} />);
  expect(text(root)).toContain("暂停它的商店更新检查");
  expect(api.beginStoreOwnership).not.toHaveBeenCalled();
  await act(async () => buttons(root).find((b) => b.props.children === "直接填写仓库")!.props.onClick());
  expect(buttons(root).some((b) => b.props.children === "接管并暂停商店检查")).toBe(true);
  expect(buttons(root).some((b) => b.props.children === "解除关联")).toBe(false);
});

test("ownership actions persist intent before native write, preserve latest unrelated names and prior freeze", async () => {
  const { changeOwnership } = await import("../src/ownershipController");
  for (const [action, prior] of [["takeover", false], ["return", false], ["return", true]] as const) {
    const order: string[] = [], intent = ownershipIntent(action, prior);
    api.beginStoreOwnership.mockImplementationOnce(async () => { order.push("intent"); return intent; });
    api.ownershipNativeStep.mockImplementation(async (_id?: unknown, phase?: unknown) => {
      order.push(String(phase)); return { intent, frozen: ["Unrelated", "Concurrent", plugin.name] };
    });
    const update = mock(async (names: string[]) => { order.push("native"); });
    installFrozenService(update, async () => { order.push("invalidate"); });
    expect(await changeOwnership(storePlugin(), action)).toBe(true);
    expect(order).toEqual(["intent", "invalidate", "writing", "native", "invalidate", "confirming", "complete"]);
    expect(update).toHaveBeenCalledWith(["Unrelated", "Concurrent", ...(action === "takeover" || prior ? [plugin.name] : [])]);
    expect(api.setBinding).not.toHaveBeenCalled();
  }
});

test("ownership native failure remains unconfirmed and recovery never replays automatically", async () => {
  const { changeOwnership } = await import("../src/ownershipController");
  installFrozenService(async () => { throw new Error("native partial failure"); });
  expect(await changeOwnership(storePlugin(), "takeover")).toBe(false);
  expect(api.ownershipNativeStep.mock.calls.map((call) => call[1])).toEqual(["writing"]);
  const pending: PluginRow = { ...storePlugin(), ownership: { state: "pending", eligible: false, error: null, intent: { ...ownershipIntent(), state: "writing" } } };
  listing = { ...listing, plugins: [pending] };
  const root = await mount(<InstalledTab plugin={pending} onBinding={() => {}} onOperation={() => {}} />);
  expect(text(root)).toContain("native partial failure");
  expect(buttons(root).map((b) => b.props.children)).toEqual(["回查接管结果", "显式继续冻结操作"]);
  expect(api.beginStoreOwnership).toHaveBeenCalledTimes(1);
  expect(api.ownershipNativeStep).toHaveBeenCalledTimes(1);
  installFrozenService(async () => { throw new Error("must not write during readback"); });
  await act(async () => buttons(root).find((b) => b.props.children === "回查接管结果")!.props.onClick());
  expect(api.ownershipNativeStep.mock.calls.map((call) => call[1])).toEqual(["writing", "read", "complete"]);
});

test("ownership single-flight survives QAM remount and timeout cannot activate from a late native write", async () => {
  const { changeOwnership, useOwnership } = await import("../src/ownershipController");
  let resolve!: () => void, timeout!: () => void;
  const update = mock(() => new Promise<void>((done) => { resolve = done; }));
  installFrozenService(update);
  const originalTimer = globalThis.setTimeout;
  globalThis.setTimeout = ((callback: () => void, delay: number, ...args: unknown[]) => delay === 10000 ? (timeout = callback, 12345) : originalTimer(callback, delay, ...args)) as typeof setTimeout;
  function Observer() { const state = useOwnership(); return <span data-busy={state.busy}>{state.error}</span>; }
  try {
    let first!: Promise<boolean>;
    await act(async () => { first = changeOwnership(storePlugin(), "takeover"); await Promise.resolve(); await Promise.resolve(); });
    const root = await mount(<Observer />);
    expect(root.root.findByType("span").props["data-busy"]).toBe(true);
    await unmount(root); await mount(<Observer />);
    expect(await changeOwnership(storePlugin(), "takeover")).toBe(false);
    await act(async () => timeout());
    expect(await first).toBe(false);
    expect(await changeOwnership(storePlugin(), "takeover")).toBe(false);
    await act(async () => resolve());
    expect(update).toHaveBeenCalledTimes(1);
    expect(api.ownershipNativeStep.mock.calls.map((call) => call[1])).toEqual(["writing"]);
  } finally { globalThis.setTimeout = originalTimer; resolve?.(); }
});

test("plugin unload during native write prevents late activation and reload performs no writes", async () => {
  const { changeOwnership, stopOwnershipController } = await import("../src/ownershipController");
  let resolve!: () => void;
  installFrozenService(() => new Promise<void>((done) => { resolve = done; }));
  const first = changeOwnership(storePlugin(), "takeover");
  for (let i = 0; i < 10 && !resolve; i++) await Promise.resolve();
  stopOwnershipController();
  expect(await first).toBe(false);
  resolve(); await Promise.resolve(); await Promise.resolve();
  expect(api.ownershipNativeStep.mock.calls.map((call) => call[1])).toEqual(["writing"]);
  const pending: PluginRow = { ...storePlugin(), ownership: { state: "pending", eligible: false, error: null, intent: { ...ownershipIntent(), state: "writing" } } };
  await mount(<InstalledTab plugin={pending} onBinding={() => {}} onOperation={() => {}} />);
  expect(api.beginStoreOwnership).toHaveBeenCalledTimes(1);
});

test("owned store uses return instead of generic unlink and active installation disables return", async () => {
  const owned: PluginRow = { ...storePlugin(), sideload: true, ownership: { state: "owned", eligible: true, error: null, intent: { ...ownershipIntent(), state: "active" } } };
  listing = { ...listing, plugins: [owned] };
  const root = await mount(<InstalledTab section="maintenance" plugin={owned} onBinding={() => {}} onOperation={() => {}} />);
  expect(buttons(root).some((b) => b.props.children === "交还商店")).toBe(true);
  expect(buttons(root).some((b) => b.props.children === "管理仓库关联")).toBe(false);
  expect(text(root)).toContain("原本冻结的插件仍保持冻结");
  operation = { ...makeOperation(), stage: "ready", existing_name: plugin.name };
  await act(async () => controller.refreshOperation());
  expect(buttons(root).find((b) => b.props.children === "交还商店")!.props.disabled).toBe(true);
  const editor = await mount(<BindingEditor plugin={owned} onSaved={() => {}} />);
  expect(buttons(editor)).toHaveLength(0);
});

test("old owned reports require current consent identity and return makes results historical", async () => {
  const intent = { ...ownershipIntent(), state: "active" as const };
  const owned: PluginRow = { ...storePlugin(), sideload: true, ownership: { state: "owned", eligible: true, error: null, intent } };
  const item = { name: plugin.name, repo: plugin.repo, current: plugin.version, latest: "2", status: "update" as const, error: null };
  const report = { checkedAt: new Date(Date.now() + 86400000).toISOString(), items: [item] };
  const { checkFor } = await import("../src/viewModel");
  expect(checkFor(owned, report)).toBeUndefined();
  expect(checkFor(owned, { ...report, items: [{ ...item, folder: plugin.folder!, ownership_id: intent.id }] })?.status).toBe("update");
  expect(checkFor({ ...owned, ownership: { ...owned.ownership!, eligible: false, state: "store" } }, report)).toBeUndefined();
  listing = { ...listing, plugins: [storePlugin()] };
  await checks.refreshPlugins();
  const root = await mount(<CheckResults report={report} selected={null} onOpen={() => {}} />);
  expect(text(root)).toContain("历史记录");
  expect(root.root.findAllByType("svg").some((node) => node.props.className === "native-updater-success")).toBe(false);
  listing = { ...listing, plugins: [owned] }; await checks.refreshPlugins();
  const open = mock();
  const staleConsent = await mount(<CheckResults report={report} selected={null} onOpen={open} />);
  await act(async () => staleConsent.root.findAllByType("Field").find((f) => f.props["data-check-key"])!.props.onActivate());
  expect(open).toHaveBeenCalledWith(item, report, null);
  const detail = await mount(<InstalledTab plugin={owned} checkRecord={{ item, checkedAt: report.checkedAt }} onBinding={() => {}} onOperation={() => {}} />);
  const record = detail.root.findAllByType("PanelSection").find((section) => section.props.title === "定时检查记录")!;
  expect(record.findAllByType("span").find((span) => span.props["data-status-tone"])!.props["data-status-tone"]).toBe("muted");
});

test("standalone native uninstall is backend-authorized and denied store ownership never dispatches", async () => {
  const uninstall = mock(); (window as any).DeckyPluginLoader.uninstallPlugin = uninstall;
  api.authorizePluginMutation.mockRejectedValueOnce(new Error("store responsibility"));
  await expect(controller.requestUninstall(plugin.name)).rejects.toThrow("store responsibility");
  expect(uninstall).not.toHaveBeenCalled();
});

test("newer selected pending and returned ownership cannot be reactivated by a stale cached list", async () => {
  const active = { ...ownershipIntent(), state: "active" as const };
  listing = { ...listing, plugins: [{ ...storePlugin(), sideload: true, ownership: { state: "owned", eligible: true, error: null, intent: active } }] };
  await checks.refreshPlugins();
  for (const state of ["pending", "store"] as const) {
    const selected: PluginRow = { ...storePlugin(), ownership: { state, eligible: false, error: null,
      intent: { ...active, id: "b".repeat(32), action: state === "store" ? "return" : "takeover", state: state === "store" ? "released" : "writing" } } };
    const root = await mount(<InstalledTab plugin={selected} onBinding={() => {}} onOperation={() => {}} />);
    expect(buttons(root).some((b) => ["卸载插件", "卸载并安装", "更新到 v2"].includes(b.props.children))).toBe(false);
    expect(api.getPluginReleases).not.toHaveBeenCalled();
  }
});

test("unknown metadata remains unowned with explicit nonstore repair binding available", async () => {
  const unknown: PluginRow = { ...plugin, in_store: null, sideload: false, repo_source: "package.json", ownership: { state: "unknown", eligible: false, error: null, intent: null } };
  listing = { ...listing, plugins: [unknown] }; await checks.refreshPlugins();
  const root = await mount(<InstalledTab plugin={unknown} onBinding={() => {}} onOperation={() => {}} />);
  expect(buttons(root).map((b) => b.props.children)).toEqual(["关联仓库以修复非商店插件追踪"]);
  expect(api.getPluginReleases).not.toHaveBeenCalled();
  const editor = await mount(<BindingEditor plugin={unknown} onSaved={() => {}} />);
  expect(buttons(editor).some((b) => b.props.children === "直接填写仓库")).toBe(true);
  expect(buttons(editor).some((b) => b.props.children === "接管并暂停商店检查")).toBe(false);
});

test("diagnostic pickup requires explicit address choice with multiple interfaces and explains absent LAN and expiry", async () => {
  api.getDiagnosticsAddresses.mockImplementation(async () => ({ addresses: ["192.168.2.5", "10.0.0.2"] }));
  const definition = makePlugin(); stop = definition.onDismount!;
  const root = await mount(<DiagnosticsSettings />);
  const action = () => root.root.findAllByType("DialogButton").find((b) => b.props["data-action-key"] === "shareDiagnostics")!;
  expect(action().props.disabled).toBe(true);
  const select = root.root.findAllByType("DropdownItem").find((d) => d.props.label === "下载局域网地址")!;
  await act(async () => select.props.onChange({ data: "10.0.0.2" }));
  expect(action().props.disabled).toBe(false);
  expect(api.startDiagnosticsShare).not.toHaveBeenCalled();
  api.getDiagnosticsAddresses.mockImplementation(async () => ({ addresses: [] }));
  const diagnostic = await import("../src/diagnosticsController");
  await act(async () => diagnostic.refreshDiagnosticShare());
  expect(text(root)).toContain("没有可用的私有 IPv4");
  expect(action().props.disabled).toBe(true);
  api.getDiagnosticsShare.mockImplementation(async () => ({ state: "expired", session: "a".repeat(32), package: diagnosticExport as any }));
  await act(async () => diagnostic.refreshDiagnosticShare());
  expect(text(root)).toContain("下载已过期");
  expect(text(root)).not.toContain("http://");
  expect(api.exportDiagnostics).not.toHaveBeenCalled();
});

test("diagnostic address draft accepts an open native dropdown after QAM remount without starting pickup", async () => {
  api.getDiagnosticsAddresses.mockImplementation(async () => ({ addresses: ["192.168.2.5", "10.0.0.2"] }));
  const definition = makePlugin(); stop = definition.onDismount!;
  let root = await mount(<DiagnosticsSettings />);
  const select = () => root.root.findAllByType("DropdownItem").find((d) => d.props.label === "下载局域网地址")!;
  const action = () => root.root.findAllByType("DialogButton").find((b) => b.props["data-action-key"] === "shareDiagnostics")!;
  const oldCallback = select().props.onChange;
  await unmount(root); root = await mount(<DiagnosticsSettings />);
  await act(async () => oldCallback({ data: "10.0.0.2" }));
  expect(select().props.selectedOption).toBe("10.0.0.2");
  expect(action().props.disabled).toBe(false);
  expect(api.startDiagnosticsShare).not.toHaveBeenCalled();
  await act(async () => action().props.onClick());
  expect(api.startDiagnosticsShare).toHaveBeenCalledTimes(1);
  expect(api.startDiagnosticsShare.mock.calls[0][0]).toBe("10.0.0.2");
  expect(api.exportDiagnostics).not.toHaveBeenCalled();
});

test("diagnostic stale dropdown cannot overwrite a newer choice or revive invalid addresses and sessions", async () => {
  api.getDiagnosticsAddresses.mockImplementation(async () => ({ addresses: ["192.168.2.5", "10.0.0.2"] }));
  const definition = makePlugin(); stop = definition.onDismount!;
  let root = await mount(<DiagnosticsSettings />);
  const diagnostic = await import("../src/diagnosticsController");
  const select = () => root.root.findAllByType("DropdownItem").find((d) => d.props.label === "下载局域网地址")!;
  const action = () => root.root.findAllByType("DialogButton").find((b) => b.props["data-action-key"] === "shareDiagnostics")!;
  const oldest = select().props.onChange;
  await act(async () => select().props.onChange({ data: "192.168.2.5" }));
  await act(async () => oldest({ data: "10.0.0.2" }));
  expect(select().props.selectedOption).toBe("192.168.2.5");
  const beforeAddressLoss = select().props.onChange;
  api.getDiagnosticsAddresses.mockImplementation(async () => ({ addresses: ["10.0.0.2"] }));
  await act(async () => diagnostic.refreshDiagnosticShare());
  expect(root.root.findAllByType("DropdownItem")).toHaveLength(0);
  expect(text(root)).toContain("点击使用此地址");
  expect(action().props.disabled).toBe(true);
  api.getDiagnosticsAddresses.mockImplementation(async () => ({ addresses: ["192.168.2.5", "10.0.0.2"] }));
  await act(async () => diagnostic.refreshDiagnosticShare());
  await act(async () => beforeAddressLoss({ data: "192.168.2.5" }));
  expect(select().props.selectedOption).toBe("");
  const beforeSession = select().props.onChange;
  api.getDiagnosticsShare.mockImplementation(async () => ({ state: "stopped", session: "b".repeat(32) }));
  await act(async () => diagnostic.refreshDiagnosticShare());
  await act(async () => beforeSession({ data: "10.0.0.2" }));
  expect(select().props.selectedOption).toBe("");
  const beforeUnload = select().props.onChange;
  await act(async () => {
    stop!(); stop = undefined;
    const again = makePlugin(); stop = again.onDismount!;
  });
  await unmount(root); root = await mount(<DiagnosticsSettings />);
  await act(async () => beforeUnload({ data: "10.0.0.2" }));
  expect(select().props.selectedOption).toBe("");
  expect(action().props.disabled).toBe(true);
  expect(api.startDiagnosticsShare).not.toHaveBeenCalled();
});

test("diagnostic stop during preparation ignores late success and stays stopped across QAM remount", async () => {
  let finish!: (value: DiagnosticShare) => void;
  api.startDiagnosticsShare.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  const definition = makePlugin(); stop = definition.onDismount!;
  let root = await mount(<DiagnosticsSettings />);
  const action = root.root.findAllByType("DialogButton").find((b) => b.props["data-action-key"] === "shareDiagnostics")!;
  await act(async () => action.props.onClick());
  const session = api.startDiagnosticsShare.mock.calls[0][1];
  const halt = root.root.findAllByType("Field").find((f) => f.props["data-action-key"] === "stopDiagnostics")!;
  await act(async () => halt.props.onActivate());
  await act(async () => finish({ state: "active", session, url: "http://synthetic-revoked", remaining: 300, downloads: 0 }));
  expect(text(root)).toContain("下载已停止");
  expect(text(root)).not.toContain("http://");
  await unmount(root); root = await mount(<DiagnosticsSettings />);
  expect(text(root)).toContain("下载已停止");
  expect(api.startDiagnosticsShare).toHaveBeenCalledTimes(1);
});

test("diagnostic clipboard capability and late completion cannot claim a new session was copied", async () => {
  const definition = makePlugin(); stop = definition.onDismount!;
  const root = await mount(<DiagnosticsSettings />);
  await act(async () => root.root.findAllByType("DialogButton").find((b) => b.props["data-action-key"] === "shareDiagnostics")!.props.onClick());
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, "navigator");
  try {
    Object.defineProperty(globalThis, "navigator", { configurable: true, value: {} });
    const copy = () => root.root.findAllByType("DialogButton").find((b) => b.props["data-action-key"] === "copyDiagnostics")!;
    await act(async () => copy().props.onClick());
    expect(text(root)).toContain("复制失败");
    let finish!: () => void;
    Object.defineProperty(globalThis, "navigator", { configurable: true, value: { clipboard: { writeText: () => new Promise<void>((resolve) => { finish = resolve; }) } } });
    await act(async () => copy().props.onClick());
    await act(async () => root.root.findAllByType("Field").find((f) => f.props["data-action-key"] === "stopDiagnostics")!.props.onActivate());
    await act(async () => finish());
    expect(text(root)).not.toContain("链接已复制");
    expect(text(root)).not.toContain("http://");
  } finally {
    if (descriptor) Object.defineProperty(globalThis, "navigator", descriptor);
    else Reflect.deleteProperty(globalThis, "navigator");
  }
});



test("task52 diagnostics is an explicit inner page with one-shot return focus and no start on entry", async () => {
  const definition = makePlugin(); stop = definition.onDismount!;
  let root = await mount(definition.content as React.ReactElement);
  await act(async () => buttons(root).find((b) => b.props.children === "设置")!.props.onClick());
  expect(root.root.findAllByType(DiagnosticsSettings)).toHaveLength(0);
  const entry = () => root.root.findAllByType("Field").find((f) => f.props["data-action-key"] === "diagnostics")!;
  await act(async () => entry().props.onActivate());
  expect(root.root.findAllByType(DiagnosticsSettings)).toHaveLength(1);
  expect(text(root)).not.toContain("领取");
  expect(root.root.findAllByType("DropdownItem")).toHaveLength(0);
  expect(api.startDiagnosticsShare).not.toHaveBeenCalled();
  await unmount(root); root = await mount(definition.content as React.ReactElement);
  expect(root.root.findAllByType(DiagnosticsSettings)).toHaveLength(1);
  expect(api.startDiagnosticsShare).not.toHaveBeenCalled();
  await act(async () => navigation(root).props.onCancel({ preventDefault() {}, stopPropagation() {} }));
  expect(entry().props.preferredFocus).toBe(true);
  await act(async () => navigation(root).props.onGamepadFocus());
  expect(entry().props.preferredFocus).toBe(false);
  expect(navigation(root).props.navEntryPreferPosition).toBe(0);
});


for (const dismissal of ["onCancel", "onEscKeypress", "closeModal", "popout"]) test(`task52 first reinstall confirmation ${dismissal} never prepares or mutates`, async () => {
  stop = controller.startInstallController(); await controller.refreshOperation();
  const pending = controller.startInstallation(release, release.zips[0].url, plugin, undefined, "reinstall");
  await act(async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); });
  expect(nativeModal!.props.strDescription).toContain("现在尚未备份");
  const duplicate = await controller.startInstallation(release, release.zips[0].url, plugin, undefined, "reinstall");
  expect(duplicate).toBe(false);
  if (dismissal === "popout") nativeModalClose!(); else nativeModal!.props[dismissal]();
  nativeModal!.props.onOK();
  expect(await pending).toBe(false);
  expect(api.prepareInstall).not.toHaveBeenCalled();
  expect(api.claimUninstall).not.toHaveBeenCalled();
  expect(loaderCall).not.toHaveBeenCalled();
});

test("task52 first confirmation is controller-owned across QAM but stale approval after reload is ignored", async () => {
  stop = controller.startInstallController(); await controller.refreshOperation();
  const pending = controller.startInstallation(release, release.zips[0].url, plugin, undefined, "reinstall");
  await act(async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); });
  const late = nativeModal!.props.onOK;
  let root = await mount(<InstalledTab plugin={plugin} onBinding={() => {}} onOperation={() => {}} />);
  await unmount(root); root = await mount(<InstalledTab plugin={plugin} onBinding={() => {}} onOperation={() => {}} />);
  expect(await controller.startInstallation(release, release.zips[0].url, plugin, undefined, "reinstall")).toBe(false);
  stop(); stop = controller.startInstallController(); await controller.refreshOperation();
  late(); expect(await pending).toBe(false);
  expect(api.prepareInstall).not.toHaveBeenCalled();
});

test("task52 cancel blocks a late claimed uninstall before the native side effect", async () => {
  let finish!: (op: InstallOperation) => void;
  api.claimUninstall.mockImplementationOnce(() => new Promise((done) => { finish = done; }));
  const { pending } = await beginReinstall();
  nativeModal!.props.onOK();
  await act(async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); });
  const claimed = { ...operation!, stage: "uninstalling" as const };
  await act(async () => controller.cancelCurrentOperation());
  await act(async () => finish(claimed)); await pending;
  expect(api.cancelOperation).toHaveBeenCalledTimes(1);
  expect(loaderCall).not.toHaveBeenCalled();
  await controller.dispatchPrepared(); expect(loaderCall).not.toHaveBeenCalled();
});

test("task52 cancel while native uninstall is outstanding waits its result and never installs", async () => {
  let finish!: () => void;
  loaderCall.mockImplementationOnce(() => new Promise<void>((done) => { finish = done; }));
  api.completeUninstall.mockImplementationOnce(async () => (operation = { ...operation!, stage: "cancelled" }));
  const { pending } = await beginReinstall(); nativeModal!.props.onOK();
  await act(async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); });
  expect(loaderCall).toHaveBeenCalledTimes(1);
  const root = await mount(<OperationPanel />);
  await act(async () => { void controller.cancelCurrentOperation(); void controller.cancelCurrentOperation(); });
  expect(api.cancelOperation).toHaveBeenCalledTimes(1);
  expect(text(root)).toContain("等待Decky卸载结果");
  expect(api.completeUninstall).not.toHaveBeenCalled();
  finish(); await act(async () => pending);
  expect(api.completeUninstall).toHaveBeenCalledTimes(1);
  expect(loaderCall).toHaveBeenCalledTimes(1);
});

test("task52 late reinstall install rejection after prompt and unknown cancellation never rechecks uninstall", async () => {
  let rejectInstall!: (error: Error) => void;
  const events = installEventBus();
  api.recordInstallEvent.mockImplementation(async (_id, kind, _name, value) => {
    if (kind === "uninstall_started") operation = { ...operation!, uninstall_started: true };
    if (kind === "prompt") operation = { ...operation!, request_id: value };
    if (kind === "cancel_unknown" || kind === "dispatch_error") operation = { ...operation!, stage: "unknown" };
    return operation;
  });
  loaderCall.mockImplementation(async (route: string) => {
    if (route === "utilities/install_plugin") return new Promise<void>((_resolve, reject) => { rejectInstall = reject; });
    if (route === "utilities/cancel_plugin_install") throw new Error("cancel reply lost");
  });
  const { pending } = await beginReinstall(); nativeModal!.props.onOK();
  await act(async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); });
  expect(loaderCall.mock.calls.filter((call) => call[0] === "utilities/install_plugin")).toHaveLength(1);
  expect(api.completeUninstall).toHaveBeenCalledTimes(1);
  await act(async () => {
    events.get("loader/add_plugin_install_prompt")!("Example", "2.0.0", "known", "a".repeat(64));
    await controller.refreshOperation();
    await controller.cancelCurrentOperation();
  });
  expect(operation!.stage).toBe("unknown");
  expect(operation!.cancel_requested).toBe(true);
  // Match the backend consequence of the erroneous second complete: absence
  // would cancel the task even though the native install is still unconfirmed.
  api.completeUninstall.mockImplementationOnce(async () => (operation = { ...operation!, stage: "cancelled" }));
  await act(async () => { rejectInstall(new Error("late install reply lost")); await pending; });
  expect(api.completeUninstall).toHaveBeenCalledTimes(1);
  expect(operation!.stage).toBe("unknown");
  expect(controller.isActive(operation)).toBe(true);
  expect(operation!.request_id).toBe("known");
  expect(api.recordInstallEvent).toHaveBeenCalledWith("job", "dispatch_error", "Example", "late install reply lost");
});

test("task52 cancelled reinstall retries only an outstanding uninstall check failure", async () => {
  let failCheck!: (error: Error) => void;
  api.completeUninstall.mockImplementationOnce(() => new Promise<InstallOperation>((_resolve, reject) => { failCheck = reject; }));
  const { pending } = await beginReinstall(); nativeModal!.props.onOK();
  await act(async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); });
  expect(api.completeUninstall).toHaveBeenCalledTimes(1);
  await controller.cancelCurrentOperation();
  api.completeUninstall.mockImplementationOnce(async () => (operation = { ...operation!, stage: "cancelled" }));
  await act(async () => { failCheck(new Error("cancelled during absence check")); await pending; });
  expect(api.completeUninstall).toHaveBeenCalledTimes(2);
  expect(operation!.stage).toBe("cancelled");
  expect(loaderCall.mock.calls.filter((call) => call[0] === "utilities/install_plugin")).toHaveLength(0);
});

test("task52 pending native cancellation failure is unknown and cannot be replayed or ended", async () => {
  stop = controller.startInstallController(); await controller.refreshOperation();
  operation = { ...makeOperation(), stage: "awaiting", request_id: "known" };
  await controller.refreshOperation();
  loaderCall.mockRejectedValueOnce(new Error("lost reply"));
  await controller.cancelCurrentOperation();
  expect(api.recordInstallEvent).toHaveBeenCalledWith("job", "cancel_unknown", "Example");
  await controller.cancelCurrentOperation();
  expect(loaderCall).toHaveBeenCalledTimes(1);
  const root = await mount(<OperationPanel />);
  expect(text(root)).toContain("取消结果无法确认");
  expect(buttons(root).some((b) => b.props.children === "我已处理 Decky 任务，结束跟踪")).toBe(false);
});

test("task52 native install started or uncorrelated dispatch disables cancellation but preserves result UI", async () => {
  for (const op of [{ ...makeOperation(), stage: "awaiting" as const }, { ...makeOperation(), stage: "installing" as const, request_id: "req", saw_start: true }, { ...makeOperation(), stage: "verified" as const }]) {
    operation = op; await controller.refreshOperation();
    const root = await mount(<OperationPanel />);
    const cancel = root.root.findAllByType("DialogButton").find((b) => b.props["data-action-key"] === "cancelRequest")!;
    expect(cancel.props.disabled).toBe(true);
    await act(async () => cancel.props.onClick());
    expect(api.cancelOperation).not.toHaveBeenCalled();
    await unmount(root);
  }
});


test("task52 release detail busy indicator is animated and disappears after real data arrives", async () => {
  const { promise, resolve: finish } = Promise.withResolvers<ReleaseReadResult>();
  api.getPluginReleases.mockImplementationOnce(() => promise);
  const root = await mount(<InstalledTab plugin={plugin} onBinding={() => {}} onOperation={() => {}} />);
  const busy = root.root.findAllByType("Field").find((f) => f.props["aria-busy"] === true)!;
  expect(busy.props.focusable).toBe(false);
  await act(async () => finish(releaseResult()));
  expect(root.root.findAllByType("Field").some(field => field.props["aria-busy"] === true)).toBe(false);
  expect(buttons(root).some((b) => b.props.children === "更新到 v2")).toBe(true);
});

test("task52 single uninstall keeps the Loader native-confirm wrapper, deduplicates pending auth and ignores stale authorization", async () => {
  stop = controller.startInstallController(); await controller.refreshOperation();
  let finish!: () => void;
  api.authorizePluginMutation.mockImplementationOnce(() => new Promise<void>((done) => { finish = done; }));
  const wrapper = mock(); (window as any).DeckyPluginLoader.uninstallPlugin = wrapper;
  const pending = controller.requestUninstall("Example");
  await controller.requestUninstall("Example");
  await act(async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); });
  expect(wrapper).not.toHaveBeenCalled();
  stop(); stop = controller.startInstallController(); await controller.refreshOperation();
  finish(); await pending;
  expect(wrapper).not.toHaveBeenCalled();
  await controller.requestUninstall("Example");
  expect(wrapper).toHaveBeenCalledWith("Example", "卸载 Example", "卸载", expect.stringContaining("标准 settings/data"));
  expect(loaderCall).not.toHaveBeenCalled();
});

test("task52 cancellation between persisted native-start authorization and its response prevents native call", async () => {
  let finish!: (op: InstallOperation) => void;
  api.recordInstallEvent.mockImplementationOnce((_id, _kind) => new Promise((done) => { finish = done; }));
  const { pending } = await beginReinstall(); nativeModal!.props.onOK();
  await act(async () => { for (let i = 0; i < 15; i++) await Promise.resolve(); });
  operation = { ...operation!, uninstall_started: true };
  await controller.cancelCurrentOperation();
  finish(operation); await pending;
  expect(loaderCall).not.toHaveBeenCalled();
  expect(api.recordInstallEvent).toHaveBeenCalledWith("job", "uninstall_not_started", "Example");
});

test("task52 cancellation before a late submit response records no native dispatch", async () => {
  stop = controller.startInstallController(); await controller.refreshOperation();
  operation = { ...makeOperation(), stage: "ready" }; await controller.refreshOperation();
  let finish!: (op: InstallOperation) => void;
  api.submitPrepared.mockImplementationOnce(() => new Promise((done) => { finish = done; }));
  const pending = controller.dispatchPrepared();
  const stale = { ...operation, stage: "awaiting" as const };
  await controller.cancelCurrentOperation(); finish(stale); await pending;
  expect(loaderCall).not.toHaveBeenCalled();
  expect(api.recordInstallEvent).toHaveBeenCalledWith("job", "dispatch_not_started", "Example");
});

test("task52 cancel RPC persistence failure stays visible and can only retry after authoritative refresh", async () => {
  stop = controller.startInstallController(); await controller.refreshOperation();
  operation = { ...makeOperation(), stage: "ready" }; await controller.refreshOperation();
  api.cancelOperation.mockRejectedValueOnce(new Error("disk full"));
  await controller.cancelCurrentOperation();
  const root = await mount(<OperationPanel />);
  expect(text(root)).toContain("disk full");
  await controller.cancelCurrentOperation(); expect(api.cancelOperation).toHaveBeenCalledTimes(1);
  await act(async () => controller.refreshOperation());
  expect(root.root.findAllByType("DialogButton").find((b) => b.props["data-action-key"] === "confirmInstall")!.props.disabled).toBe(true);
  expect(text(root)).toContain("已停止自动提交");
  await act(async () => controller.cancelCurrentOperation());
  expect(api.cancelOperation).toHaveBeenCalledTimes(2);
  expect(operation!.stage).toBe("cancelled");
  expect(loaderCall).not.toHaveBeenCalled();
});


test("task52 no operation has no notice and active tasks cannot hide risks", async () => {
  const definition = makePlugin(); stop = definition.onDismount!;
  const root = await mount(definition.content as React.ReactElement);
  const actions = () => buttons(root).filter((f) => ["viewTask", "dismissTask"].includes(f.props["data-action-key"]));
  expect(actions()).toHaveLength(0);
  operation = makeOperation(); await act(async () => controller.refreshOperation());
  expect(actions().filter((f) => f.props["data-action-key"] === "viewTask")).toHaveLength(1);
  expect(actions().filter((f) => f.props["data-action-key"] === "dismissTask")).toHaveLength(0);
  await controller.dismissTaskNotice("job");
  expect(api.dismissOperation).not.toHaveBeenCalled();
});

for (const stage of ["closed", "failed", "cancelled"] as const) test(`task52 ${stage} notice closes by precise ID and keeps settings access across QAM`, async () => {
  operation = { ...makeOperation(), stage, error: "preserved explanation", ...(stage === "closed" ? { outcome: "success", saw_finish: true, binding_saved: true } : {}) };
  const original = { ...operation };
  const definition = makePlugin(); stop = definition.onDismount!;
  let root = await mount(definition.content as React.ReactElement);
  const dismiss = () => buttons(root).find((f) => f.props["data-action-key"] === "dismissTask");
  expect(dismiss()).toBeTruthy();
  await act(async () => dismiss()!.props.onClick());
  expect(api.dismissOperation).toHaveBeenCalledWith("job");
  expect(dismiss()).toBeUndefined();
  expect(operation).toEqual({ ...original, notice_dismissed: true });
  expect(api.cancelOperation).not.toHaveBeenCalled();
  expect(api.closeOperation).not.toHaveBeenCalled();
  await unmount(root); root = await mount(definition.content as React.ReactElement);
  expect(dismiss()).toBeUndefined();
  await act(async () => buttons(root).find((b) => b.props.children === "设置")!.props.onClick());
  const history = buttons(root).find((b) => b.props.children === "查看最近安装任务")!;
  expect(history).toBeTruthy(); await act(async () => history.props.onClick());
  expect(text(root)).toContain("preserved explanation");
  expect(root.root.findAllByType(OperationPanel)).toHaveLength(1);
});

test("task52 late initial status cannot restore a dismissed notice and new task ID displays normally", async () => {
  operation = { ...makeOperation(), stage: "failed" };
  const definition = makePlugin(); stop = definition.onDismount!;
  const root = await mount(definition.content as React.ReactElement);
  let finish!: (op: InstallOperation) => void;
  api.getOperation.mockImplementationOnce(() => new Promise((done) => { finish = done; }));
  const stale = { ...operation };
  const reading = controller.refreshOperation(); await Promise.resolve();
  await act(async () => controller.dismissTaskNotice("job"));
  await act(async () => { finish(stale); await reading; });
  expect(buttons(root).some((f) => f.props["data-action-key"] === "dismissTask")).toBe(false);
  operation = { ...makeOperation(), id: "new-job" };
  await act(async () => controller.refreshOperation());
  expect(root.root.findAllByType("Field").some((f) => f.props["data-action-key"] === "viewTask")).toBe(true);
  await controller.dismissTaskNotice("job");
  expect(api.dismissOperation).toHaveBeenCalledTimes(1);
});

test("task notices follow the settings switch on every page and keep the settings entry", async () => {
  operation = { ...makeOperation(), stage: "closed", outcome: "success", saw_finish: true, binding_saved: true };
  const definition = makePlugin(); stop = definition.onDismount!;
  const root = await mount(definition.content as React.ReactElement);
  const homeNotice = () => root.root.findAllByType("Field").filter((field) => field.props["aria-label"] === "安装已完成，查看任务");
  expect(homeNotice()).toHaveLength(1);
  await act(async () => buttons(root).find((button) => button.props["data-action-key"] === "settings")!.props.onClick());
  const toggle = () => root.root.findAllByType("ToggleField").find((field) => field.props.label === "显示任务提示")!;
  expect(toggle().props.checked).toBe(true);
  await act(async () => toggle().props.onChange(false));
  expect(api.setShowHomeTaskNotice).toHaveBeenCalledWith(false);
  expect(toggle().props.checked).toBe(false);
  expect(homeNotice()).toHaveLength(0);
  expect(buttons(root).find((button) => button.props.children === "查看最近安装任务")).toBeTruthy();
  api.setShowHomeTaskNotice.mockRejectedValueOnce(new Error("cannot save notice"));
  await act(async () => toggle().props.onChange(true));
  expect(text(root)).toContain("cannot save notice");
  expect(toggle().props.checked).toBe(false);
  await act(async () => navigation(root).props.onCancel({ preventDefault() {}, stopPropagation() {} }));
  expect(homeNotice()).toHaveLength(0);
  operation = makeOperation();
  await act(async () => controller.refreshOperation());
  expect(root.root.findAllByType("Field").some((field) => field.props["data-action-key"] === "viewTask")).toBe(false);
  await act(async () => buttons(root).find((button) => button.props["data-action-key"] === "settings")!.props.onClick());
  await act(async () => toggle().props.onChange(true));
  await act(async () => navigation(root).props.onCancel({ preventDefault() {}, stopPropagation() {} }));
  expect(root.root.findAllByType("Field").some((field) => field.props["data-action-key"] === "viewTask")).toBe(true);
});

test("notice opt-out persists across remounts, preserves task evidence and can be re-enabled", async () => {
  operation = { ...makeOperation(), stage: "closed", outcome: "success", saw_start: true, saw_finish: true, binding_saved: true };
  const original = { ...operation };
  const definition = makePlugin(); stop = definition.onDismount!;
  let root = await mount(definition.content as React.ReactElement);
  const mute = () => buttons(root).find(button => button.props["data-action-key"] === "muteTaskNotice");
  api.setShowHomeTaskNotice.mockRejectedValueOnce(new Error("cannot save preference"));
  await act(async () => mute()!.props.onClick());
  expect(mute()).toBeTruthy();
  expect(api.dismissOperation).not.toHaveBeenCalled();
  expect(settings.showHomeTaskNotice).not.toBe(false);
  await act(async () => mute()!.props.onClick());
  expect(settings.showHomeTaskNotice).toBe(false);
  expect(operation).toEqual({ ...original, notice_dismissed: true });
  expect(api.cancelOperation).not.toHaveBeenCalled();
  expect(api.closeOperation).not.toHaveBeenCalled();
  operation = { ...original, id: "later-task" };
  await act(async () => controller.refreshOperation());
  await unmount(root); root = await mount(definition.content as React.ReactElement);
  expect(mute()).toBeUndefined();
  await act(async () => buttons(root).find(button => button.props["data-action-key"] === "settings")!.props.onClick());
  expect(buttons(root).some(button => button.props.children === "查看最近安装任务")).toBe(true);
  const toggle = root.root.findAllByType("ToggleField").find(field => field.props.label === "显示任务提示")!;
  expect(toggle.props.checked).toBe(false);
  await act(async () => toggle.props.onChange(true));
  await act(async () => navigation(root).props.onCancel({ preventDefault() {}, stopPropagation() {} }));
  expect(mute()).toBeTruthy();
});

test("saved notice opt-out hides banners before preference loading and on inner pages", async () => {
  settings = { ...settings, showHomeTaskNotice: false };
  operation = { ...makeOperation(), stage: "failed", error: "retained failure" };
  const gate = Promise.withResolvers<AppSettings>();
  api.getAppSettings.mockImplementationOnce(() => gate.promise);
  const definition = makePlugin(); stop = definition.onDismount!;
  let root = await mount(definition.content as React.ReactElement);
  const banners = () => root.root.findAllByProps({ className: "gss-task-notice" });
  expect(banners()).toHaveLength(0);
  await act(async () => gate.resolve(settings));
  await act(async () => root.root.findAllByType("Field").find(row => row.props.label === plugin.name)!.props.onActivate());
  expect(banners()).toHaveLength(0);
  await unmount(root); root = await mount(definition.content as React.ReactElement);
  expect(banners()).toHaveLength(0);
  await act(async () => navigation(root).props.onCancel({ preventDefault() {}, stopPropagation() {} }));
  await act(async () => buttons(root).find(button => button.props["data-action-key"] === "settings")!.props.onClick());
  expect(banners()).toHaveLength(0);
  await act(async () => buttons(root).find(button => button.props["data-action-key"] === "viewTask")!.props.onClick());
  expect(text(root)).toContain("retained failure");
  expect(operation.notice_dismissed).toBeUndefined();
});

test("a late preference read cannot restore banners after saving opt-out", async () => {
  operation = { ...makeOperation(), stage: "failed" };
  const oldSettings = { ...settings, showHomeTaskNotice: true };
  const gate = Promise.withResolvers<AppSettings>();
  api.getAppSettings.mockImplementationOnce(() => gate.promise);
  const definition = makePlugin(); stop = definition.onDismount!;
  const root = await mount(definition.content as React.ReactElement);
  await act(async () => buttons(root).find(button => button.props["data-action-key"] === "settings")!.props.onClick());
  const toggle = () => root.root.findAllByType("ToggleField").find(field => field.props.label === "显示任务提示")!;
  await act(async () => toggle().props.onChange(false));
  await act(async () => gate.resolve(oldSettings));
  expect(toggle().props.checked).toBe(false);
  expect(root.root.findAllByProps({ className: "gss-task-notice" })).toHaveLength(0);
  expect(settings.showHomeTaskNotice).toBe(false);
});

test("license reader retains expanded group and document selection across QAM with hierarchical return focus", async () => {
  const definition = makePlugin(); stop = definition.onDismount!;
  let root = await mount(definition.content as React.ReactElement);
  const click = async (key: string) => act(async () => buttons(root).find(button => button.props["data-action-key"] === key)!.props.onClick());
  const back = async () => act(async () => navigation(root).props.onCancel({ preventDefault() {}, stopPropagation() {} }));
  await click("settings"); await click("licenses");
  expect(buttons(root).some(button => button.props["data-action-key"] === "license-entry-sample")).toBe(false);
  await click("license-dependencies"); await click("license-entry-sample"); await click("license-document-0");
  await unmount(root); root = await mount(definition.content as React.ReactElement);
  expect(root.root.findAllByType("Field").filter(field => field.props.className === "gss-license-paragraph").map(field => field.props.label).join("")).toBe("Copyright Example\n\nPermission granted.\n");
  expect(api.getOpenSourceNotices).toHaveBeenCalledTimes(1);
  await back();
  expect(buttons(root).find(button => button.props["data-action-key"] === "license-document-0")!.props.preferredFocus).toBe(true);
  await back();
  expect(buttons(root).find(button => button.props["data-action-key"] === "license-entry-sample")!.props.preferredFocus).toBe(true);
  await back();
  expect(buttons(root).find(button => button.props["data-action-key"] === "licenses")!.props.preferredFocus).toBe(true);
  expect(api.prepareInstall).not.toHaveBeenCalled();
});


test("task52 dismiss failure does not hide notice and late response cannot replace newer task", async () => {
  operation = { ...makeOperation(), stage: "failed" };
  const definition = makePlugin(); stop = definition.onDismount!;
  const root = await mount(definition.content as React.ReactElement);
  api.dismissOperation.mockRejectedValueOnce(new Error("notice write failed"));
  await act(async () => controller.dismissTaskNotice("job"));
  expect(text(root)).toContain("notice write failed");
  expect(buttons(root).some((f) => f.props["data-action-key"] === "dismissTask")).toBe(true);
  let finish!: (op: InstallOperation) => void;
  api.dismissOperation.mockImplementationOnce(() => new Promise((done) => { finish = done; }));
  const stale = { ...operation, notice_dismissed: true };
  const dismissing = controller.dismissTaskNotice("job");
  operation = { ...makeOperation(), id: "new-task" };
  await act(async () => controller.refreshOperation());
  await act(async () => { finish(stale); await dismissing; });
  expect(text(root)).toContain("正在下载并检查安装包");
  expect(buttons(root).some((f) => f.props["data-action-key"] === "dismissTask")).toBe(false);
});


test("task52 actual confirmation wrapper forwards ordinary close and unmount into settle-once cancellation", async () => {
  stop = controller.startInstallController(); await controller.refreshOperation();
  const pending = controller.startInstallation(release, release.zips[0].url, plugin, undefined, "reinstall");
  await act(async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); });
  const modal = nativeModal!;
  const root = await mount(React.cloneElement(modal, { closeModal: mock() }));
  await act(async () => root.root.findByType("ConfirmModal").props.closeModal());
  expect(await pending).toBe(false);
  await unmount(root);
  modal.props.onOK();
  expect(api.prepareInstall).not.toHaveBeenCalled();
});


test("task52 old rendered task actions cannot cancel, dispatch or close a newer task", async () => {
  stop = controller.startInstallController(); await controller.refreshOperation();
  operation = { ...makeOperation(), stage: "ready" }; await controller.refreshOperation();
  const root = await mount(<OperationPanel />);
  const oldButtons = root.root.findAllByType("DialogButton").filter((b) => ["cancelRequest", "confirmInstall", "finishTracking"].includes(b.props["data-action-key"])).map((b) => b.props.onClick);
  operation = { ...makeOperation(), id: "new-task", stage: "ready" };
  await act(async () => controller.refreshOperation());
  await act(async () => { for (const click of oldButtons) click(); });
  expect(api.cancelOperation).not.toHaveBeenCalled();
  expect(api.submitPrepared).not.toHaveBeenCalled();
  expect(api.closeOperation).not.toHaveBeenCalled();
  expect(loaderCall).not.toHaveBeenCalled();
});


test("task53 ordinary mount, QAM remount and update check do not request source refresh", async () => {
  const element = <Home selected={null} onOpen={() => {}} />;
  let root = await mount(element);
  await unmount(root); root = await mount(element);
  await act(async () => checks.runCheck());
  expect(api.listSideloadPlugins.mock.calls.every((args) => args[0] !== true)).toBe(true);
  const settingsPage = await mount(<SettingsTab />);
  const refresh = settingsPage.root.findAllByType("Field").filter((b) => b.props["data-action-key"] === "refreshRecognition");
  api.listSideloadPlugins.mockClear();
  await act(async () => refresh[0]!.props.onClick());
  expect(api.listSideloadPlugins.mock.calls).toEqual([[true]]);
});

test("task53 remembered store responsibility stays hidden by default and has explicit takeover", async () => {
  const stored: PluginRow = { ...plugin, in_store:null, cached_in_store:true, sideload:false,
    ownership:{state:"store",eligible:false,intent:null,error:null} };
  listing = {...listing,plugins:[stored],store_ok:false,catalog:{source:"cache",timestamp:1000,stale:true,warning:"offline"}};
  const home = await mount(<Home selected={null} onOpen={() => {}} />);
  expect(home.root.findAllByType("Field").some((f) => f.props.label === "Example")).toBe(false);
  expect(text(home)).not.toMatch(/来源|缓存|目录时间|offline/);
  const store = await mount(<StorePlugins selected={null} onOpen={() => {}} />);
  expect(store.root.findAllByType("Field").some(f => f.props.label === "Example")).toBe(true);
  const detail = await mount(<InstalledTab plugin={stored} onBinding={() => {}} onOperation={() => {}} />);
  expect(buttons(detail).some((b) => b.props["data-action-key"] === "takeover")).toBe(true);
  expect(api.getPluginReleases).not.toHaveBeenCalled();
  const editor = await mount(<BindingEditor plugin={stored} onSaved={() => {}} />);
  expect(text(editor)).toContain("接管");
  expect(api.beginStoreOwnership).not.toHaveBeenCalled();
  expect(api.setBinding).not.toHaveBeenCalled();
});

test("task53 remembered nonstore management remains usable without source warnings", async () => {
  listing = {...listing,plugins:[{...plugin,in_store:null,repo_source:"remembered",memory_in_store:false,
    ownership:{state:"nonstore",eligible:true,intent:null,error:null}}],store_ok:false,
    catalog:{source:"cache",timestamp:1000,stale:true,warning:"offline"}};
  const root = await mount(<Home selected={null} onOpen={() => {}} />);
  expect(root.root.findAllByType("Field").some((f) => f.props.label === "Example")).toBe(true);
  expect(text(root)).not.toMatch(/商店来源|缓存|目录时间|offline/);
});

test("third-party selection requires consent and cancel preserves confirmed mode", async () => {
  settings.networkMode = "direct";
  const root = await mount(<NetworkSettings />);
  await act(async () => networkChoice(root, "ghfast")!.props.onActivate());
  const modal = await mount(nativeModal!);
  await act(async () => modal.root.findByType("ConfirmModal").props.onOK());
  expect(api.setNetworkMode).not.toHaveBeenCalled();
  await act(async () => modal.root.findByType("ToggleField").props.onChange(true));
  await act(async () => setLocale("ja"));
  expect(modal.root.findByType("ToggleField").props.checked).toBe(true);
  expect(modal.root.findByType("ConfirmModal").props.bOKDisabled).toBe(false);
  await act(async () => modal.root.findByType("ConfirmModal").props.onCancel());
  expect(api.setNetworkMode).not.toHaveBeenCalled();
  const { getViewState } = await import("../src/viewState");
  expect(getViewState().network.selection).toBeNull();
  expect(getViewState().network.settings?.networkMode).toBe("direct");
  expect(getViewState().network.busy).toBe(false);
});

test("language changes retain an unsaved install draft without replaying requests", async () => {
  const definition = makePlugin(); stop = definition.onDismount!;
  let root = await mount(definition.content!);
  await act(async () => buttons(root).find(b => b.props["data-action-key"] === "newInstall")!.props.onClick());
  await act(async () => root.root.findByType("TextField").props.onChange({ target: { value: "acme/unsaved" } }));
  const { getViewState } = await import("../src/viewState");
  const before = getViewState();
  for (const locale of ["en", "pt", "es", "ja", "ko", "zh-cn"] as const) {
    await act(async () => setLocale(locale));
    expect(root.root.findByType("TextField").props.value).toBe("acme/unsaved");
    expect(getViewState()).toBe(before);
  }
  await unmount(root); root = await mount(definition.content!);
  expect(root.root.findByType("TextField").props.value).toBe("acme/unsaved");
  expect(api.probeRepository).not.toHaveBeenCalled();
  expect(api.prepareInstall).not.toHaveBeenCalled();
});

test("home language recovery works without entering translated settings", async () => {
  const definition = makePlugin(); stop = definition.onDismount!;
  const root = await mount(definition.content!);
  const { getViewState } = await import("../src/viewState");
  const { getLocale } = await import("../src/i18n");
  const before = getViewState();
  const chooser = () => root.root.findByType("Dropdown");
  await act(async () => chooser().props.onChange({ data: "ko" }));
  expect(getLocale()).toBe("ko");
  await act(async () => chooser().props.onChange({ data: "en" }));
  expect(getLocale()).toBe("en");
  expect(getViewState()).toBe(before);
  expect(root.root.findAllByType("Field").some(row => row.props.label === plugin.name)).toBe(true);
  expect(api.setNetworkMode).not.toHaveBeenCalled();
  expect(api.prepareInstall).not.toHaveBeenCalled();
  expect(api.listReleases).not.toHaveBeenCalled();
});

test("backup cleanup requires explicit approval and cancellation leaves backups untouched", async () => {
  const root = await mount(<SettingsTab />);
  const cleanup = () => root.root.findAllByType("Field").find(row => row.props["data-action-key"] === "deleteBackups")!;
  await act(async () => cleanup().props.onActivate());
  expect(api.deleteBackups).not.toHaveBeenCalled();
  expect(cleanup().props.disabled).toBe(true);
  const modal = await mount(nativeModal!);
  await act(async () => modal.root.findByType("ConfirmModal").props.onCancel());
  expect(api.deleteBackups).not.toHaveBeenCalled();
  expect(cleanup().props.disabled).toBe(false);
});

test("backup cleanup failure remains visible and allows another explicit attempt", async () => {
  api.deleteBackups.mockRejectedValueOnce(new Error("backup permission denied"));
  const root = await mount(<SettingsTab />);
  const cleanup = () => root.root.findAllByType("Field").find(row => row.props["data-action-key"] === "deleteBackups")!;
  await act(async () => cleanup().props.onActivate());
  const modal = await mount(nativeModal!);
  await act(async () => modal.root.findByType("ConfirmModal").props.onOK());
  expect(text(root)).toContain("backup permission denied");
  expect(cleanup().props.disabled).toBe(false);
  expect(api.deleteBackups).toHaveBeenCalledTimes(1);
});

test("cleanup refresh removes the deleted backup location without discarding task evidence", async () => {
  operation = { ...makeOperation(), stage: "closed", outcome: "success", artifact_deleted: true,
    backup: { archive: "/prepared/data-backup.zip", bytes: 1024, files: 2, paths: ["settings/example"], sha256: "b".repeat(64), scope: "standard", created_at: "2026-10-26T00:00:00Z" } };
  await act(async () => controller.refreshOperation());
  api.deleteBackups.mockImplementationOnce(async () => {
    operation = { ...operation!, backup_deleted: true };
    return { deleted: 1, bytes: 1024, skipped: 0, failed: 0 };
  });
  const root = await mount(<SettingsTab />);
  await act(async () => root.root.findAllByType("Field").find(row => row.props["data-action-key"] === "deleteBackups")!.props.onActivate());
  const modal = await mount(nativeModal!);
  await act(async () => modal.root.findByType("ConfirmModal").props.onOK());
  const panel = await mount(<OperationPanel />);
  await act(async () => panel.root.findAllByType("DialogButton").find(button => button.props["aria-expanded"] === false)!.props.onClick());
  expect(text(panel)).not.toContain("/prepared/data-backup.zip");
  expect(text(panel)).toContain("settings/example");
  let outcome: string | undefined;
  function InstallationConsumer() { outcome = controller.useInstallation().operation?.outcome; return null; }
  await mount(<InstallationConsumer />);
  expect(outcome).toBe("success");
});

test("repository map completion wins over an older status read", async () => {
  const { RepositoryMapSettings } = await import("../src/RepositoryMapSettings");
  const { Status } = await import("../src/components");
  let finishRead!: (value: Awaited<ReturnType<typeof api.getRepositoryMapStatus>>) => void;
  api.getRepositoryMapStatus.mockImplementationOnce(() => new Promise(resolve => { finishRead = resolve; }));
  const root = await mount(<RepositoryMapSettings />);
  await act(async () => { buttons(root).find(button => button.props["data-action-key"] === "updateRepositoryMap")!.props.onClick(); });
  await act(async () => { finishRead({ revision: 1, updated_at: "2026-10-27", count: 35, source: "bundled", warning: null }); });
  const titles = root.root.findAllByType(Status).map(node => String(node.props.title));
  expect(titles.some(title => title.includes("2") && title.includes("36"))).toBe(true);
  expect(titles.some(title => title.includes("1") && title.includes("35"))).toBe(false);
});

test("repository map failure retains current metadata and allows retry", async () => {
  const { RepositoryMapSettings } = await import("../src/RepositoryMapSettings");
  const { Status } = await import("../src/components");
  api.updateRepositoryMap.mockRejectedValueOnce(new Error("network offline"));
  const root = await mount(<RepositoryMapSettings />);
  const action = () => buttons(root).find(button => button.props["data-action-key"] === "updateRepositoryMap")!;
  await act(async () => { action().props.onClick(); });
  expect(root.root.findAllByType(Status).some(node => node.props.tone === "error" && node.props.children === "network offline")).toBe(true);
  expect(root.root.findAllByType(Status).some(node => String(node.props.title).includes("35"))).toBe(true);
  expect(action().props.disabled).toBe(false);
  await act(async () => { action().props.onClick(); });
  expect(root.root.findAllByType(Status).some(node => node.props.tone === "error")).toBe(false);
  expect(root.root.findAllByType(Status).some(node => String(node.props.title).includes("36"))).toBe(true);
});

test("repository map update remains in flight across a QAM remount", async () => {
  const { RepositoryMapSettings } = await import("../src/RepositoryMapSettings");
  const { Status } = await import("../src/components");
  let finish!: (value: Awaited<ReturnType<typeof api.updateRepositoryMap>>) => void;
  api.updateRepositoryMap.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  const root = await mount(<RepositoryMapSettings />);
  await act(async () => { buttons(root).find(button => button.props["data-action-key"] === "updateRepositoryMap")!.props.onClick(); });
  await unmount(root);
  const reopened = await mount(<RepositoryMapSettings />);
  const action = () => buttons(reopened).find(button => button.props["data-action-key"] === "updateRepositoryMap")!;
  expect(action().props["aria-busy"]).toBe(true);
  await act(async () => { finish({ revision: 2, updated_at: "2026-10-28", count: 36, source: "downloaded", warning: null, changed: true }); });
  expect(action().props["aria-busy"]).toBe(false);
  expect(api.updateRepositoryMap).toHaveBeenCalledTimes(1);
  expect(reopened.root.findAllByType(Status).some(node => String(node.props.title).includes("36"))).toBe(true);
});
