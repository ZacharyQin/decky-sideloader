import { t } from "./i18n";
import { useSyncExternalStore } from "react";
import { claimUninstall, completeUninstall, cancelOperation, continuePrepared, dismissOperation, closeOperation, deleteInstallPackage, getOperation, prepareInstall, recordInstallEvent, retryDownload, submitPrepared } from "./api";
import { errorText } from "./errors";
import { cancelPendingInstall, confirmInstallWarnings, confirmPrepareReinstall, confirmReinstall, uninstallForReinstall, InstallType, installFromArtifact, listenForInstallEvents, probeLoaderChannel, uninstallPlugin } from "./loaderBridge";
import type { InstallOperation, PluginRow, ReleaseRow } from "./types";
import { versionAction } from "./viewModel";
import { refreshAfterPluginMutation } from "./checkController";
import { createBindingDraft, getViewState, updateViewState } from "./viewState";
import { toastError } from "./toast";

let snapshot: { operation: InstallOperation | null; error: string | null; preparing: boolean; cancelling: boolean; dismissing: boolean } = { operation: null, error: null, preparing: false, cancelling: false, dismissing: false };
const listeners = new Set<() => void>();
let automatic: { id: string; type: InstallType } | null = null;
let dispatching = false;
let refreshing: Promise<void> | null = null;
let eventQueue = Promise.resolve();
let initializeEvents: ((operation: InstallOperation | null) => void) | null = null;
let generation = 0;
let revision = 0;
let preparing = false;
let uninstallIntent = false;
let dismissingId: string | null = null;
let cancellingId: string | null = null;
const blocked = new Set<string>();
const cancelUncertain = new Set<string>();
const nativeStarted = new Set<string>();
const publish = (operation: InstallOperation | null, error: string | null = null) => {
  if (operation && snapshot.operation?.id === operation.id) {
    if (!isActive(snapshot.operation) && isActive(operation)) operation = snapshot.operation;
    else {
      if (snapshot.operation.cancel_requested) operation = { ...operation, cancel_requested: true };
      if (snapshot.operation.notice_dismissed) operation = { ...operation, notice_dismissed: true };
      if (snapshot.operation.artifact_deleted) operation = { ...operation, artifact_deleted: true };
    }
  }
  ++revision;
  snapshot = { operation, error, preparing, cancelling: cancellingId === operation?.id, dismissing: dismissingId === operation?.id }; listeners.forEach((listener) => listener());
};
export function useInstallation() {
  return useSyncExternalStore((listener) => { listeners.add(listener); return () => { listeners.delete(listener); }; }, () => snapshot);
}
export function isActive(operation: InstallOperation | null) {
  return !!operation && !["failed", "closed", "cancelled"].includes(operation.stage);
}
export async function refreshOperation(): Promise<void> {
  if (refreshing) return refreshing;
  const currentGeneration = generation;
  const pending = (async () => {
    try {
      await eventQueue;
      const readRevision = revision;
      const op = await getOperation();
      if (currentGeneration !== generation || readRevision !== revision) return;
      if (op && !op.cancel_requested) cancelUncertain.delete(op.id);
      publish(op);
      const initialize = initializeEvents;
      initializeEvents = null;
      initialize?.(op);
      if (op?.stage === "ready" && !op.cancel_requested && !blocked.has(op.id) && automatic?.id === op.id) {
        if (op.package?.warnings?.length) void dispatchPrepared(automatic.type, op.id);
        else await dispatchPrepared(automatic.type, op.id);
      }
    } catch (error) { if (currentGeneration === generation) publish(snapshot.operation, errorText(error)); }
  })();
  refreshing = pending;
  try { await pending; } finally { if (refreshing === pending) refreshing = null; }
}
export async function dispatchPrepared(type?: InstallType, identifier = snapshot.operation?.id): Promise<void> {
  const op = snapshot.operation;
  if (!op || op.id !== identifier || op.stage !== "ready" || op.cancel_requested || blocked.has(op.id) || dispatching) return;
  const channel = probeLoaderChannel();
  if (!channel.ok) { publish(op, channel.reason); return; }
  dispatching = true;
  automatic = null;
  let submitted: InstallOperation | null = null;
  const currentGeneration = generation;
  let uninstallClaimed = false;
  let uninstallCheckPending = false;
  const stopped = () => currentGeneration !== generation || snapshot.operation?.id !== op.id || blocked.has(op.id) || !!snapshot.operation?.cancel_requested;
  const publishResult = (result: InstallOperation | null, error: string | null = null) => {
    if (currentGeneration === generation && snapshot.operation?.id === op.id && result?.id === op.id) publish(result, error);
  };
  try {
    if (op.package?.warnings?.length && (!await confirmInstallWarnings(op.package.warnings) || stopped() || snapshot.operation?.stage !== "ready")) return;
    if (op.mode === "reinstall") {
      const confirmed = await confirmReinstall(op);
      if (stopped()) return;
      if (!confirmed) {
        publishResult(await recordInstallEvent(op.id, "uninstall_cancelled", op.package!.name));
        return;
      }
      submitted = await claimUninstall(op.id);
      uninstallClaimed = true;
      if (stopped() || submitted.cancel_requested || submitted.stage !== "uninstalling") return;
      publishResult(submitted);
      submitted = await recordInstallEvent(op.id, "uninstall_started", submitted.package!.name);
      if (!submitted) return;
      if (stopped() || submitted.cancel_requested || submitted.stage !== "uninstalling" || !submitted.uninstall_started) {
        if (currentGeneration === generation) publishResult(await recordInstallEvent(op.id, "uninstall_not_started", op.package!.name));
        return;
      }
      publishResult(submitted);
      await uninstallForReinstall(submitted);
      if (currentGeneration !== generation || snapshot.operation?.id !== op.id) return;
      uninstallCheckPending = true;
      submitted = await completeUninstall(op.id);
      uninstallCheckPending = false;
    } else {
      submitted = await submitPrepared(op.id);
    }
    if (currentGeneration !== generation || snapshot.operation?.id !== op.id) return;
    if (stopped() || submitted.cancel_requested || submitted.stage !== "awaiting") {
      if (submitted.stage === "awaiting" && !submitted.request_id) {
        publishResult(await recordInstallEvent(op.id, "dispatch_not_started", submitted.package!.name));
      } else if (snapshot.operation?.id === op.id) publishResult(submitted);
      return;
    }
    publishResult(submitted);
    await installFromArtifact(submitted, op.mode === "reinstall" ? InstallType.INSTALL : type ?? (op.existing_name ? InstallType.OVERWRITE : InstallType.INSTALL));
  } catch (error) {
    if (currentGeneration !== generation || snapshot.operation?.id !== op.id) return;
    const message = errorText(error);
    if (uninstallCheckPending && blocked.has(op.id)) {
      try { publishResult(await completeUninstall(op.id)); }
      catch { publish(snapshot.operation, t("{0}。卸载结果仍需回查，不会继续安装。", [message])); }
      return;
    }
    if (submitted?.package) {
      try { publishResult(await recordInstallEvent(op.id, uninstallClaimed && submitted.stage === "uninstalling" ? "uninstall_error" : "dispatch_error", submitted.package.name, message), message); }
      catch { publishResult(submitted, t("{0}。状态回写失败，请重新核对，勿重复安装。", [message])); }
    } else { publish(snapshot.operation, message); }
  } finally { if (currentGeneration === generation) dispatching = false; }
}
export async function startInstallation(release: ReleaseRow, zip: string, plugin: PluginRow | null, pluginPath?: string, mode: "install" | "reinstall" = "install", onPreparing?: () => void): Promise<boolean> {
  if (preparing || dispatching || uninstallIntent || mode === "reinstall" && isActive(snapshot.operation)) return false;
  const channel = probeLoaderChannel();
  if (!channel.ok) throw new Error(channel.reason);
  const currentGeneration = generation;
  preparing = true;
  try {
    if (mode === "reinstall" && !await confirmPrepareReinstall(plugin!.name)) return false;
    if (currentGeneration !== generation || mode === "reinstall" && isActive(snapshot.operation)) return false;
    publish(snapshot.operation);
    onPreparing?.();
    const op = mode === "reinstall" ? await prepareInstall(release.repo, release.tag, zip, plugin?.name ?? null, null, mode)
      : pluginPath === undefined ? await prepareInstall(release.repo, release.tag, zip, plugin?.name ?? null)
      : await prepareInstall(release.repo, release.tag, zip, plugin?.name ?? null, pluginPath);
    if (currentGeneration !== generation) return false;
    // Events observed before this task was created cannot belong to it.
    const initialize = initializeEvents;
    initializeEvents = null;
    initialize?.(null);
    automatic = { id: op.id, type: versionAction(release, !!plugin).type };
    preparing = false;
    publish(op);
    return true;
  } catch (error) {
    if (currentGeneration === generation) { preparing = false; publish(null, errorText(error)); }
    throw error;
  } finally {
    if (currentGeneration === generation && preparing) { preparing = false; publish(snapshot.operation, snapshot.error); }
  }
}

