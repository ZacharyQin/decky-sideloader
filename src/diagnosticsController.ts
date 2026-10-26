import { t } from "./i18n";
import { useSyncExternalStore } from "react";
import type { DiagnosticEvent, DiagnosticStatus, DiagnosticShare } from "./types";
import type { Page } from "./viewState";
import { cleanDiagnosticEvent, frontendErrorType, frontendLocation } from "./diagnosticEvents";
import { frontendIdentity } from "./build";

type Protocol = {
  record: (events: DiagnosticEvent[], dropped: number) => Promise<{ accepted: number }>;
  export: () => Promise<DiagnosticStatus>;
  status: () => Promise<DiagnosticStatus>;
  sharing?: {
    addresses: () => Promise<{ addresses: string[] }>;
    status: () => Promise<DiagnosticShare>;
    start: (address: string, session: string, previous: string | null) => Promise<DiagnosticShare>;
    stop: (session: string) => Promise<DiagnosticShare>;
  };
};
const MAX_QUEUE = 32;
const MAX_BATCH = 16;
const MAX_BATCH_BYTES = 8192;
let protocol: Protocol | null = null;
let queue: DiagnosticEvent[] = [];
let dropped = 0;
let sending = false;
let timer: ReturnType<typeof setTimeout> | undefined;
let statusTimer: ReturnType<typeof setTimeout> | undefined;
let generation = 0;
let exportRevision = 0;
let intent: Promise<void> | null = null;
let state: DiagnosticStatus = { state: "idle" };
let clientId = "";
let page: Page = "home";
let viewRevision = 0;
let pendingFlush: Promise<void> | null = null;
let flushTimer: ReturnType<typeof setTimeout> | undefined;
let releaseDrain: (() => void) | undefined;
const listeners = new Set<() => void>();
const publish = (next: DiagnosticStatus) => { state = next; for (const listener of listeners) listener(); };
const drop = (count = 1) => { dropped = Math.min(65535, dropped + count); };

export function recordDiagnostic(event: DiagnosticEvent): void {
  try {
    if (!protocol) return;
    const clean = cleanDiagnosticEvent(event);
    if (!clean) { drop(); return; }
    if (queue.length >= MAX_QUEUE) { drop(); return; }
    queue.push(clean);
    schedule();
  } catch { /* Observability cannot change the read-only Loader result. */ }
}

export function recordUIAction(action: string): void {
  recordDiagnostic({ event: "ui_action", action, page, clientId });
}

export function recordFrontendError(error: unknown, origin: Extract<DiagnosticEvent, { event: "frontend_error" }>["origin"]): void {
  try { recordDiagnostic({ event: "frontend_error", page, origin, clientId, errorType: frontendErrorType(error), ...frontendLocation(error) }); }
  catch { /* Diagnostic collection must not replace the original error. */ }
}

export async function recordDiagnosticView(next: Page, plugin?: string | null, release?: string | null): Promise<void> {
  page = next;
  const revision = ++viewRevision, id = generation, client = clientId;
  const fingerprint = async (kind: string, value: string | null | undefined) => {
    if (!value || value.length > 4096) return undefined;
    try { return Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(`${kind}:${value}`)))).map(b => b.toString(16).padStart(2, "0")).join("").slice(0, 32); }
    catch { return undefined; }
  };
  const [pluginId, releaseId] = await Promise.all([fingerprint("plugin", plugin), fingerprint("release", release)]);
  if (id !== generation || revision !== viewRevision) return;
  recordDiagnostic({ event: "ui_page", page: next, clientId: client, ...(pluginId ? { pluginId } : {}), ...(releaseId ? { releaseId } : {}) });
}

function recordIdentity() {
  if (frontendIdentity) recordDiagnostic({ event: "frontend_build", ...frontendIdentity, clientId });
}

function flush(): Promise<void> {
  if (pendingFlush) return pendingFlush;
  const id = generation;
  const task = sendBatch().finally(() => { if (id === generation) pendingFlush = null; });
  pendingFlush = task;
  return task;
}

function flushBeforeExport(): Promise<void> | null {
  recordIdentity();
  if (!queue.length && !sending) return null;
  clearTimeout(timer); timer = undefined;
  const id = generation;
  const deadline = new Promise<void>(resolve => { releaseDrain = resolve; flushTimer = setTimeout(resolve, 1000); });
  const drain = async () => { for (let i = 0; i < 3 && id === generation && protocol && (queue.length || sending); i++) await flush(); };
  return Promise.race([drain(), deadline]).finally(() => {
    if (id !== generation) return;
    clearTimeout(flushTimer); flushTimer = undefined; releaseDrain = undefined;
    clearTimeout(timer); timer = undefined;
    schedule();
  });
}

