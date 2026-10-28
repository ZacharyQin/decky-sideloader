import type { LoaderSnapshot } from "./loaderManagement";
import type { Page } from "./viewState";
export type OwnershipIntent = { id: string; name: string; folder: string; repo: string; prior_frozen: boolean; action: "takeover" | "return"; state: "prepared" | "writing" | "confirming" | "active" | "released" };
export type PluginOwnership = { state: "unknown" | "readonly" | "pending" | "store" | "owned" | "nonstore"; eligible: boolean; intent: OwnershipIntent | null; error: string | null };
export type PluginRow = {
  ownership?: PluginOwnership;
  name: string; folder: string | null; version: string | null; author: string; package_name?: string | null;
  repo: string | null; repo_source: "user" | "package.json" | "remembered" | "mapping" | "disabled" | null;
  memory_in_store?: boolean | null; memory_changed?: boolean;
  registration?: "registered" | "unknown"; cached_in_store?: boolean | null;
  management?: { state: "enabled" | "disabled" | "unregistered" | "ambiguous" | "unknown"; version: string | null; loadType: number | null; versionMismatch: boolean };
  is_self: boolean; in_store: boolean | null; sideload: boolean; skipped_tags?: string[];
};
export type CheckStatus = "update" | "current" | "ahead" | "uncomparable" | "unbound" | "no_release" | "no_asset" | "error";
export type UpdateInfo = {
  folder?: string; ownership_id?: string | null; checkedAt?: string;
  name: string; repo: string | null; current: string | null; latest: string | null;
  status: CheckStatus; error: string | null; notice?: string | null; release_tag?: string | null;
};
export type CheckReport = { checkedAt: string; fullCheckedAt?: string | null; items: UpdateInfo[]; source?: "manual" | "scheduled"; error?: string };
export type PluginListResponse = {
  native_snapshot: LoaderSnapshot;
  loader?: { ok: boolean; count: number | null; error: string | null };
  catalog?: { source: "live" | "cache" | "registry" | "unavailable"; timestamp: number | null; stale: boolean; warning: string | null };
  plugins: PluginRow[]; store_ok: boolean; store_error: string | null; last_check: CheckReport | null;
  release_snapshots?: ReleaseSnapshot[];
};
export type RepositoryRow = {
  repo: string; description: string; stars: number; fork: boolean; archived: boolean;
};
export type RepositoryPlugin = {
  path: string; name: string; package_name: string; version: string; evidence: string[]; requires_root: boolean;
};
export type RepositoryProbe = { repo: string; tree_sha: string; plugins: RepositoryPlugin[] };
export type RepositoryMapStatus = {
  revision: number; updated_at: string; count: number; source: "bundled" | "downloaded"; warning: string | null;
};
export type RepositoryMapUpdate = RepositoryMapStatus & { changed: boolean };
export type ZipAsset = { name: string; url: string };
export type ReleaseRow = {
  tag: string; title: string; repo: string; prerelease: boolean; body: string; published_at: string;
  zips: ZipAsset[]; comparison: -1 | 0 | 1 | null; has_zip: boolean;
  download_count?: number | null; // Absent in snapshots saved before download statistics were added.
};
export type ReleaseSnapshot = {
  name: string; folder: string | null; repo: string; current: string | null;
  ownership_id: string | null; checkedAt: string; releases: ReleaseRow[];
};
export type ReleaseReadResult = { snapshot: ReleaseSnapshot | null; error: string | null };
export type NetworkMode = "direct" | "auto" | "gh-proxy" | "ghfast";
export type NetworkRoute = "direct" | "gh-proxy" | "ghfast";
export type AppSettings = {
  networkMode?: NetworkMode; lastNetworkRoute?: { route: NetworkRoute; timestamp: number } | null;
  bindings: Record<string, { repo?: string; overridden?: boolean; disabled?: boolean; folder?: string; package_name?: string | null; package_repo?: string | null }>;
  checkNotifications?: boolean; showHomeTaskNotice?: boolean; deletePackageAfterInstall?: boolean; downloadTimeoutMinutes?: number; lastScheduledCheck?: CheckReport | null;
  autoCheck: boolean; autoCheckMinutes: number; lastCheck: CheckReport | null;
};
export type OperationStage = "checking" | "backup" | "ready" | "uninstalling" | "validating" | "awaiting" | "installing" | "verified" | "failed" | "unknown" | "cancelled" | "closed";
export type DownloadProgress = { received: number; total: number | null; status: "connecting" | "downloading" | "complete" | "failed"; route: NetworkRoute; attempt: number };
export type DependencyDownload = DownloadProgress & { name: string; index: number; count: number };
export type OperationObservation = { id: string; label: string; status: "running" | "complete" | "failed"; detail?: string; updated_at: string };
export type BackupCleanup = { deleted: number; bytes: number; skipped: number; failed: number };
export type InstallOperation = {
  id: string; stage: OperationStage; repo: string; tag: string; existing_name: string | null;
  created_at: string; error: string | null; artifact: string; directory: string; request_id: string | null;
  network_mode?: NetworkMode;
  download?: DownloadProgress | null;
  dependency_download?: DependencyDownload | null;
  observations?: OperationObservation[];
  notice_dismissed?: boolean; artifact_deleted?: boolean; cancel_requested?: boolean; uninstall_started?: boolean; uninstall_blocked?: boolean; uninstall_result_received?: boolean;
  backup_deleted?: boolean;
  saw_finish: boolean; binding_saved: boolean;
  mode?: "install" | "reinstall"; saw_start?: boolean; evidence_conflict?: boolean;
  outcome?: "success" | null; close_reason?: "verified" | "manual"; completed_at?: string;
  target?: RepositoryPlugin & { tree_sha: string } | null;
  package?: { name: string; folder: string; version: string; sha256: string; warnings?: string[] } | null;
  backup: { paths: string[]; files: number; bytes: number; archive: string; sha256: string; scope: string; created_at: string } | null;
};