export const isDispatchBlocked = (identifier: string) => blocked.has(identifier);

export function cancellationReason(op: InstallOperation): string | null {
  if (["failed", "closed", "cancelled"].includes(op.stage)) return t("任务已经结束");
  if (op.stage === "installing" || op.stage === "verified" || op.saw_start || nativeStarted.has(op.id)) return t("Decky 安装已开始，无法中断，仍将核验并保存结果");
  if (op.cancel_requested || cancellingId === op.id) return t("已请求停止，正在等待任务结果，不会重复发送");
  if (cancelUncertain.has(op.id)) return t("取消意图尚未确认，请先重新核对结果");
  if (op.stage === "unknown") return t("原生操作结果未知，请重新核对结果");
  if (op.stage === "awaiting" && !op.request_id) return t("正在关联 Decky 请求，尚不能安全取消");
  return null;
}

export async function cancelCurrentOperation(identifier = snapshot.operation?.id): Promise<void> {
  const op = snapshot.operation;
  if (!op || op.id !== identifier || cancellationReason(op)) return;
  const currentGeneration = generation;
  // Synchronous barrier precedes any RPC; no await continuation may dispatch now.
  blocked.add(op.id); automatic = null; cancellingId = op.id;
  publish(op);
  const current = () => currentGeneration === generation && snapshot.operation?.id === op.id;
  try {
    let result = await cancelOperation(op.id);
    if (!current() || result.id !== op.id) return;
    publish(result);
    if (result.stage === "awaiting" && result.request_id && !result.saw_start && !nativeStarted.has(op.id)) {
      try {
        await cancelPendingInstall(result.request_id);
        if (!current()) return;
        result = (await recordInstallEvent(op.id, "cancelled", result.package!.name))!;
      } catch (error) {
        result = (await recordInstallEvent(op.id, "cancel_unknown", result.package!.name))!;
        if (current() && result?.id === op.id) publish(result, t("取消结果无法确认：{0}。请重新核对，勿重复发送。", [errorText(error)]));
        return;
      }
    }
    if (current() && result?.id === op.id) publish(result);
  } catch (error) { if (current()) { cancelUncertain.add(op.id); publish(snapshot.operation, errorText(error)); } }
  finally {
    if (currentGeneration === generation && cancellingId === op.id) {
      cancellingId = null;
      if (current()) publish(snapshot.operation, snapshot.error);
    }
  }
}
export const cancelConfirmation = cancelCurrentOperation;

