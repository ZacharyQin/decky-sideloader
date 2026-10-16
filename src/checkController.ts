// Semantic index:
// - participant: docs/upstream-conflict/sideload/plugin-management.md -- share checks and list refresh state across QAM mounts while retaining generation isolation and separate errors.
import { useSyncExternalStore } from "react";
import { checkUpdates, listSideloadPlugins } from "./api";
import { errorText } from "./components";
import type { CheckReport, ScheduledCheckEvent, PluginListResponse } from "./types";

type CheckState = {
  list: PluginListResponse | null; checking: boolean; refreshing: boolean; error: string | null; revision: number;
  listError: string | null; checkError: string | null; scheduledReport?: CheckReport;
};
let state: CheckState = { list: null, checking: false, refreshing: false, error: null, revision: 0, listError: null, checkError: null };
let pending: Promise<void> | null = null;
let refreshPending: Promise<void> | null = null;
let refreshRequested = false;
let generation = 0;
const listeners = new Set<() => void>();
const publish = (changes: Partial<CheckState>) => {
  state = { ...state, ...changes };
  state.error = state.checkError ?? state.listError;
  for (const listener of listeners) listener();
};
const subscribe = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; };
export const useChecks = () => useSyncExternalStore(subscribe, () => state, () => state);
export function refreshPlugins(): Promise<void> {
  // Remounts share the queued/in-flight read. A new check invalidates a read
  // already in flight, so requests during that check require one later read.
  if (refreshPending) {
    if (pending) refreshRequested = true;
    return refreshPending;
  }
  refreshRequested = true;
  refreshPending = Promise.resolve().then(async () => {
    try {
      while (refreshRequested) {
        while (pending) await pending;
        refreshRequested = false;
        const request = ++generation;
        try {
          const list = await listSideloadPlugins();
          if (request === generation) publish({ list, listError: null });
        } catch (error) {
          if (request === generation) publish({ listError: errorText(error) });
        }
      }
    } finally { refreshPending = null; publish({ refreshing: false }); }
  });
  publish({ refreshing: true });
  return refreshPending;
}
export function runCheck(): Promise<void> {
  if (pending) return pending;
  ++generation;
  pending = Promise.resolve().then(async () => {
    try { publish({ list: await checkUpdates(), checkError: null, revision: state.revision + 1 }); }
    catch (error) { publish({ checkError: errorText(error) }); }
    finally { pending = null; publish({ checking: false }); }
  });
  publish({ checking: true });
  return pending;
}

// A pending disable suppresses delivery immediately; failed saves restore backend policy.
let notificationsSavingOff = false;
let confirmedNotifications: boolean | null = null;
export function confirmScheduledNotifications(value: boolean) { confirmedNotifications = value; }
export function suppressScheduledNotifications(value: boolean) { notificationsSavingOff = value; }
export function receiveScheduledCheck(event: ScheduledCheckEvent): boolean {
  ++generation;
  refreshRequested = true;
  publish({ scheduledReport: event.report, ...(state.list && (!state.list.last_check || event.report.checkedAt >= state.list.last_check.checkedAt) ? { list: { ...state.list, last_check: event.report } } : {}) });
  void refreshPlugins();
  return event.checkNotifications && confirmedNotifications !== false && !notificationsSavingOff;
}
