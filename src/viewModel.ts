import { t } from "./i18n";
import { pluginEligible } from "./loaderManagement";
import { InstallType } from "./loaderBridge";
import type { CheckReport, InstallOperation, OperationObservation, PluginRow, ReleaseRow, UpdateInfo } from "./types";

export function initialSelection(releases: ReleaseRow[]) {
  const first = releases[0];
  const stable = first?.prerelease ? releases.find((release) => release.has_zip && !release.prerelease) : undefined;
  const chosen = stable ?? first;
  return { tag: chosen?.tag ?? null, zip: onlyAsset(chosen) };
}
export function onlyAsset(release: ReleaseRow | undefined): string | null {
  return release?.zips.length === 1 ? release.zips[0].url : null;
}
export function versionAction(release: ReleaseRow, installed: boolean) {
  if (!installed) return { label: t("安装 {0}", [release.tag]), type: InstallType.INSTALL };
  switch (release.comparison) {
    case 0: return { label: t("重新安装 {0}", [release.tag]), type: InstallType.REINSTALL };
    case 1: return { label: t("更新到 {0}", [release.tag]), type: InstallType.UPDATE };
    case -1: return { label: t("降级到 {0}", [release.tag]), type: InstallType.DOWNGRADE };
    default: return { label: t("安装所选版本 {0}", [release.tag]), type: InstallType.OVERWRITE };
  }
}
export function checkFor(plugin: PluginRow, report: CheckReport | null): UpdateInfo | undefined {
  if (!pluginEligible(plugin)) return undefined;
  return report?.items.find((c) => c.name === plugin.name && c.repo === plugin.repo && c.current === plugin.version && (c.folder === undefined || c.folder === plugin.folder) &&
    (plugin.ownership?.state !== "owned" || c.ownership_id === plugin.ownership.intent?.id));
}
export function pluginSummary(plugin: PluginRow, report: CheckReport | null): string {
  const version = plugin.version ?? t("未知版本");
  if (!plugin.repo) return `${version}，${plugin.repo_source === "disabled" ? t("已停止更新追踪") : t("需补充 GitHub")}`;
  const result = checkFor(plugin, report);
  if (!result) return t("{0}，尚未检查更新", [version]);
  const status = {
    update: t("可更新到 {0}", [result.latest]), current: t("已是最新"), ahead: t("本机版本高于最新发布"),
    uncomparable: t("远端 {0}，版本无法比较", [result.latest]), unbound: t("需补充 GitHub"),
    no_release: t("仓库没有发布版本"), no_asset: t("最新 {0} 没有安装包", [result.latest]), error: t("检查失败，进入详情重试"),
  }[result.status];
  return `${version}，${status}${result.notice ? `，${result.notice}` : ""}`;
}

export const checkStatusLabels = {
  get update() { return t("可更新"); }, get current() { return t("已是最新"); }, get ahead() { return t("本机版本较新"); }, get uncomparable() { return t("版本无法比较"); },
  get unbound() { return t("未关联"); }, get no_release() { return t("无发布"); }, get no_asset() { return t("无安装包"); }, get error() { return t("检查失败"); },
};
export function checkReportSummary(report: CheckReport): string {
  if (report.error) return t("整轮检查失败：{0}", [report.error]);
  const count = (status: string) => report.items.filter((item) => item.status === status).length;
  const unresolved = count("no_asset") + count("no_release") + count("uncomparable");
  return t("可更新 {0} 个，无需更新 {1} 个，失败 {2} 个，未关联 {3} 个，无法确认 {4} 个", [count("update"), count("current") + count("ahead"), count("error"), count("unbound"), unresolved]);
}

export function latestScheduledReport(persisted: CheckReport | null | undefined, live: CheckReport | undefined): CheckReport | null {
  return live && (!persisted || Date.parse(live.checkedAt) >= Date.parse(persisted.checkedAt)) ? live : persisted ?? null;
}
export function shortCheckTime(value: string, now = new Date()): string {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return t("时间未知");
  const pad = (n: number) => String(n).padStart(2, "0");
  const time = `${pad(date.getHours())}:${pad(date.getMinutes())}`;
  if (date.toDateString() === now.toDateString()) return t("今天 {0}", [time]);
  return `${date.getFullYear() === now.getFullYear() ? "" : `${date.getFullYear()}-`}${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${time}`;
}
export function compactCheckSummary(report: CheckReport): string {
  if (report.error) return t("检查失败，查看原因");
  const count = (statuses: string[]) => report.items.filter((item) => statuses.includes(item.status)).length;
  const parts = [[t("可更新"), count(["update"])], [t("失败"), count(["error"])], [t("无法确认"), count(["no_asset", "no_release", "uncomparable"])]] as const;
  return parts.filter(([, n]) => n > 0).map(([label, n]) => `${label} ${n}`).join("，") ||
    (report.items.some((item) => item.status === "current" || item.status === "ahead") ? t("没有可用更新") : report.items.length ? t("尚无可检查的关联仓库") : t("没有可检查的插件"));
}
export const checkItemKey = (item: UpdateInfo) => JSON.stringify([item.name, item.repo, item.current]);
export function orderedCheckItems(report: CheckReport): UpdateInfo[] {
  const rank = { update: 0, error: 1, no_asset: 2, no_release: 2, uncomparable: 2, current: 3, ahead: 3, unbound: 4 };
  return [...report.items].sort((a, b) => rank[a.status] - rank[b.status]);
}

const installSteps = [
  ["download", "主安装包下载"], ["archive", "安装包校验"], ["dependencies", "额外依赖准备"], ["backup", "数据备份"],
  ["awaiting", "等待原生确认"], ["installing", "Loader 安装"], ["verification", "安装文件核验"], ["verified", "保存安装结果"],
] as const;
const reinstallSteps = [...installSteps.slice(0, 4), ["uninstalling", "原生卸载"] as const, ...installSteps.slice(4)];

export function operationProgress(op: InstallOperation) {
  const steps = op.mode === "reinstall" ? reinstallSteps : installSteps;
  let latest: OperationObservation | undefined;
  for (const row of op.observations ?? []) {
    if (!steps.some(([id]) => id === row.id)) continue;
    if (op.stage === "checking" && (row.status !== "running" || row.id !== "download" && row.id !== "archive" && row.id !== "dependencies")) continue;
    if (!latest || row.status === "running" && latest.status !== "running" ||
      (row.status === "running") === (latest.status === "running") && (Date.parse(row.updated_at) || 0) >= (Date.parse(latest.updated_at) || 0)) latest = row;
  }
  let currentId: string | undefined;
  switch (op.stage) {
    case "checking": currentId = latest?.id ?? (op.dependency_download ? "dependencies" : "download"); break;
    case "backup": currentId = "backup"; break;
    case "ready": case "validating": currentId = op.mode === "reinstall" && !op.uninstall_result_received ? "uninstalling" : "awaiting"; break;
    case "uninstalling": currentId = "uninstalling"; break;
    case "awaiting": currentId = "awaiting"; break;
    case "installing": currentId = latest?.id === "verification" ? "verification" : "installing"; break;
    case "verified": currentId = "verified"; break;
    default: currentId = latest?.id ?? (op.saw_start ? "installing" : op.request_id ? "awaiting" : op.uninstall_started ? "uninstalling" : op.backup ? "backup" : op.dependency_download ? "dependencies" : op.download ? "download" : undefined);
  }
  if (op.outcome === "success") currentId = "verified";
  return { steps, current: steps.findIndex(([id]) => id === currentId), observation: op.observations?.find(row => row.id === currentId) };
}
