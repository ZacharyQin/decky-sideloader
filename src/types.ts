// Semantic index:
// - participant: docs/upstream-conflict/sideload/plugin-management.md -- keep management identity separate and mirror task cancellation and notice metadata compatibly.
// - participant: docs/upstream-conflict/sideload/diagnostics-sharing.md -- define the bounded pickup session DTO without persistent credentials.
// - participant: docs/upstream-conflict/sideload/diagnostics.md -- define strict bounded observation and export DTOs without exposing configuration.
export type OwnershipIntent = { id: string; name: string; folder: string; repo: string; prior_frozen: boolean; action: "takeover" | "return"; state: "prepared" | "writing" | "confirming" | "active" | "released" };
export type PluginOwnership = { state: "unknown" | "readonly" | "pending" | "store" | "owned" | "nonstore" | "repair"; eligible: boolean; intent: OwnershipIntent | null; error: string | null };
export type PluginRow = {
  ownership?: PluginOwnership;
  name: string; folder: string | null; version: string | null; author: string; package_name?: string | null;
  repo: string | null; repo_source: "user" | "package.json" | "remembered" | "disabled" | null;
  memory_in_store?: boolean | null; memory_changed?: boolean;
  registration?: "registered" | "unknown"; cached_in_store?: boolean | null;
  management?: { state: "enabled" | "disabled" | "unregistered" | "ambiguous" | "unknown"; version: string | null; loadType: number | null; versionMismatch: boolean };
  is_self: boolean; in_store: boolean | null; sideload: boolean;
};
export type CheckStatus = "update" | "current" | "ahead" | "uncomparable" | "unbound" | "no_release" | "no_asset" | "error";
export type UpdateInfo = {
  folder?: string; ownership_id?: string | null;
  name: string; repo: string | null; current: string | null; latest: string | null;
  status: CheckStatus; error: string | null;
};
export type CheckReport = { checkedAt: string; items: UpdateInfo[]; source?: "manual" | "scheduled"; error?: string };
export type PluginListResponse = {
  loader?: { ok: boolean; count: number | null; error: string | null };
  catalog?: { source: "live" | "cache" | "registry" | "unavailable"; timestamp: number | null; stale: boolean; warning: string | null };
  plugins: PluginRow[]; store_ok: boolean; store_error: string | null; last_check: CheckReport | null;
};
export type RepositoryRow = {
  repo: string; description: string; stars: number; fork: boolean; archived: boolean;
};
export type RepositoryPlugin = {
  path: string; name: string; package_name: string; version: string; evidence: string[];
};
export type RepositoryProbe = { repo: string; tree_sha: string; plugins: RepositoryPlugin[] };
export type ZipAsset = { name: string; url: string };
export type ReleaseRow = {
  tag: string; title: string; repo: string; prerelease: boolean; body: string; published_at: string;
  zips: ZipAsset[]; comparison: -1 | 0 | 1 | null; has_zip: boolean;
};
export type NetworkMode = "direct" | "auto" | "gh-proxy";
export type NetworkRoute = "direct" | "gh-proxy";
export type AppSettings = {
  networkMode?: NetworkMode; lastNetworkRoute?: { route: NetworkRoute; timestamp: number } | null;
  bindings: Record<string, { repo?: string; overridden?: boolean; disabled?: boolean; folder?: string; package_name?: string | null; package_repo?: string | null }>;
  checkNotifications?: boolean; lastScheduledCheck?: CheckReport | null;
  autoCheck: boolean; autoCheckMinutes: number; lastCheck: CheckReport | null;
};
export type OperationStage = "checking" | "backup" | "ready" | "uninstalling" | "validating" | "awaiting" | "installing" | "verified" | "failed" | "unknown" | "cancelled" | "closed";
export type InstallOperation = {
  id: string; stage: OperationStage; repo: string; tag: string; existing_name: string | null;
  created_at: string; error: string | null; artifact: string; directory: string; request_id: string | null;
  network_mode?: NetworkMode;
  download?: { received: number; total: number | null; status: "connecting" | "downloading" | "complete" | "failed"; route: NetworkRoute; attempt: number } | null;
  notice_dismissed?: boolean; cancel_requested?: boolean; uninstall_started?: boolean; uninstall_blocked?: boolean; uninstall_result_received?: boolean;
  saw_finish: boolean; binding_saved: boolean;
  mode?: "install" | "reinstall"; saw_start?: boolean; evidence_conflict?: boolean;
  outcome?: "success" | null; close_reason?: "verified" | "manual"; completed_at?: string;
  target?: RepositoryPlugin & { tree_sha: string } | null;
  package?: { name: string; folder: string; version: string; sha256: string; warnings?: string[] } | null;
  backup: { paths: string[]; files: number; bytes: number; archive: string; sha256: string; scope: string; created_at: string } | null;
};

export type ScheduledCheckEvent = { report: CheckReport; checkNotifications: boolean };

export type DiagnosticPackage = Extract<DiagnosticStatus, { state: "success" }>;
export type DiagnosticShare = {
  state: "idle" | "preparing" | "active" | "draining" | "stopped" | "expired" | "exhausted" | "error";
  session?: string;
  package?: DiagnosticPackage;
  url?: string;
  remaining?: number;
  downloads?: number;
  error?: string;
};

export type DiagnosticEvent = { event: "loader_read"; ok: boolean; count: number; durationMs: number };
export type DiagnosticStatus =
  | { state: "idle" | "exporting" }
  | { state: "error"; error: string }
  | { state: "success"; file: string; path: string; bytes: number };