export type ScheduledCheckEvent = { report: CheckReport; checkNotifications: boolean; self_update?: SelfUpdateStatus | null };

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

export type FrontendErrorType = "Error" | "TypeError" | "RangeError" | "ReferenceError" | "SyntaxError" | "URIError" | "EvalError" | "Exception";
export type DiagnosticEvent =
  | { event: "loader_read"; ok: boolean; count: number; durationMs: number; clientId?: string }
  | { event: "frontend_build"; version: string; fingerprint: string; clientId: string }
  | { event: "ui_page"; page: Page; clientId: string; pluginId?: string; releaseId?: string }
  | { event: "ui_action"; page: Page; action: string; clientId: string }
  | { event: "frontend_error"; page: Page; origin: "render" | "action" | "global" | "unhandled" | "toast" | "icon"; errorType: FrontendErrorType; clientId: string; line?: number; column?: number };
export type DiagnosticStatus =
  | { state: "idle" | "exporting" }
  | { state: "error"; error: string }
  | { state: "success"; file: string; path: string; bytes: number };

export type LicenseEntry = {
  id: string; name: string; version: string; group: string; host: boolean;
  license: string; source: string; attribution: string; note?: string;
  documents: { title: string; text: string }[];
};

export type FrontendIdentity = { version: string; fingerprint: string };
export type SelfUpdateRelease = ReleaseRow & { version: string; sha256: string; asset_id: number; size: number };
export type SelfUpdateOperation = {
  id: string; stage: "downloading" | "ready" | "awaiting" | "installing" | "verifying" | "unknown" | "completed" | "cancelled" | "failed";
  release: SelfUpdateRelease; artifact: string; request_id: string | null; saw_start: boolean; cancel_requested: boolean; error: string | null;
  package: { name: string; folder: string; version: string; sha256: string; warnings: string[] } | null;
  progress?: { received: number; total: number | null; status: string; route: string; attempt: number };
};
export type SelfUpdateStatus = {
  current: string; check: { status: "update" | "current" | "ahead" | "error"; release: SelfUpdateRelease | null; checked_at: string; error: string | null } | null;
  operation: SelfUpdateOperation | null; checking: boolean; preparing: boolean; frontend_compatible: boolean; error: string | null;
};