export async function dismissTaskNotice(identifier: string): Promise<void> {
  const op = snapshot.operation;
  if (!op || op.id !== identifier || isActive(op) || op.notice_dismissed || dismissingId) return;
  const currentGeneration = generation;
  const current = () => currentGeneration === generation && snapshot.operation?.id === identifier;
  dismissingId = identifier; publish(op);
  try {
    const result = await dismissOperation(identifier);
    if (current() && result.id === identifier) publish(result);
  } catch (error) { if (current()) publish(snapshot.operation, errorText(error)); }
  finally {
    if (currentGeneration === generation && dismissingId === identifier) {
      dismissingId = null;
      if (current()) publish(snapshot.operation, snapshot.error);
    }
  }
}
export async function removeInstallPackage(identifier: string): Promise<void> {
  const op = snapshot.operation;
  if (!op || op.id !== identifier || op.artifact_deleted || !["failed", "closed", "cancelled"].includes(op.stage)) return;
  const currentGeneration = generation;
  try {
    const result = await deleteInstallPackage(identifier);
    if (currentGeneration === generation && snapshot.operation?.id === identifier && result.id === identifier) publish(result);
  } catch (error) {
    if (currentGeneration === generation && snapshot.operation?.id === identifier) publish(snapshot.operation, errorText(error));
  }
}

export async function continueDownloaded(identifier: string): Promise<void> {
  const op = snapshot.operation;
  if (!op || op.id !== identifier || op.stage !== "failed" || op.download?.status !== "complete") return;
  const currentGeneration = generation;
  try {
    const result = await continuePrepared(identifier);
    if (currentGeneration === generation && result.id === identifier) publish(result);
  } catch (error) {
    if (currentGeneration === generation && snapshot.operation?.id === identifier) publish(snapshot.operation, errorText(error));
  }
}

export async function retryFailedDownload(identifier: string): Promise<void> {
  const op = snapshot.operation;
  if (!op || op.id !== identifier || op.stage !== "failed" || op.download?.status !== "failed") return;
  const currentGeneration = generation;
  try {
    const result = await retryDownload(identifier);
    if (currentGeneration === generation && result.id === identifier) publish(result);
  } catch (error) {
    if (currentGeneration === generation && snapshot.operation?.id === identifier) publish(snapshot.operation, errorText(error));
  }
}