function schedule() {
  if (!protocol || sending || timer || !queue.length) return;
  timer = setTimeout(() => { timer = undefined; void flush(); }, 250);
}
async function sendBatch() {
  if (!protocol || sending || !queue.length && !dropped) return;
  const id = generation;
  const current = protocol;
  const batch = queue.splice(0, MAX_BATCH);
  const lost = dropped; dropped = 0;
  sending = true;
  try {
    if (new TextEncoder().encode(JSON.stringify({ events: batch, dropped: lost })).byteLength > MAX_BATCH_BYTES) { drop(batch.length); return; }
    await current.record(batch, lost);
  } catch { if (id === generation) drop(batch.length + lost); }
  finally { if (id === generation) { sending = false; schedule(); } }
}

export function startDiagnostics(next: Protocol): () => void {
  const id = ++generation;
  protocol = next;
  clientId = crypto.randomUUID().replace(/-/g, "");
  page = "home";
  recordIdentity();
  const onError = (event: ErrorEvent) => { if (frontendLocation(event.error)) recordFrontendError(event.error, "global"); };
  const onRejected = (event: PromiseRejectionEvent) => { if (frontendLocation(event.reason)) recordFrontendError(event.reason, "unhandled"); };
  if (typeof window !== "undefined" && typeof window.addEventListener === "function") {
    window.addEventListener("error", onError);
    window.addEventListener("unhandledrejection", onRejected);
  }
  shareIntent = null;
  publishShare({ status: { state: "idle" }, addresses: [], loading: Boolean(next.sharing) });
  if (next.sharing) void refreshDiagnosticShare();
  const readStatus = async (remaining: number) => {
    const revision = exportRevision;
    try {
      const result = await next.status();
      if (id !== generation || intent || revision !== exportRevision) return;
      publish(result);
      if (result.state === "exporting" && remaining > 0) statusTimer = setTimeout(() => { statusTimer = undefined; void readStatus(remaining - 1); }, 1000);
      else if (result.state === "exporting") publish({ state: "error", error: t("诊断导出仍未完成，请稍后重试读取状态") });
    } catch { if (id === generation && !intent && revision === exportRevision) publish({ state: "error", error: t("诊断状态读取失败，可重试导出") }); }
  };
  void readStatus(60);
  return () => {
    if (id !== generation) return;
    if (typeof window !== "undefined" && typeof window.removeEventListener === "function") {
      window.removeEventListener("error", onError);
      window.removeEventListener("unhandledrejection", onRejected);
    }
    ++viewRevision;
    pendingFlush = null;
    releaseDrain?.(); releaseDrain = undefined;
    clearTimeout(flushTimer); flushTimer = undefined;
    const activeSession = shareState.status.session;
    if (next.sharing && activeSession) void next.sharing.stop(activeSession).catch(() => {});
    ++generation; protocol = null; queue = []; dropped = 0; sending = false;
    clearTimeout(shareTimer); shareTimer = undefined;
    ++shareRevision;
    shareIntent = null;
    publishShare({ status: { state: "idle" }, addresses: [], loading: false });
    clearTimeout(timer); timer = undefined;
    clearTimeout(statusTimer); statusTimer = undefined;
    // Pending RPC completion is ignored, never automatically sent again.
    intent = null;
  };
}

export function requestDiagnosticsExport(): Promise<void> {
  if (intent) return intent;
  if (!protocol || state.state === "exporting") return Promise.resolve();
  const id = generation;
  const current = protocol;
  ++exportRevision;
  clearTimeout(statusTimer); statusTimer = undefined;
  publish({ state: "exporting" });
  intent = (async () => {
    try {
      const drain = flushBeforeExport(); if (drain) await drain;
      if (id !== generation) return;
      const result = await current.export();
      if (id === generation) publish(result);
    } catch { if (id === generation) publish({ state: "error", error: t("诊断导出失败，请重试") }); }
    finally { if (id === generation) intent = null; }
  })();
  return intent;
}

export const useDiagnostics = () => useSyncExternalStore(
  (listener) => { listeners.add(listener); return () => { listeners.delete(listener); }; },
  () => state,
  () => state,
);

type ShareView = { status: DiagnosticShare; addresses: string[]; selectedAddress?: string; loading: boolean; error?: string };
let shareState: ShareView = { status: { state: "idle" }, addresses: [], loading: false };
let shareIntent: Promise<void> | null = null;
let shareRevision = 0;
let shareTimer: ReturnType<typeof setTimeout> | undefined;
const shareListeners = new Set<() => void>();
let addressRevision = 0;
const publishShare = (next: ShareView) => {
  if (next.status.session !== shareState.status.session || next.addresses.length !== shareState.addresses.length ||
      next.addresses.some((address) => !shareState.addresses.includes(address))) ++addressRevision;
  if (next.selectedAddress === undefined && next.addresses.length === 1) next = { ...next, selectedAddress: next.addresses[0] };
  if (next.selectedAddress && !next.addresses.includes(next.selectedAddress)) next = { ...next, selectedAddress: "" };
  shareState = next;
  for (const listener of shareListeners) listener();
};

