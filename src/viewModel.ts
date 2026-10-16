// Semantic index:
// - participant: docs/upstream-conflict/sideload/plugin-management.md -- centralize release selection, truthful item states and scheduled report summaries.
import { pluginEligible } from "./loaderManagement";
import { InstallType } from "./loaderBridge";
import type { CheckReport, PluginRow, ReleaseRow, UpdateInfo } from "./types";

export function initialSelection(releases: ReleaseRow[]) {
  const first = releases[0];
  return { tag: first?.tag ?? null, zip: onlyAsset(first) };
}
export function onlyAsset(release: ReleaseRow | undefined): string | null {
  return release?.zips.length === 1 ? release.zips[0].url : null;
}
export function versionAction(release: ReleaseRow, installed: boolean) {
  if (!installed) return { label: `安装 ${release.tag}`, type: InstallType.INSTALL };
  switch (release.comparison) {
    case 0: return { label: `重新安装 ${release.tag}`, type: InstallType.REINSTALL };
    case 1: return { label: `更新到 ${release.tag}`, type: InstallType.UPDATE };
    case -1: return { label: `切换到 ${release.tag}`, type: InstallType.DOWNGRADE };
    default: return { label: `安装所选版本 ${release.tag}`, type: InstallType.OVERWRITE };
  }
}
export function checkFor(plugin: PluginRow, report: CheckReport | null): UpdateInfo | undefined {
  if (!pluginEligible(plugin)) return undefined;
  return report?.items.find((c) => c.name === plugin.name && c.repo === plugin.repo && c.current === plugin.version && (c.folder === undefined || c.folder === plugin.folder) &&
    (plugin.ownership?.state !== "owned" || c.ownership_id === plugin.ownership.intent?.id));
}
export function pluginSummary(plugin: PluginRow, report: CheckReport | null): string {
  const version = plugin.version ?? "未知版本";
  if (!plugin.repo) return `${version}，${plugin.repo_source === "disabled" ? "已停止更新追踪" : "需补充 GitHub"}`;
  const result = checkFor(plugin, report);
  if (!result) return `${version}，尚未检查更新`;
  const status = {
    update: `可更新到 ${result.latest}`, current: "已是最新", ahead: "本机版本高于最新发布",
    uncomparable: `远端 ${result.latest}，版本无法比较`, unbound: "需补充 GitHub",
    no_release: "仓库没有发布版本", no_asset: `最新 ${result.latest} 没有安装包`, error: "检查失败，进入详情重试",
  }[result.status];
  return `${version}，${status}`;
}

export const checkStatusLabels = {
  update: "可更新", current: "已是最新", ahead: "本机版本较新", uncomparable: "版本无法比较",
  unbound: "未关联", no_release: "无发布", no_asset: "无安装包", error: "检查失败",
};
export function checkReportSummary(report: CheckReport): string {
  if (report.error) return `整轮检查失败：${report.error}`;
  const count = (status: string) => report.items.filter((item) => item.status === status).length;
  const unresolved = count("no_asset") + count("no_release") + count("uncomparable");
  return `可更新 ${count("update")} 个，无需更新 ${count("current") + count("ahead")} 个，失败 ${count("error")} 个，未关联 ${count("unbound")} 个，无法确认 ${unresolved} 个`;
}

export function latestScheduledReport(persisted: CheckReport | null | undefined, live: CheckReport | undefined): CheckReport | null {
  return live && (!persisted || Date.parse(live.checkedAt) >= Date.parse(persisted.checkedAt)) ? live : persisted ?? null;
}
export function shortCheckTime(value: string, now = new Date()): string {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "时间未知";
  const pad = (n: number) => String(n).padStart(2, "0");
  const time = `${pad(date.getHours())}:${pad(date.getMinutes())}`;
  if (date.toDateString() === now.toDateString()) return `今天 ${time}`;
  return `${date.getFullYear() === now.getFullYear() ? "" : `${date.getFullYear()}-`}${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${time}`;
}
export function compactCheckSummary(report: CheckReport): string {
  if (report.error) return "检查失败，查看原因";
  const count = (statuses: string[]) => report.items.filter((item) => statuses.includes(item.status)).length;
  const parts = [["可更新", count(["update"])], ["失败", count(["error"])], ["无法确认", count(["no_asset", "no_release", "uncomparable"])]] as const;
  return parts.filter(([, n]) => n > 0).map(([label, n]) => `${label} ${n}`).join("，") ||
    (report.items.some((item) => item.status === "current" || item.status === "ahead") ? "没有可用更新" : report.items.length ? "尚无可检查的关联仓库" : "没有可检查的插件");
}
export const checkItemKey = (item: UpdateInfo) => JSON.stringify([item.name, item.repo, item.current]);
export function orderedCheckItems(report: CheckReport): UpdateInfo[] {
  const rank = { update: 0, error: 1, no_asset: 2, no_release: 2, uncomparable: 2, current: 3, ahead: 3, unbound: 4 };
  return [...report.items].sort((a, b) => rank[a.status] - rank[b.status]);
}
