import { useSyncExternalStore } from "react";
import { cancelSelfUpdate, checkSelfUpdate, getSelfUpdateStatus, prepareSelfUpdate, recordSelfUpdateEvent, submitSelfUpdate } from "./api";
import { cancelPendingInstall, confirmInstallWarnings, installSelfUpdate, probeLoaderChannel } from "./loaderBridge";
import { errorText } from "./errors";
import type { SelfUpdateOperation, SelfUpdateStatus } from "./types";

let state: { status: SelfUpdateStatus | null; busy: boolean; error: string | null } = { status: null, busy: false, error: null };
const listeners = new Set<() => void>();
let generation = 0;
let readTicket = 0;
let events = Promise.resolve();
const publish = (changes: Partial<typeof state>) => { state = { ...state, ...changes }; for (const listener of listeners) listener(); };
const subscribe = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; };
export const useSelfUpdate = () => useSyncExternalStore(subscribe, () => state, () => state);
export const selfUpdateActive = (op: SelfUpdateOperation | null | undefined) => !!op && ["downloading", "ready", "awaiting", "installing", "verifying", "unknown"].includes(op.stage);

export async function refreshSelfUpdate() {
  const ticket = ++readTicket;
  const current = generation;
  try {
    const status = await getSelfUpdateStatus();
    if (current === generation && ticket === readTicket) publish({ status, error: null });
  } catch (error) {
    if (current === generation && ticket === readTicket) publish({ error: errorText(error) });
  }
}

export function receiveSelfUpdate(status: SelfUpdateStatus | null | undefined) {
  if (!status) return;
  ++readTicket;
  publish({ status });
  void refreshSelfUpdate();
}

async function act(action: (current: number) => Promise<void>) {
  if (state.busy) return;
  const current = generation;
  ++readTicket;
  publish({ busy: true, error: null });
  try {
    await action(current);
    if (current === generation) await refreshSelfUpdate();
  } catch (error) {
    if (current === generation) publish({ error: errorText(error) });
  } finally {
    if (current === generation) publish({ busy: false });
  }
}

export const checkForSelfUpdate = () => act(async () => { await checkSelfUpdate(); });
export const downloadSelfUpdate = () => act(async () => {
  const check = state.status?.check;
  if (check?.status !== "update" || !check.release) return;
  await prepareSelfUpdate(check.release.tag, check.release.sha256);
});

export const installPreparedSelfUpdate = () => act(async (current) => {
  const op = state.status?.operation;
  if (!op || op.stage !== "ready" || op.cancel_requested) return;
  const probe = probeLoaderChannel();
  if (!probe.ok) throw new Error(probe.reason);
  if (op.package?.warnings.length && !await confirmInstallWarnings(op.package.warnings)) return;
  if (current !== generation || state.status?.operation?.id !== op.id) return;
  const submitted = await submitSelfUpdate(op.id);
  if (current !== generation) return;
  publish({ status: submitted });
  if (!submitted.operation) return;
  try {
    await installSelfUpdate(submitted.operation);
  } catch (error) {
    await recordSelfUpdateEvent(op.id, "dispatch_error");
    throw error;
  }
});

export const cancelCurrentSelfUpdate = () => act(async () => {
  const op = state.status?.operation;
  if (!op) return;
  const result = await cancelSelfUpdate(op.id);
  const pending = result.operation;
  if (pending && ["awaiting", "unknown"].includes(pending.stage) && pending.request_id && !pending.saw_start) {
    try {
      await cancelPendingInstall(pending.request_id);
      await events;
      await recordSelfUpdateEvent(op.id, "cancelled", "", pending.request_id);
    } catch (error) {
      await recordSelfUpdateEvent(op.id, "cancel_unknown");
      throw error;
    }
  }
});

export function receiveSelfUpdateLoaderEvent(kind: string, name: string, value: string, evidence: string) {
  const op = state.status?.operation;
  if (name !== "Sideloader" || !op || !["awaiting", "installing", "unknown"].includes(op.stage)) return;
  const current = generation;
  events = events.then(async () => {
    if (current !== generation) return;
    await recordSelfUpdateEvent(op.id, kind, value, evidence);
    if (current === generation) await refreshSelfUpdate();
  }).catch(error => { if (current === generation) publish({ error: errorText(error) }); });
}

export function startSelfUpdateController() {
  ++generation;
  publish({ busy: false });
  void refreshSelfUpdate();
  const timer = setInterval(() => {
    if (!state.busy && (selfUpdateActive(state.status?.operation) || state.status?.checking || state.status?.preparing)) void refreshSelfUpdate();
  }, 1500);
  return () => { ++generation; ++readTicket; clearInterval(timer); };
}
