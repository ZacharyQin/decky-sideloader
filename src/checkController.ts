// Semantic index:
// - participant: docs/upstream-conflict/sideload/plugin-management.md -- separate local reads and single explicit source refresh intents across QAM with generation and error isolation.
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
let sourceRefreshRequested = false;
let sourceRefreshInFlight = false;
let generation = 0;
const listeners = new Set<() => void>();
const publish = (changes: Partial<CheckState>) => {
  state = { ...state, ...changes };
  state.error = state.checkError ?? state.listError;
  for (const listener of listeners) listener();
};
const subscribe = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; };
export const useChecks = () => useSyncExternalStore(subscribe, () => state, () => state);
export function refreshPlugins(sources = false): Promise<void> {
  // Ordinary reads never refresh the store. Only a user's source-refresh
  // intent is queued, consumed once, and coalesced across QAM mounts.
  if (sources && !sourceRefreshInFlight) {
    sourceRefreshRequested = true;
    refreshRequested = true;
  }
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
        const sources = sourceRefreshRequested;
        sourceRefreshRequested = false;
        sourceRefreshInFlight = sources;
        const request = ++generation;
        try {
          const list = await (sources ? listSideloadPlugins(true) : listSideloadPlugins());
          if (request === generation) publish({ list, listError: null });
        } catch (error) {
          if (request === generation) publish({ listError: errorText(error) });
        } finally { sourceRefreshInFlight = false; }
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
