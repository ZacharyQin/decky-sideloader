import { t, useLocale } from "./i18n";
import { recordDiagnostic } from "./diagnosticsController";
import { createElement, useEffect, useState } from "react";
import type { ConfirmModalProps } from "@decky/ui";
import { validateLoaderPlugins, type LoaderSnapshot } from "./loaderManagement";
import type { InstallOperation } from "./types";
import type { SelfUpdateOperation } from "./types";
import { errorText } from "./errors";

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
  DeckyPluginLoader?: { deckyState: { closeActivePlugin: () => void }; frozenPluginsService?: { update: (names: string[]) => Promise<void>; invalidate: () => Promise<void> }; uninstallPlugin: (name: string, title: string, buttonText: string, description: string) => void };
};
const host = () => window as LoaderWindow;
const SELF_NAME = "Sideloader";

export function returnToPluginList(): void {
  host().DeckyPluginLoader!.deckyState.closeActivePlugin();
}

export function probeLoaderChannel(): ChannelProbe {
  const backend = host().DeckyBackend;
  if (typeof backend?.call !== "function") return { ok: false, reason: t("Decky 安装接口不可用") };
  if (typeof backend.addEventListener !== "function" || typeof backend.removeEventListener !== "function") return { ok: false, reason: t("此 Loader 版本无法跟踪原生安装确认，请更新 Loader") };
  if (typeof host().DeckyPluginLoader?.uninstallPlugin !== "function") return { ok: false, reason: t("Decky 卸载接口不可用") };
  return { ok: true, reason: "ok" };
}

export async function installFromArtifact(operation: InstallOperation, installType: InstallType): Promise<unknown> {
  const probe = probeLoaderChannel();
  if (!probe.ok) throw new Error(probe.reason);
  const pkg = operation.package;
  if (operation.stage !== "awaiting" || !pkg || !operation.artifact.startsWith("file://") || !/^[a-f0-9]{64}$/.test(pkg.sha256) || pkg.name === SELF_NAME) {
    throw new Error(t("安装任务未通过本地预检，拒绝提交"));
  }
  return host().DeckyBackend!.call("utilities/install_plugin", operation.artifact, pkg.name, pkg.version, pkg.sha256, installType);
}

export async function installSelfUpdate(operation: SelfUpdateOperation): Promise<unknown> {
  const probe = probeLoaderChannel();
  if (!probe.ok) throw new Error(probe.reason);
  const pkg = operation.package;
  if (operation.stage !== "awaiting" || !pkg || pkg.name !== SELF_NAME || pkg.folder !== "decky-sideloader" ||
    pkg.version !== operation.release.version || pkg.sha256 !== operation.release.sha256 || !/^[a-f0-9]{64}$/.test(pkg.sha256) ||
    !operation.artifact.startsWith("file:///") || !operation.artifact.endsWith(`/self-update/${operation.id}/plugin.zip`)) {
    throw new Error(t("安装任务未通过本地预检，拒绝提交"));
  }
  return host().DeckyBackend!.call("utilities/install_plugin", operation.artifact, SELF_NAME, pkg.version, pkg.sha256, InstallType.UPDATE);
}