// Native dropdowns may outlive the QAM component. Keep their draft in this
// lifecycle, but reject callbacks after a newer choice, address set or session.
export function diagnosticAddressSelector(): (address: string) => void {
  const id = generation, revision = addressRevision, session = shareState.status.session;
  return (address) => {
    if (!protocol?.sharing || id !== generation || revision !== addressRevision || session !== shareState.status.session ||
        shareState.loading || shareState.error || !shareState.addresses.includes(address) ||
        ["preparing", "active", "draining"].includes(shareState.status.state)) return;
    ++addressRevision;
    publishShare({ ...shareState, selectedAddress: address });
  };
}

function pollShare(id: number, revision: number, remaining = 360) {
  clearTimeout(shareTimer); shareTimer = undefined;
  if (!protocol?.sharing || !["active", "preparing", "draining"].includes(shareState.status.state)) return;
  const current = protocol.sharing;
  shareTimer = setTimeout(async () => {
    shareTimer = undefined;
    try {
      const status = await current.status();
      if (id !== generation || revision !== shareRevision || shareIntent) return;
      publishShare({ ...shareState, status, error: undefined });
      if (remaining > 0) pollShare(id, revision, remaining - 1);
      else publishShare({ ...shareState, error: t("下载状态读取超时，请停止或刷新状态") });
    } catch {
      if (id === generation && revision === shareRevision && !shareIntent) publishShare({ ...shareState, status: { state: "error", session: shareState.status.session }, error: t("下载状态读取失败，请停止或刷新状态") });
    }
  }, 1000);
}

export async function refreshDiagnosticShare(): Promise<void> {
  if (!protocol?.sharing || shareIntent) return;
  const id = generation, revision = ++shareRevision, current = protocol.sharing;
  clearTimeout(shareTimer); shareTimer = undefined;
  publishShare({ ...shareState, loading: true, error: undefined });
  try {
    const [status, { addresses }] = await Promise.all([current.status(), current.addresses()]);
    if (id !== generation || revision !== shareRevision || shareIntent) return;
    publishShare({ ...shareState, status, addresses, loading: false });
    pollShare(id, revision);
  } catch {
    if (id === generation && revision === shareRevision && !shareIntent) publishShare({ ...shareState, loading: false, error: t("下载状态读取失败，请刷新后重试") });
  }
}

export function requestDiagnosticShare(address: string): Promise<void> {
  if (shareIntent) return shareIntent;
  if (!protocol?.sharing || shareState.error || !shareState.addresses.includes(address) || ["preparing", "active", "draining"].includes(shareState.status.state)) return Promise.resolve();
  const id = generation, revision = ++shareRevision, current = protocol.sharing;
  clearTimeout(shareTimer); shareTimer = undefined;
  // Memory-only correlation ID, distinct from the backend cryptographic bearer token.
  const previous = shareState.status.session ?? null;
  const session = crypto.randomUUID().replace(/-/g, "");
  publishShare({ ...shareState, loading: false, status: { state: "preparing", session }, error: undefined });
  shareIntent = (async () => {
    try {
      const drain = flushBeforeExport(); if (drain) await drain;
      if (id !== generation || revision !== shareRevision) return;
      const status = await current.start(address, session, previous);
      if (id === generation && revision === shareRevision) publishShare({ ...shareState, status });
    } catch {
      if (id === generation && revision === shareRevision) publishShare({ ...shareState, status: { state: "error", session }, error: t("下载启动未确认，请刷新状态或停止后重试") });
    } finally {
      if (id === generation && revision === shareRevision) { shareIntent = null; pollShare(id, revision); }
    }
  })();
  return shareIntent;
}

export function stopDiagnosticShare(): Promise<void> {
  if (shareState.status.state === "draining" && shareIntent) return shareIntent;
  const session = shareState.status.session;
  if (!protocol?.sharing || !session) return Promise.resolve();
  const id = generation, revision = ++shareRevision, current = protocol.sharing;
  clearTimeout(shareTimer); shareTimer = undefined;
  publishShare({ ...shareState, status: { ...shareState.status, state: "draining", url: undefined }, error: undefined });
  shareIntent = (async () => {
    try {
      const status = await current.stop(session);
      if (id === generation && revision === shareRevision) publishShare({ ...shareState, status });
    } catch {
      if (id === generation && revision === shareRevision) publishShare({ ...shareState, status: { state: "error", session }, error: t("停止未确认，请刷新状态后重试") });
    } finally {
      if (id === generation && revision === shareRevision) shareIntent = null;
    }
  })();
  return shareIntent;
}

export const useDiagnosticShare = () => useSyncExternalStore(
  (listener) => { shareListeners.add(listener); return () => { shareListeners.delete(listener); }; },
  () => shareState,
  () => shareState,
);
