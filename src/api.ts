// Semantic index:
// - participant: docs/upstream-conflict/sideload/plugin-management.md -- preserve management and ownership boundaries and expose exact-task cancellation and notice RPCs.
// - participant: docs/upstream-conflict/sideload/diagnostics-sharing.md -- strictly validate local addresses, credentials and correlated pickup responses.
// - participant: docs/upstream-conflict/sideload/diagnostics.md -- validate diagnostic RPC DTOs separately from existing business API results.
import { mergeLoaderPlugins } from "./loaderManagement";
import { readLoaderPlugins } from "./loaderBridge";
import { callable } from "@decky/api";
import type { AppSettings, InstallOperation, PluginListResponse, ReleaseRow, RepositoryRow, RepositoryProbe, NetworkMode, DiagnosticEvent, DiagnosticStatus, DiagnosticShare, OwnershipIntent } from "./types";

const readDiskPlugins = callable<[refresh?: boolean], PluginListResponse>("list_sideload_plugins");
const checkDiskUpdates = callable<[], PluginListResponse>("check_updates");
async function withLoader(read: () => Promise<PluginListResponse>): Promise<PluginListResponse> {
  const list = await read(); // Preserve backend RPC rejection; Loader failure is independent.
  return mergeLoaderPlugins(list, await readLoaderPlugins());
}
export const listSideloadPlugins = (refresh = false) => withLoader(() => refresh ? readDiskPlugins(true) : readDiskPlugins());
export const getAppSettings = callable<[], AppSettings>("get_app_settings");
export const setNetworkMode = callable<[mode: NetworkMode], AppSettings>("set_network_mode");
export const setCheckNotifications = callable<[enabled: boolean], AppSettings>("set_check_notifications");
export const setAutoCheck = callable<[enabled: boolean, minutes: number], AppSettings>("set_auto_check");
export const searchRepositories = callable<[query: string], RepositoryRow[]>("search_repositories");
export const probeRepository = callable<[repo: string], RepositoryProbe>("probe_repository");
export const setBinding = callable<[name: string, repo: string], { name: string; repo: string }>("set_binding");
export const clearBinding = callable<[name: string], void>("clear_binding");
export const listReleases = callable<[repo: string, current_version?: string | null], ReleaseRow[]>("list_releases");
export const checkUpdates = () => withLoader(checkDiskUpdates);
export const prepareInstall = callable<[repo: string, tag: string, asset_url: string, existing_name: string | null, plugin_path?: string | null, mode?: "install" | "reinstall"], InstallOperation>("prepare_install");
export const getOperation = callable<[], InstallOperation | null>("get_operation");
export const submitPrepared = callable<[identifier: string], InstallOperation>("submit_prepared");
export const recordInstallEvent = callable<[identifier: string, kind: string, name: string, value?: string, sha256?: string], InstallOperation | null>("record_install_event");
export const dismissOperation = callable<[identifier: string], InstallOperation>("dismiss_operation");
export const cancelOperation = callable<[identifier: string], InstallOperation>("cancel_operation");
export const closeOperation = callable<[identifier: string], InstallOperation>("close_operation");

export const claimUninstall = callable<[identifier: string], InstallOperation>("claim_uninstall");
export const completeUninstall = callable<[identifier: string], InstallOperation>("complete_uninstall");