export async function finishTracking(identifier = snapshot.operation?.id): Promise<void> {
  const op = snapshot.operation;
  if (!op || op.id !== identifier) return;
  const currentGeneration = generation;
  try {
    const result = await closeOperation(op.id);
    if (currentGeneration === generation && snapshot.operation?.id === op.id) publish(result);
  } catch (error) { if (currentGeneration === generation && snapshot.operation?.id === op.id) publish(snapshot.operation, errorText(error)); }
}
export async function requestUninstall(name: string): Promise<void> {
  if (uninstallIntent || preparing || dispatching) return;
  const currentGeneration = generation;
  uninstallIntent = true;
  try {
    const op = await getOperation();
    if (currentGeneration !== generation) return;
    if (isActive(op)) throw new Error(t("请先处理当前安装任务，再卸载插件"));
    await uninstallPlugin(name, () => currentGeneration === generation && !isActive(snapshot.operation) && !preparing && !dispatching);
  } finally { if (currentGeneration === generation) uninstallIntent = false; }
}
async function refreshAfterUninstall(name: string, currentGeneration: number): Promise<void> {
  const selected = getViewState().plugin;
  try {
    const listing = await refreshAfterPluginMutation();
    if (currentGeneration !== generation || !listing || selected?.name !== name || listing.plugins.some(row => row.name === name)) return;
    updateViewState(current => {
      if (current.plugin !== selected) return current;
      const targetPage = ["details", "maintenance", "versions", "binding"].includes(current.page)
        || (current.page === "releaseNotes" && current.releaseNotes?.origin === "details")
        || (current.page === "network" && current.networkOrigin === "details");
      return { ...current, page: targetPage ? "home" : current.page, plugin: null,
        detailOrigin: "home", focusReturn: null, bindingOrigin: "details",
        operationOrigin: current.operationOrigin === "details" ? "home" : current.operationOrigin,
        networkOrigin: "settings", releaseNotesFocus: false,
        releaseNotes: current.releaseNotes?.origin === "details" ? null : current.releaseNotes,
        detailSelection: { key: "", tag: null, zip: null }, bindingDraft: createBindingDraft(),
        checkItem: null, checkSelection: null };
    });
  } catch (error) { if (currentGeneration === generation) toastError(error); }
}

export function startInstallController(): () => void {
  const currentGeneration = ++generation;
  let disposed = false;
  type InstallEvent = Parameters<Parameters<typeof listenForInstallEvents>[0]>;
  let pendingEvents: InstallEvent[] | null = [];
  const recordEvent = (operation: InstallOperation | null, [kind, name, value, sha256]: InstallEvent) => {
    // Bind at arrival (or the first successful read), never when queued writes finish.
    const id = operation?.package?.name === name && isActive(operation) ? operation.id : null;
    if (!id) return;
    if (kind === "start" && operation?.request_id) nativeStarted.add(id);
    eventQueue = eventQueue.then(async () => {
      if (disposed || currentGeneration !== generation) return;
      let result: InstallOperation | null;
      try { result = await recordInstallEvent(id, kind, name, value, sha256); }
      catch { result = await recordInstallEvent(id, kind, name, value, sha256); }
      if (!disposed && currentGeneration === generation && snapshot.operation?.id === id && result?.id === id) publish(result);
    }).catch((error) => { if (!disposed && currentGeneration === generation && snapshot.operation?.id === id) publish(snapshot.operation, errorText(error)); });
  };
  initializeEvents = (operation) => {
    const events = pendingEvents!;
    pendingEvents = null;
    for (const event of events) recordEvent(operation, event);
  };
  const stopEvents = listenForInstallEvents((...event) => {
    if (event[0] === "uninstall_returned") {
      const op = snapshot.operation;
      if (!(isActive(op) && op?.mode === "reinstall" && op.package?.name === event[1])) {
        void refreshAfterUninstall(event[1], currentGeneration);
      }
      return;
    }
    if (pendingEvents) pendingEvents.push(event);
    else recordEvent(snapshot.operation, event);
  });
  void refreshOperation();
  const timer = setInterval(() => {
    if (isActive(snapshot.operation)) void refreshOperation();
  }, 1500);
  return () => { disposed = true; ++generation; initializeEvents = null; pendingEvents = null; automatic = null; refreshing = null; dispatching = false; preparing = false; uninstallIntent = false; cancellingId = null; dismissingId = null; blocked.clear(); cancelUncertain.clear(); nativeStarted.clear(); stopEvents(); clearInterval(timer); };
}
