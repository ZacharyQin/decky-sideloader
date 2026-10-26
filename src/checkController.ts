import { useSyncExternalStore } from "react";
import { checkUpdates, getPluginReleases, listSideloadPlugins } from "./api";
import { errorText } from "./errors";
import type { CheckReport, ScheduledCheckEvent, PluginListResponse, PluginRow, ReleaseSnapshot, ReleaseReadResult } from "./types";
import { pluginEligible } from "./loaderManagement";
import { checkFor } from "./viewModel";
import { t } from "./i18n";

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
  if (changes.list) syncReleaseReads(changes.list);
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
export async function refreshAfterPluginMutation(): Promise<PluginListResponse | null> {
  // Reads started before a mutation cannot satisfy its completion refresh.
  ++generation;
  refreshRequested = true;
  await refreshPlugins();
  if (state.listError) throw new Error(state.listError);
  return state.list;
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

type ReleaseState = ReleaseReadResult & { busy: boolean; loaded: boolean };
const emptyRelease: ReleaseState = { snapshot: null, error: null, busy: false, loaded: false };
const releaseStates = new Map<string, ReleaseState>();
const releaseReads = new Map<string, Promise<ReleaseReadResult>>();
let releaseGeneration = 0;
const identityKey = (name: string, folder: string | null, repo: string | null, current: string | null, owner: string | null) =>
  JSON.stringify([name, folder, repo, current, owner]);
export const pluginReleaseKey = (plugin: PluginRow) => identityKey(plugin.name, plugin.folder, plugin.repo, plugin.version, plugin.ownership?.intent?.id ?? null);
const snapshotKey = (snapshot: ReleaseSnapshot) => identityKey(snapshot.name, snapshot.folder, snapshot.repo, snapshot.current, snapshot.ownership_id);
function currentIdentity(plugin: PluginRow) {
  const current = state.list?.plugins.find(p => p.name === plugin.name && p.folder === plugin.folder);
  return pluginEligible(plugin) && (!state.list || !!current && pluginEligible(current) && pluginReleaseKey(current) === pluginReleaseKey(plugin));
}
const notifyReleases = () => { for (const listener of listeners) listener(); };
function syncReleaseReads(list: PluginListResponse) {
  const valid = new Set<string>();
  const snapshots = new Map((list.release_snapshots ?? []).map(snapshot => [snapshotKey(snapshot), snapshot]));
  for (const plugin of list.plugins) {
    if (!pluginEligible(plugin)) continue;
    const key = pluginReleaseKey(plugin);
    valid.add(key);
    const old = releaseStates.get(key);
    const snapshot = snapshots.get(key);
    const report = checkFor(plugin, list.last_check);
    const error = report?.status === "error" ? report.error : null;
    if (snapshot && (!old?.snapshot || snapshot.checkedAt >= old.snapshot.checkedAt)) {
      const advanced = !old?.snapshot || snapshot.checkedAt > old.snapshot.checkedAt;
      releaseStates.set(key, { snapshot, error: error ?? (advanced ? null : old?.error ?? null), busy: old?.busy ?? false, loaded: true });
    } else if (!snapshot && !releaseReads.has(key) && error) {
      releaseStates.set(key, { snapshot: old?.snapshot ?? null, error, busy: false, loaded: true });
    }
  }
  for (const key of releaseStates.keys()) if (!valid.has(key)) releaseStates.delete(key);
}
export function usePluginReleases(plugin: PluginRow): ReleaseState {
  const key = pluginReleaseKey(plugin);
  const value = useSyncExternalStore(subscribe, () => releaseStates.get(key) ?? emptyRelease, () => emptyRelease);
  return currentIdentity(plugin) ? value : { snapshot: null, error: t("身份已变化，需重新确认"), busy: false, loaded: true };
}
export function readPluginReleases(plugin: PluginRow, refresh = false): Promise<ReleaseReadResult> {
  const key = pluginReleaseKey(plugin);
  if (!currentIdentity(plugin)) return Promise.resolve({ snapshot: null, error: t("身份已变化，需重新确认") });
  const pendingRead = releaseReads.get(key);
  if (pendingRead) return pendingRead;
  const previous = releaseStates.get(key) ?? emptyRelease;
  if (!refresh && previous.loaded) return Promise.resolve(previous);
  const epoch = releaseGeneration;
  releaseStates.set(key, { ...previous, busy: true, error: null });
  const promise = Promise.resolve().then(async () => {
    let result: ReleaseReadResult;
    try { result = await getPluginReleases(plugin.repo!, plugin.version, plugin.name, plugin.folder, refresh); }
    catch (error) { result = { snapshot: previous.snapshot, error: errorText(error) }; }
    if (epoch !== releaseGeneration || !currentIdentity(plugin)) return { snapshot: null, error: t("身份已变化，需重新确认") };
    const current = releaseStates.get(key);
    if (result.snapshot && snapshotKey(result.snapshot) !== key) result = { snapshot: null, error: t("身份已变化，需重新确认") };
    const advanced = current?.snapshot && current.snapshot.checkedAt > (previous.snapshot?.checkedAt ?? "");
    if (advanced && (!result.snapshot || current.snapshot!.checkedAt > result.snapshot.checkedAt)) result = { snapshot: current.snapshot, error: current.error };
    releaseStates.set(key, { ...result, busy: false, loaded: true });
    return result;
  }).finally(() => {
    if (releaseReads.get(key) === promise) releaseReads.delete(key);
    if (epoch !== releaseGeneration) return;
    notifyReleases();
    // Preserve detail -> list publication, including late completion after navigation.
    ++generation;
    refreshRequested = true;
    void refreshPlugins();
  });
  releaseReads.set(key, promise);
  notifyReleases();
  return promise;
}
export function disposeReleaseReads() {
  ++releaseGeneration;
  releaseReads.clear();
  releaseStates.clear();
  notifyReleases();
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
