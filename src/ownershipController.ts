import { t } from "./i18n";
import { useSyncExternalStore } from "react";
import { beginStoreOwnership, ownershipNativeStep } from "./api";
import { invalidateFrozenPlugins, updateFrozenPlugins } from "./loaderBridge";
import { errorText } from "./errors";
import type { OwnershipIntent, PluginRow } from "./types";

let state: { busy: boolean; error: string | null; identity: string | null } = { busy: false, error: null, identity: null };
let generation = 0;
let interrupt: (() => void) | undefined;
export function stopOwnershipController() { generation++; interrupt?.(); }
const listeners = new Set<() => void>();
const publish = (patch: Partial<typeof state>) => { state = { ...state, ...patch }; for (const fn of listeners) fn(); };
const identity = (plugin: PluginRow) => JSON.stringify([plugin.name, plugin.folder]);
export function useOwnership(plugin?: PluginRow) {
  const snapshot = useSyncExternalStore((fn) => { listeners.add(fn); return () => { listeners.delete(fn); }; }, () => state);
  return plugin && snapshot.identity !== identity(plugin) ? { ...snapshot, error: null } : snapshot;
}

const current = (epoch: number) => { if (epoch !== generation) throw new Error(t("接管被插件重载中断，结果未确认，请显式回查")); };
async function writeNative(intent: OwnershipIntent, epoch: number) {
  await invalidateFrozenPlugins();
  current(epoch);
  const { frozen, intent: claimed } = await ownershipNativeStep(intent.id, "writing");
  if (["name", "folder", "repo", "action", "prior_frozen"].some((key) => claimed[key as keyof OwnershipIntent] !== intent[key as keyof OwnershipIntent])) throw new Error(t("接管意图已变化"));
  current(epoch);
  const desired = intent.action === "takeover" || intent.prior_frozen;
  const names = frozen.filter((name) => name !== intent.name);
  if (desired) names.push(intent.name);
  await updateFrozenPlugins(names);
  current(epoch);
  await invalidateFrozenPlugins();
}

export async function changeOwnership(plugin: PluginRow, action: "takeover" | "return" | "read" | "continue", repo = plugin.repo ?? ""): Promise<boolean> {
  if (state.busy || !plugin.folder) return false;
  publish({ busy: true, error: null, identity: identity(plugin) });
  const epoch = generation;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let native: Promise<void> | undefined;
  let timedOut = false;
  const cancelled = new Promise<never>((_, reject) => { interrupt = () => {
    timedOut = true; reject(new Error(t("接管被插件重载中断，结果未确认，请显式回查")));
  }; });
  void cancelled.catch(() => {});
  try {
    const intent = action === "takeover" || action === "return"
      ? await beginStoreOwnership(plugin.name, plugin.folder, repo, action)
      : plugin.ownership?.intent ? (await ownershipNativeStep(plugin.ownership.intent.id, "read")).intent : null;
    current(epoch);
    if (!intent) throw new Error(t("没有可恢复的接管意图"));
    if (intent.name !== plugin.name || intent.folder !== plugin.folder) throw new Error(t("接管身份不匹配"));
    if (action !== "read") {
      native = writeNative(intent, epoch);
      await Promise.race([native, cancelled, new Promise<never>((_, reject) => { timer = setTimeout(() => {
        timedOut = true; reject(new Error(t("原生冻结结果超时，仍未确认。不会自动重试；原调用结束后可显式回查。")));
      }, 10000); })]);
      current(epoch);
      await ownershipNativeStep(intent.id, "confirming");
    } else {
      native = invalidateFrozenPlugins();
      await Promise.race([native, cancelled, new Promise<never>((_, reject) => { timer = setTimeout(() => {
        timedOut = true; reject(new Error(t("原生冻结回查超时，结果仍未确认")));
      }, 10000); })]);
    }
    current(epoch);
    await ownershipNativeStep(intent.id, "complete");
    current(epoch);
    return true;
  } catch (error) {
    publish({ error: errorText(error) });
    return false;
  } finally {
    clearTimeout(timer);
    interrupt = undefined;
    // A timeout cannot cancel a native array write. Keep the local exclusion
    // until it actually settles, and never activate from its late callback.
    if (timedOut && native) void native.then(() => publish({ busy: false }), () => publish({ busy: false }));
    else publish({ busy: false });
  }
}