const sendDiagnostics = callable<[events: DiagnosticEvent[], dropped: number], unknown>("record_diagnostic_events");
const exportLocalDiagnostics = callable<[], unknown>("export_diagnostics");
const readDiagnosticsStatus = callable<[], unknown>("get_diagnostics_status");
const diagnosticBytes = (value: string) => new TextEncoder().encode(value).byteLength;
function diagnosticStatus(value: unknown): DiagnosticStatus {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("诊断状态格式无效");
  const row = value as Record<string, unknown>;
  const keys = Object.keys(row);
  if ((row.state === "idle" || row.state === "exporting") && keys.length === 1) return { state: row.state };
  if (row.state === "error" && keys.length === 2 && typeof row.error === "string" && diagnosticBytes(row.error) <= 512) return { state: "error", error: row.error };
  if (row.state === "success" && keys.length === 4 && typeof row.file === "string" && /^diagnostics-[0-9]{19}-[a-f0-9]{32}\.zip$/.test(row.file) &&
    typeof row.path === "string" && diagnosticBytes(row.path) <= 4096 && row.path.startsWith("/") && !/[\x00-\x1f\x7f]/.test(row.path) &&
    row.path.endsWith("/Downloads/GitHub-Sideload-Diagnostics/" + row.file) && !row.path.split("/").some((part) => part === "." || part === "..") &&
    Number.isInteger(row.bytes) && (row.bytes as number) > 0 && (row.bytes as number) <= 6 * 1024 * 1024) {
    return { state: "success", file: row.file, path: row.path, bytes: row.bytes as number };
  }
  throw new Error("诊断状态格式无效");
}
const readShareAddresses = callable<[], unknown>("get_diagnostics_addresses");
const readShare = callable<[], unknown>("get_diagnostics_share");
const startShare = callable<[address: string, session: string, previous: string | null], unknown>("start_diagnostics_share");
const stopShare = callable<[session: string], unknown>("stop_diagnostics_share");
function privateAddress(value: unknown): value is string {
  if (typeof value !== "string" || !/^(?:\d{1,3}\.){3}\d{1,3}$/.test(value)) return false;
  const parts = value.split(".").map(Number);
  return parts.every((v, i) => v <= 255 && String(v) === value.split(".")[i]) &&
    (parts[0] === 10 || parts[0] === 172 && parts[1] >= 16 && parts[1] <= 31 || parts[0] === 192 && parts[1] === 168);
}
function shareStatus(value: unknown): DiagnosticShare {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("下载状态格式无效");
  const row = value as DiagnosticShare;
  if (!["idle", "preparing", "active", "draining", "stopped", "expired", "exhausted", "error"].includes(row.state)) throw new Error("下载状态格式无效");
  const keys = ["state"];
  if (row.state !== "idle") {
    if (typeof row.session !== "string" || !/^[a-f0-9]{32}$/.test(row.session)) throw new Error("下载请求无效");
    keys.push("session");
  }
  const result: DiagnosticShare = { state: row.state, ...(row.session ? { session: row.session } : {}) };
  if (row.package !== undefined) {
    const pack = diagnosticStatus(row.package);
    if (pack.state !== "success") throw new Error("下载文件格式无效");
    result.package = pack; keys.push("package");
  }
  if (row.state === "active") {
    const match = typeof row.url === "string" && /^http:\/\/([^/:]+):([0-9]{1,5})\/pickup\/([a-f0-9]{64})$/.exec(row.url);
    if (!match || !privateAddress(match[1]) || Number(match[2]) < 1 || Number(match[2]) > 65535 || String(Number(match[2])) !== match[2] || !result.package ||
      !Number.isInteger(row.remaining) || row.remaining! < 0 || row.remaining! > 300 || !Number.isInteger(row.downloads) || row.downloads! < 0 || row.downloads! > 3) throw new Error("下载链接格式无效");
    Object.assign(result, { url: row.url, remaining: row.remaining, downloads: row.downloads });
    keys.push("url", "remaining", "downloads");
  }
  if (row.state === "error") {
    if (typeof row.error !== "string" || diagnosticBytes(row.error) > 512) throw new Error("下载错误格式无效");
    result.error = row.error.replace(/领取/g, "下载"); keys.push("error");
  }
  if (Object.keys(row).sort().join(",") !== keys.sort().join(",")) throw new Error("下载状态格式无效");
  return result;
}
export async function getDiagnosticsAddresses(): Promise<{ addresses: string[] }> {
  const row = await readShareAddresses() as { addresses: unknown };
  if (!row || typeof row !== "object" || Object.keys(row).join(",") !== "addresses" || !Array.isArray(row.addresses) || row.addresses.length > 128 ||
    row.addresses.some((v) => !privateAddress(v)) || new Set(row.addresses).size !== row.addresses.length) throw new Error("局域网地址格式无效");
  return { addresses: row.addresses as string[] };
}
export const getDiagnosticsShare = async () => shareStatus(await readShare());
export async function startDiagnosticsShare(address: string, session: string, previous: string | null) {
  if (!privateAddress(address) || typeof session !== "string" || !/^[a-f0-9]{32}$/.test(session) || previous !== null && (typeof previous !== "string" || !/^[a-f0-9]{32}$/.test(previous))) throw new Error("下载请求无效");
  const result = shareStatus(await startShare(address, session, previous));
  if (result.session !== session || result.state === "active" && !result.url!.startsWith(`http://${address}:`)) throw new Error("下载响应不匹配");
  return result;
}
export async function stopDiagnosticsShare(session: string) {
  if (typeof session !== "string" || !/^[a-f0-9]{32}$/.test(session)) throw new Error("下载请求无效");
  const result = shareStatus(await stopShare(session));
  if (result.session !== session || ["active", "preparing", "draining"].includes(result.state)) throw new Error("下载尚未停止");
  return result;
}

