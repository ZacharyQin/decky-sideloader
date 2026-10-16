// Semantic index:
// - owner: docs/upstream-conflict/sideload/diagnostics-sharing.md -- keep one pickup intent, guarded address draft and bounded status polling across QAM mounts without replay.
// - owner: docs/upstream-conflict/sideload/diagnostics.md -- bound frontend observations and retain one export intent across QAM mounts without replay.
import { useSyncExternalStore } from "react";
import type { DiagnosticEvent, DiagnosticStatus, DiagnosticShare } from "./types";

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
const listeners = new Set<() => void>();
const publish = (next: DiagnosticStatus) => { state = next; for (const listener of listeners) listener(); };
const drop = (count = 1) => { dropped = Math.min(65535, dropped + count); };

export function recordDiagnostic(event: DiagnosticEvent): void {
  try {
    if (!protocol) return;
    if (event.event !== "loader_read" || typeof event.ok !== "boolean" || !Number.isInteger(event.count) || event.count < 0 || event.count > 2147483647 ||
      !Number.isInteger(event.durationMs) || event.durationMs < 0 || event.durationMs > 2147483647) { drop(); return; }
    if (queue.length >= MAX_QUEUE) { drop(); return; }
    // Copy known primitives only; never retain the caller's object or error payload.
    queue.push({ event: "loader_read", ok: event.ok, count: event.count, durationMs: event.durationMs });
    schedule();
  } catch { /* Observability cannot change the read-only Loader result. */ }
}

function schedule() {
  if (!protocol || sending || timer || !queue.length) return;
  timer = setTimeout(() => { timer = undefined; void flush(); }, 250);
}
async function flush() {
  if (!protocol || sending) return;
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
      else if (result.state === "exporting") publish({ state: "error", error: "诊断导出仍未完成，请稍后重试读取状态" });
    } catch { if (id === generation && !intent && revision === exportRevision) publish({ state: "error", error: "诊断状态读取失败，可重试导出" }); }
  };
  void readStatus(60);
  return () => {
    if (id !== generation) return;
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
      const result = await current.export();
      if (id === generation) publish(result);
    } catch { if (id === generation) publish({ state: "error", error: "诊断导出失败，请重试" }); }
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
      else publishShare({ ...shareState, error: "下载状态读取超时，请停止或刷新状态" });
    } catch {
      if (id === generation && revision === shareRevision && !shareIntent) publishShare({ ...shareState, status: { state: "error", session: shareState.status.session }, error: "下载状态读取失败，请停止或刷新状态" });
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
    if (id === generation && revision === shareRevision && !shareIntent) publishShare({ ...shareState, loading: false, error: "下载状态读取失败，请刷新后重试" });
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
      const status = await current.start(address, session, previous);
      if (id === generation && revision === shareRevision) publishShare({ ...shareState, status });
    } catch {
      if (id === generation && revision === shareRevision) publishShare({ ...shareState, status: { state: "error", session }, error: "下载启动未确认，请刷新状态或停止后重试" });
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
      if (id === generation && revision === shareRevision) publishShare({ ...shareState, status: { state: "error", session }, error: "停止未确认，请刷新状态后重试" });
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
