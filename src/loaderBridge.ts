// Semantic index:
// - participant: docs/upstream-conflict/sideload/plugin-management.md -- settle native confirmations once, retain Loader uninstall confirmation and verified native management routes.
// - participant: docs/upstream-conflict/sideload/diagnostics.md -- observe only read-only management result counts without changing the install channel.
import { recordDiagnostic } from "./diagnosticsController";
import { createElement, useEffect } from "react";
import type { ConfirmModalProps } from "@decky/ui";
import { validateLoaderPlugins, type LoaderSnapshot } from "./loaderManagement";
import type { InstallOperation } from "./types";

export enum InstallType { INSTALL = 0, REINSTALL = 1, UPDATE = 2, DOWNGRADE = 3, OVERWRITE = 4 }
export type ChannelProbe = { ok: boolean; reason: string };
type Listener = (...args: any[]) => void;
type DeckyBackend = {
  call: (route: string, ...args: unknown[]) => Promise<unknown>;
  addEventListener?: (event: string, listener: Listener) => unknown;
  removeEventListener?: (event: string, listener: Listener) => void;
};
type LoaderWindow = Window & {
  DeckyBackend?: DeckyBackend;
  DeckyPluginLoader?: { frozenPluginsService?: { update: (names: string[]) => Promise<void>; invalidate: () => Promise<void> }; uninstallPlugin: (name: string, title: string, buttonText: string, description: string) => void };
};
const host = () => window as LoaderWindow;
const SELF_NAME = "GitHub Sideload";

export function probeLoaderChannel(): ChannelProbe {
  const backend = host().DeckyBackend;
  if (typeof backend?.call !== "function") return { ok: false, reason: "Decky 安装接口不可用" };
  if (typeof backend.addEventListener !== "function" || typeof backend.removeEventListener !== "function") return { ok: false, reason: "此 Loader 版本无法跟踪原生安装确认，请更新 Loader" };
  if (typeof host().DeckyPluginLoader?.uninstallPlugin !== "function") return { ok: false, reason: "Decky 卸载接口不可用" };
  return { ok: true, reason: "ok" };
}

export async function installFromArtifact(operation: InstallOperation, installType: InstallType): Promise<unknown> {
  const probe = probeLoaderChannel();
  if (!probe.ok) throw new Error(probe.reason);
  const pkg = operation.package;
  if (operation.stage !== "awaiting" || !pkg || !operation.artifact.startsWith("file://") || !/^[a-f0-9]{64}$/.test(pkg.sha256) || pkg.name === SELF_NAME) {
    throw new Error("安装任务未通过本地预检，拒绝提交");
  }
  return host().DeckyBackend!.call("utilities/install_plugin", operation.artifact, pkg.name, pkg.version, pkg.sha256, installType);
}

export function listenForInstallEvents(onEvent: (kind: string, name: string, value: string, sha256: string) => void): () => void {
  const backend = host().DeckyBackend;
  if (!backend?.addEventListener || !backend.removeEventListener) return () => {};
  const listeners: [string, Listener][] = [
    ["loader/add_plugin_install_prompt", (name, _version, requestId, sha256) => onEvent("prompt", name, requestId, sha256)],
    ["loader/plugin_download_start", (name) => onEvent("start", name, "", "")],
    ["loader/plugin_download_finish", (name) => onEvent("finish", name, "", "")],
  ];
  for (const [event, listener] of listeners) backend.addEventListener(event, listener);
  return () => { for (const [event, listener] of listeners) backend.removeEventListener?.(event, listener); };
}

export async function cancelPendingInstall(requestId: string): Promise<void> {
  if (!requestId || !probeLoaderChannel().ok) throw new Error("无法取消此确认请求");
  await host().DeckyBackend!.call("utilities/cancel_plugin_install", requestId);
}

export async function uninstallPlugin(name: string, current = () => true): Promise<void> {
  if (name === SELF_NAME) throw new Error("不能卸载 GitHub Sideload 自身");
  const probe = probeLoaderChannel();
  if (!probe.ok) throw new Error(probe.reason);
  const { authorizePluginMutation } = await import("./api");
  await authorizePluginMutation(name);
  if (!current()) return;
  host().DeckyPluginLoader!.uninstallPlugin(name, `卸载 ${name}`, "卸载", "将移除插件程序。Decky 标准 settings/data 目录保留；插件自定义卸载行为可能影响其他数据。");
}