export const getDiagnosticsStatus = async () => diagnosticStatus(await readDiagnosticsStatus());
export const exportDiagnostics = async () => diagnosticStatus(await exportLocalDiagnostics());
export async function recordDiagnosticEvents(events: DiagnosticEvent[], dropped: number): Promise<{ accepted: number }> {
  if (!Array.isArray(events) || events.length > 16 || !Number.isInteger(dropped) || dropped < 0 || dropped > 65535 || events.some((event) =>
    !event || Object.keys(event).sort().join(",") !== "count,durationMs,event,ok" || event.event !== "loader_read" || typeof event.ok !== "boolean" ||
    !Number.isInteger(event.count) || event.count < 0 || event.count > 2147483647 || !Number.isInteger(event.durationMs) || event.durationMs < 0 || event.durationMs > 2147483647) ||
    diagnosticBytes(JSON.stringify({ events, dropped })) > 8192) throw new Error("诊断批次无效");
  const result = await sendDiagnostics(events, dropped);
  if (!result || typeof result !== "object" || Array.isArray(result) || Object.keys(result).join(",") !== "accepted" ||
    !Number.isInteger((result as { accepted: unknown }).accepted) || (result as { accepted: number }).accepted !== events.length) throw new Error("诊断回执无效");
  return result as { accepted: number };
}

const beginOwnership = callable<[name: string, folder: string, repo: string, action: "takeover" | "return"], unknown>("begin_store_ownership");
const nativeOwnership = callable<[identifier: string, phase: "read" | "writing" | "confirming" | "complete"], unknown>("ownership_native_step");
export const authorizePluginMutation = callable<[name: string], void>("authorize_plugin_mutation");
function ownershipIntent(value: unknown): OwnershipIntent {
  const row = value as OwnershipIntent;
  const text = (v: unknown): v is string => typeof v === "string" && v.length > 0 && v.length <= 256 && !/[\x00-\x1f\x7f]/.test(v);
  if (!row || typeof row !== "object" || Object.keys(row).sort().join(",") !== "action,folder,id,name,prior_frozen,repo,state" ||
    !/^[a-f0-9]{32}$/.test(row.id) || !text(row.name) || !text(row.folder) || /[\/\\]/.test(row.folder) || [".", ".."].includes(row.folder) || !text(row.repo) ||
    typeof row.prior_frozen !== "boolean" || !["takeover", "return"].includes(row.action) || !["prepared", "writing", "confirming", "active", "released"].includes(row.state)) throw new Error("接管状态格式无效");
  return row;
}
export async function beginStoreOwnership(name: string, folder: string, repo: string, action: "takeover" | "return") {
  const intent = ownershipIntent(await beginOwnership(name, folder, repo, action));
  if (intent.name !== name || intent.folder !== folder || intent.action !== action) throw new Error("接管身份不匹配");
  return intent;
}
export async function ownershipNativeStep(identifier: string, phase: "read" | "writing" | "confirming" | "complete") {
  const raw = await nativeOwnership(identifier, phase) as { intent: unknown; frozen: unknown };
  if (!raw || Object.keys(raw).sort().join(",") !== "frozen,intent" || !Array.isArray(raw.frozen) || raw.frozen.length > 4096 ||
    raw.frozen.some((n) => typeof n !== "string" || !n || n.length > 256 || /[\x00-\x1f\x7f]/.test(n)) || new Set(raw.frozen).size !== raw.frozen.length) throw new Error("冻结状态格式无效");
  const intent = ownershipIntent(raw.intent);
  if (intent.id !== identifier) throw new Error("接管意图已变化");
  if (phase === "writing" && intent.state !== "writing" || phase === "confirming" && intent.state !== "confirming" ||
    phase === "complete" && intent.state !== (intent.action === "takeover" ? "active" : "released")) throw new Error("接管阶段不匹配");
  return { intent, frozen: raw.frozen as string[] };
}