export function listenForInstallEvents(onEvent: (kind: string, name: string, value: string, sha256: string) => void): () => void {
  const backend = host().DeckyBackend;
  if (!backend?.addEventListener || !backend.removeEventListener) return () => {};
  let disposed = false;
  let activeName: string | null = null;
  const prompts = new Map<string, string>();
  const originalCall = backend.call;
  const observedCall: DeckyBackend["call"] = async function (route, ...args) {
    const requestId = String(args[0] ?? "");
    const cancelling = route === "utilities/cancel_plugin_install";
    const name = route === "utilities/confirm_plugin_install" || cancelling ? prompts.get(requestId) : undefined;
    try {
      const result = await originalCall.call(backend, route, ...args);
      if (!disposed && name) onEvent(cancelling ? "cancelled" : "confirm_returned", name, "", requestId);
      if (!disposed && route === "utilities/uninstall_plugin") onEvent("uninstall_returned", requestId, "", "");
      return result;
    } catch (error) {
      if (!disposed && name) {
        onEvent(cancelling ? "cancel_unknown" : "install_error", name, errorText(error), requestId);
      }
      throw error;
    } finally {
      if (name) prompts.delete(requestId);
      if (name === activeName) activeName = null;
    }
  };
  const listeners: [string, Listener][] = [
    ["loader/add_plugin_install_prompt", (name, _version, requestId, sha256) => {
      if (prompts.size >= 32) prompts.delete(prompts.keys().next().value!);
      prompts.set(requestId, name);
      onEvent("prompt", name, requestId, sha256);
    }],
    ["loader/plugin_download_start", (name) => { activeName = name; onEvent("start", name, "", ""); }],
    ["loader/plugin_download_info", (_percent, step) => {
      if (activeName && typeof step === "string") onEvent("progress", activeName, step, "");
    }],
    ["loader/plugin_download_finish", (name) => { onEvent("finish", name, "", ""); if (activeName === name) activeName = null; }],
  ];
  backend.call = observedCall;
  for (const [event, listener] of listeners) backend.addEventListener(event, listener);
  return () => {
    disposed = true;
    if (backend.call === observedCall) backend.call = originalCall;
    prompts.clear();
    for (const [event, listener] of listeners) backend.removeEventListener?.(event, listener);
  };
}

export async function cancelPendingInstall(requestId: string): Promise<void> {
  if (!requestId || !probeLoaderChannel().ok) throw new Error(t("无法取消此确认请求"));
  await host().DeckyBackend!.call("utilities/cancel_plugin_install", requestId);
}

export async function uninstallPlugin(name: string, current = () => true): Promise<void> {
  if (name === SELF_NAME) throw new Error(t("不能卸载 Sideloader 自身"));
  const probe = probeLoaderChannel();
  if (!probe.ok) throw new Error(probe.reason);
  const { authorizePluginMutation } = await import("./api");
  await authorizePluginMutation(name);
  if (!current()) return;
  host().DeckyPluginLoader!.uninstallPlugin(name, t("卸载 {0}", [name]), t("卸载"), t("将移除插件程序。Decky 标准 settings/data 目录保留；插件自定义卸载行为可能影响其他数据。"));
}

// The Loader's own uninstall modal calls this route; its void showModal wrapper
// cannot tell us whether this operation was cancelled or completed.
async function nativeConfirmation(props: () => ConfirmModalProps, requireConsent = false): Promise<boolean> {
  const { ConfirmModal, showModal, ToggleField } = await import("@decky/ui");
  return new Promise((resolve) => {
    let settled = false;
    const settle = (confirmed: boolean) => { if (!settled) { settled = true; resolve(confirmed); } };
    const close = () => { settle(false); modal.Close(); };
    // showModal injects closeModal into its root. Forward cancellation explicitly,
    // including ordinary dismissals and unmounts; fnOnClose only covers popouts.
    function Confirmation(input: ConfirmModalProps) {
      useLocale();
      const [consent, setConsent] = useState(false);
      useEffect(() => () => settle(false), []);
      return createElement(ConfirmModal, { ...input, ...props(), strCancelButtonText: t("取消"), closeModal: close, onCancel: close, onEscKeypress: close,
        bOKDisabled: requireConsent && !consent, onOK: () => { if (!requireConsent || consent) input.onOK?.(); },
      }, requireConsent ? createElement(ToggleField, { label: t("我理解并同意使用第三方服务"), checked: consent, onChange: setConsent }) : input.children);
    }
    const modal = showModal(createElement(Confirmation, { ...props(),
      strCancelButtonText: t("取消"),
      onOK: () => { settle(true); modal.Close(); },
      onCancel: close, closeModal: close, onEscKeypress: close,
    }), undefined, { fnOnClose: () => settle(false) });
  });
}

