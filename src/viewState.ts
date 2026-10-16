// Semantic index:
// - participant: docs/upstream-conflict/sideload/plugin-management.md -- retain popup drafts, diagnostics inner page and one-shot return focus across QAM.
import { pluginKey } from "./loaderManagement";
import { useSyncExternalStore } from "react";
import type { AppSettings, NetworkMode, CheckReport, PluginRow, ReleaseRow, RepositoryRow, RepositoryProbe, UpdateInfo } from "./types";

export type Page = "home" | "details" | "install" | "settings" | "binding" | "operation" | "checks" | "network" | "diagnostics";
type Selection = { tag: string | null; zip: string | null };
export type BindingDraft = {
  key: string; value: string; query: string; mode: "search" | "manual";
  results: RepositoryRow[] | null; selected: RepositoryRow | null;
  request: number; searching: boolean; busy: boolean; error: string | null;
};
export const createBindingDraft = (plugin?: PluginRow): BindingDraft => ({
  key: plugin ? JSON.stringify([pluginKey(plugin), plugin.repo]) : "",
  value: plugin?.repo ?? "", query: plugin?.name ?? "", mode: "search",
  results: null, selected: null, request: 0, searching: false, busy: false, error: null,
});
export type InstallDraft = {
  draft: string; mode: "manual" | "search"; query: string; repositories: RepositoryRow[] | null;
  repository: string | null; searching: boolean; probe: RepositoryProbe | null; pluginPath: string | null;
  releases: ReleaseRow[] | null; selection: Selection; busy: boolean; error: string | null; request: number;
};
type ViewState = {
  page: Page;
  plugin: PluginRow | null;
  detailOrigin: "home" | "checks";
  focusReturn: "home" | "checks" | null;
  settingsFocus: boolean;
  networkFocus: boolean;
  diagnosticsFocus: boolean;
  network: { settings: AppSettings | null; selection: NetworkMode | null; busy: boolean; request: number; error: string | null };
  checkReport: CheckReport | null;
  checkItem: UpdateInfo | null;
  checkSelection: string | null;
  install: InstallDraft;
  detailSelection: Selection & { key: string };
  bindingDraft: BindingDraft;
};
const initial = (): ViewState => ({
  page: "home", plugin: null, focusReturn: null, detailOrigin: "home", settingsFocus: false, networkFocus: false, diagnosticsFocus: false,
  network: { settings: null, selection: null, busy: false, request: 0, error: null },
  checkReport: null, checkItem: null, checkSelection: null,
  install: { draft: "", mode: "manual", query: "", repositories: null, repository: null, searching: false,
    probe: null, pluginPath: null, releases: null, selection: { tag: null, zip: null }, busy: false, error: null, request: 0 },
  detailSelection: { key: "", tag: null, zip: null },
  bindingDraft: createBindingDraft(),
});
let state = initial();
const listeners = new Set<() => void>();
export const getViewState = () => state;
export function updateViewState(update: (current: ViewState) => ViewState) {
  state = update(state);
  for (const listener of listeners) listener();
}
const subscribe = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; };
export const useViewState = () => useSyncExternalStore(subscribe, getViewState, getViewState);
export function resetViewState() { updateViewState(initial); }
export function resetInstallView() { updateViewState((current) => ({ ...current, install: initial().install })); }