// The Loader's own uninstall modal calls this route; its void showModal wrapper
// cannot tell us whether this operation was cancelled or completed.
async function nativeConfirmation(props: ConfirmModalProps): Promise<boolean> {
  const { ConfirmModal, showModal } = await import("@decky/ui");
  return new Promise((resolve) => {
    let settled = false;
    const settle = (confirmed: boolean) => { if (!settled) { settled = true; resolve(confirmed); } };
    const close = () => { settle(false); modal.Close(); };
    // showModal injects closeModal into its root. Forward cancellation explicitly,
    // including ordinary dismissals and unmounts; fnOnClose only covers popouts.
    function Confirmation(input: ConfirmModalProps) {
      useEffect(() => () => settle(false), []);
      return createElement(ConfirmModal, { ...input, closeModal: close, onCancel: close, onEscKeypress: close });
    }
    const modal = showModal(createElement(Confirmation, { ...props,
      strCancelButtonText: "取消",
      onOK: () => { settle(true); modal.Close(); },
      onCancel: close, closeModal: close, onEscKeypress: close,
    }), undefined, { fnOnClose: () => settle(false) });
  });
}

export function confirmPrepareReinstall(name: string): Promise<boolean> {
  return nativeConfirmation({ strTitle: `卸载并安装 ${name}`,
    strDescription: "先下载并核验安装包，备份 Decky 标准 settings/data 中的数据。准备完成后仍需再次确认卸载。现在尚未备份，不会自动恢复或回滚。",
    strOKButtonText: "开始准备" });
}

export function confirmReinstall(operation: InstallOperation): Promise<boolean> {
  return nativeConfirmation({
    strTitle: `卸载并安装 ${operation.package!.name}`,
    strDescription: `先卸载插件程序，再打开 Decky 安装确认。已备份 ${operation.backup?.files ?? 0} 个文件，范围：${operation.backup?.paths.join("、") || "标准 settings/data 中未发现数据"}。Loader 保留标准数据目录，但插件卸载钩子可能修改或删除数据。备份不包含自定义路径，不自动恢复或回滚。`,
    strOKButtonText: "卸载并继续", bDestructiveWarning: true,
  });
}

export async function uninstallForReinstall(operation: InstallOperation): Promise<void> {
  if (operation.stage !== "uninstalling" || operation.mode !== "reinstall" || !operation.package || operation.package.name === SELF_NAME) throw new Error("卸载任务尚未领取");
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    await Promise.race([
      host().DeckyBackend!.call("utilities/uninstall_plugin", operation.package.name),
      new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new Error("卸载结果超时，不会继续安装")), 120000); }),
    ]);
  } finally { clearTimeout(timer); }
}

// Independent read-only channel: installation event/uninstall APIs are not required.
export async function readLoaderPlugins(timeoutMs = 5000): Promise<LoaderSnapshot> {
  const started = Date.now();
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    const backend = host().DeckyBackend;
    if (typeof backend?.call !== "function") throw new Error("Loader 管理接口不可用");
    const value = await Promise.race([
      backend.call("loader/get_plugins"),
      new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new Error("Loader 管理读取超时")), timeoutMs); }),
    ]);
    const plugins = validateLoaderPlugins(value);
    recordDiagnostic({ event: "loader_read", ok: true, count: plugins.length, durationMs: Math.max(0, Date.now() - started) });
    return { ok: true, plugins };
  } catch (error) {
    recordDiagnostic({ event: "loader_read", ok: false, count: 0, durationMs: Math.max(0, Date.now() - started) });
    return { ok: false, error: error instanceof Error ? error.message : String(error) };
  } finally { clearTimeout(timer); }
}

// Use the verified native service so both persisted settings and current update
// state change together. This array write is not a cross-application transaction.
function frozenService() {
  const service = host().DeckyPluginLoader?.frozenPluginsService;
  if (typeof service?.update !== "function" || typeof service.invalidate !== "function") throw new Error("此 Loader 的原生冻结接口不可用，未执行接管");
  return service;
}
export async function invalidateFrozenPlugins(): Promise<void> { await frozenService().invalidate(); }
export async function updateFrozenPlugins(names: string[]): Promise<void> { await frozenService().update(names); }