export function confirmDeleteBackups(): Promise<boolean> {
  return nativeConfirmation(() => ({ strTitle: t("清理数据备份？"), strOKButtonText: t("清理备份"),
    strDescription: t("删除所有非活动任务的数据备份，包括最近 7 天的备份。活动任务的备份和插件原始数据不会删除。"),
    bDestructiveWarning: true,
  }));
}

export function confirmNetworkMode(): Promise<boolean> {
  return nativeConfirmation(() => ({ strTitle: t("使用第三方加速？"), strOKButtonText: t("同意并使用"),
    strDescription: t("GH-Proxy：https://gh-proxy.org/；GHFast：https://ghfast.top/。第三方可见查询与下载内容，也可修改返回内容。TLS 和 ZIP 校验不保证作者或代码可信。不改变系统代理，进行中的任务保留开始时的模式。"),
  }), true);
}
export function confirmInstallWarnings(warnings: string[]): Promise<boolean> {
  return nativeConfirmation(() => ({ strTitle: t("安装前请核对"),
    strDescription: warnings.map(warning => warning === "安装包未附带 LICENSE，请自行核对作者的分发许可。" ? t("安装包未附带 LICENSE，请自行核对作者的分发许可。")
      : warning === "此插件声明了 root 权限，请仅安装可信作者的发布。" ? t("此插件声明了 root 权限，请仅安装可信作者的发布。") : warning).join("\n\n"),
    strOKButtonText: t("继续安装"),
  }));
}


export function confirmPrepareReinstall(name: string): Promise<boolean> {
  return nativeConfirmation(() => ({ strTitle: t("卸载并安装 {0}", [name]),
    strDescription: t("先下载并核验安装包，备份 Decky 标准 settings/data 中的数据。准备完成后仍需再次确认卸载。现在尚未备份，不会自动恢复或回滚。"),
    strOKButtonText: t("开始准备") }));
}

export function confirmReinstall(operation: InstallOperation): Promise<boolean> {
  return nativeConfirmation(() => ({
    strTitle: t("卸载并安装 {0}", [operation.package!.name]),
    strDescription: t("先卸载插件程序，再打开 Decky 安装确认。已备份 {0} 个文件，范围：{1}。Loader 保留标准数据目录，但插件卸载钩子可能修改或删除数据。备份不包含自定义路径，不自动恢复或回滚。", [operation.backup?.files ?? 0, operation.backup?.paths.join("、") || t("标准 settings/data 中未发现数据")]),
    strOKButtonText: t("卸载并继续"), bDestructiveWarning: true,
  }));
}

export async function uninstallForReinstall(operation: InstallOperation): Promise<void> {
  if (operation.stage !== "uninstalling" || operation.mode !== "reinstall" || !operation.package || operation.package.name === SELF_NAME) throw new Error(t("卸载任务尚未领取"));
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    await Promise.race([
      host().DeckyBackend!.call("utilities/uninstall_plugin", operation.package.name),
      new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new Error(t("卸载结果超时，不会继续安装"))), 120000); }),
    ]);
  } finally { clearTimeout(timer); }
}

// Independent read-only channel: installation event/uninstall APIs are not required.
export async function readLoaderPlugins(timeoutMs = 5000): Promise<LoaderSnapshot> {
  const started = Date.now();
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    const backend = host().DeckyBackend;
    if (typeof backend?.call !== "function") throw new Error(t("Loader 管理接口不可用"));
    const value = await Promise.race([
      backend.call("loader/get_plugins"),
      new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new Error(t("Loader 管理读取超时"))), timeoutMs); }),
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
  if (typeof service?.update !== "function" || typeof service.invalidate !== "function") throw new Error(t("此 Loader 的原生冻结接口不可用，未执行接管"));
  return service;
}
export async function invalidateFrozenPlugins(): Promise<void> { await frozenService().invalidate(); }
export async function updateFrozenPlugins(names: string[]): Promise<void> { await frozenService().update(names); }
