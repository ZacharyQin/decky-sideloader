// Semantic index:
// - owner: docs/upstream-conflict/sideload/plugin-management.md -- merge current Loader management without inferring store origin or replacing package identity.
import type { PluginListResponse, PluginRow } from "./types";

export type NativePlugin = { name: string; version: string | null; load_type: number; disabled: boolean };
export type LoaderSnapshot = { ok: true; plugins: NativePlugin[] } | { ok: false; error: string };
export function validateLoaderPlugins(value: unknown): NativePlugin[] {
  if (!Array.isArray(value)) throw new Error("Loader 管理清单结构无效");
  const names = new Set<string>();
  for (const p of value) {
    if (!p || typeof p.name !== "string" || !p.name || !(p.version === null || typeof p.version === "string") || typeof p.load_type !== "number" || !Number.isFinite(p.load_type) || typeof p.disabled !== "boolean" || names.has(p.name)) {
      throw new Error("Loader 管理清单损坏或名称重复");
    }
    names.add(p.name);
  }
  return value;
}
export const pluginKey = (plugin: PluginRow): string => plugin.folder ?? `loader/${JSON.stringify(plugin.name)}`;
export const pluginReadOnly = (plugin: PluginRow): boolean => plugin.folder === null || plugin.management?.state === "ambiguous";
export const pluginEligible = (plugin: PluginRow): boolean => !plugin.is_self && !pluginReadOnly(plugin) &&
  (plugin.ownership ? plugin.ownership.eligible : plugin.in_store !== true && plugin.sideload);
export const pluginCanTakeOver = (plugin: PluginRow): boolean => !plugin.is_self && !pluginReadOnly(plugin) &&
  (plugin.in_store === true || plugin.cached_in_store === true || plugin.memory_in_store === true || plugin.ownership?.state === "store");
export const pluginCanBind = (plugin: PluginRow): boolean => !plugin.is_self && !pluginReadOnly(plugin) &&
  (pluginEligible(plugin) && plugin.ownership?.state !== "owned" ||
    plugin.in_store === null && plugin.cached_in_store !== true && (!plugin.ownership || plugin.ownership.state === "unknown" && !plugin.ownership.intent && !plugin.ownership.error));
export function managementReason(plugin: PluginRow): string | null {
  const state = plugin.management?.state;
  if (state === "ambiguous") return "同名磁盘目录多个，无法确认 Loader 归属，仅供查看";
  if (plugin.folder === null) return `Loader ${state === "disabled" ? "已禁用" : "已登记"}，但未匹配可核验磁盘目录，仅供查看`;
  if (state === "unregistered") return "未被 Loader 当前登记";
  if (state === "unknown") return "Loader 管理状态未知";
  if (state === "disabled") return "Loader 已禁用";
  return null;
}
export function mergeLoaderPlugins(list: PluginListResponse, snapshot: LoaderSnapshot): PluginListResponse {
  const counts = new Map<string, number>();
  for (const p of list.plugins) counts.set(p.name, (counts.get(p.name) ?? 0) + 1);
  const native = new Map(snapshot.ok ? snapshot.plugins.map((p) => [p.name, p]) : []);
  const plugins: PluginRow[] = list.plugins.map((p) => {
    const entry = native.get(p.name);
    return { ...p, is_self: p.is_self || p.name === "GitHub Sideload", management: {
      state: counts.get(p.name)! > 1 ? "ambiguous" : !snapshot.ok ? "unknown" : !entry ? "unregistered" : entry.disabled ? "disabled" : "enabled",
      version: entry?.version ?? null, loadType: entry?.load_type ?? null,
      versionMismatch: counts.get(p.name) === 1 && !!entry && entry.version !== p.version,
    } };
  });
  for (const p of native.values()) if (!counts.has(p.name)) plugins.push({
    name: p.name, folder: null, version: null, author: "", repo: null, repo_source: null,
    is_self: p.name === "GitHub Sideload", in_store: null, sideload: false,
    management: { state: p.disabled ? "disabled" : "enabled", version: p.version, loadType: p.load_type, versionMismatch: false },
  });
  return { ...list, plugins, loader: snapshot.ok ? { ok: true, count: snapshot.plugins.length, error: null } : { ok: false, count: null, error: snapshot.error } };
}
