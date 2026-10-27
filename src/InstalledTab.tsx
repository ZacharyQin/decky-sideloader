import { t } from "./i18n";
import { useEffect, useState } from "react";
import { Focusable } from "@decky/ui";
import { managementReason, pluginReadOnly, pluginEligible, pluginCanBind, pluginCanTakeOver } from "./loaderManagement";
import { setSkippedRelease } from "./api";
import { Action, Explanation, Heading, PanelSection, Status } from "./components";
import { errorText } from "./errors";
import { PluginIcon } from "./pluginIcons";
import { ReleasePicker } from "./ReleasePicker";
import { CheckRecord } from "./CheckResults";
import { probeLoaderChannel } from "./loaderBridge";
import { isActive, requestUninstall, startInstallation, useInstallation } from "./installController";
import { checkFor, initialSelection, versionAction } from "./viewModel";
import { getViewState, updateViewState, useViewState } from "./viewState";
import { changeOwnership, useOwnership } from "./ownershipController";
import { readPluginReleases, refreshPlugins, useChecks, usePluginReleases, pluginReleaseKey } from "./checkController";
import type { PluginRow, ReleaseRow, UpdateInfo } from "./types";

const noReleases: ReleaseRow[] = [];

export function InstalledTab({ plugin: selectedPlugin, onBinding, onOperation, onOwnershipChanged, checkRecord, section = "overview", onVersions, onVersionSelected, onMaintenance, onNetwork }: { plugin: PluginRow; onBinding: () => void; onOperation: () => void; onOwnershipChanged?: () => void; checkRecord?: { item: UpdateInfo; checkedAt: string }; section?: "overview" | "versions" | "maintenance"; onVersions?: () => void; onVersionSelected?: () => void; onMaintenance?: () => void; onNetwork?: () => void }) {
  const { list } = useChecks();
  const currentPlugin = list?.plugins.find((p) => p.name === selectedPlugin.name && p.folder === selectedPlugin.folder);
  // A cached list must not grant permissions over a newer selected pending or
  // returned snapshot. Explicit ownership completion reloads the selection.
  const owner = selectedPlugin.ownership && !selectedPlugin.ownership.eligible ? selectedPlugin.ownership : currentPlugin?.ownership ?? selectedPlugin.ownership;
  const plugin = { ...selectedPlugin, ownership: owner, skipped_tags: currentPlugin?.skipped_tags ?? selectedPlugin.skipped_tags,
    in_store: owner === currentPlugin?.ownership ? currentPlugin?.in_store ?? selectedPlugin.in_store : selectedPlugin.in_store,
    ...(owner?.state === "owned" && owner.intent ? { repo: owner.intent.repo, repo_source: "user" as const } : {}) };
  const ownership = useOwnership(plugin);
  const { operation, preparing } = useInstallation();
  const readOnly = pluginReadOnly(plugin);
  const eligible = pluginEligible(plugin);
  const blocked = preparing || isActive(operation);
  const releaseState = usePluginReleases(plugin);
  const releases = releaseState.snapshot?.releases ?? noReleases;
  const key = pluginReleaseKey(plugin);
  const savedSelection = useViewState().detailSelection;
  const selection = savedSelection.key === key ? savedSelection : { tag: null, zip: null };
  const setSelection = (next: { tag: string | null; zip: string | null }) => updateViewState((current) => ({ ...current, detailSelection: { key, ...next } }));
  const [actionBusy, setBusy] = useState(false);
  const busy = actionBusy || releaseState.busy;
  const [error, setError] = useState<string | null>(null);
  const channel = probeLoaderChannel();
  useEffect(() => {
    if (eligible && plugin.repo && !releaseState.loaded) void readPluginReleases(plugin);
  }, [key, eligible, releaseState.loaded]);
  useEffect(() => {
    if (!releaseState.snapshot) return;
    const saved = getViewState().detailSelection;
    const selected = releases.find(row => row.tag === saved.tag);
    const next = saved.key === key ? {
      tag: selected?.tag ?? null,
      zip: selected?.zips.some(asset => asset.url === saved.zip) ? saved.zip : null,
    } : initialSelection(releases);
    if (saved.key !== key || saved.tag !== next.tag || saved.zip !== next.zip) setSelection(next);
  }, [key, releaseState.snapshot]);
  const release = releases.find((r) => r.tag === selection.tag);
  const action = release ? versionAction(release, true) : null;
  const install = async (mode: "install" | "reinstall" = "install") => {
    if (!eligible || !release || !selection.zip || !action) return;
    setBusy(true); setError(null);
    try { await startInstallation(release, selection.zip, plugin, undefined, mode, onOperation); }
    catch (err) { setError(errorText(err)); }
    finally { setBusy(false); }
  };
  const toggleSkip = async () => {
    if (!release) return;
    setBusy(true); setError(null);
    try { await setSkippedRelease(plugin.name, release.tag, !(plugin.skipped_tags ?? []).includes(release.tag)); await refreshPlugins(); }
    catch (err) { setError(errorText(err)); }
    finally { setBusy(false); }
  };
  const primaryAction = section === "overview" && release?.comparison !== 0 ? action : null;
  const refreshedAt = releaseState.snapshot && <Explanation>{t("版本信息更新于 {0}", [new Date(releaseState.snapshot.checkedAt).toLocaleString()])}</Explanation>;
  const refreshAction = <Action iconOnly={!!refreshedAt} kind="refreshReleases" loading={releaseState.busy} disabled={busy || blocked} onClick={() => void readPluginReleases(plugin, true)}>{t("重新读取版本")}</Action>;
  const skippedNotice = release && (plugin.skipped_tags ?? []).includes(release.tag) && <div className="gss-skipped-notice"><Status tone="warning" title={t("已跳过此版本")}>{t("检查更新会忽略它，直到取消跳过。")}</Status></div>;
  const releaseStatus = <>
    {releaseState.busy && <Status tone="loading" title={t(releaseState.snapshot ? "正在刷新版本…" : "正在读取版本…")} />}
    {primaryAction ? refreshedAt : refreshedAt ? <Focusable className="gss-release-actions gss-release-updated" flow-children="row">{refreshedAt}{refreshAction}</Focusable> : refreshAction}
    {releaseState.error && <Status tone={releaseState.snapshot ? "warning" : "error"} title={t(releaseState.snapshot ? "刷新失败，保留上次结果" : "读取失败")}>{releaseState.error}</Status>}
    {primaryAction && <Focusable className="gss-release-actions" flow-children="row">
      <div className="gss-release-primary">
        <Action kind="replace" disabled={busy || blocked || ownership.busy || !selection.zip || !channel.ok} onClick={() => void install()}>{primaryAction.label}</Action>
      </div>
      {refreshAction}
    </Focusable>}
    {releaseState.error && onNetwork && <Action secondary kind="network" onClick={onNetwork}>{t("更换连接方式")}</Action>}
  </>;
  const pluginHeading = <Heading title={plugin.name} icon={<PluginIcon plugin={plugin} />}>
    {t("当前版本 {0}", [plugin.version ?? t("未知")])}{plugin.repo && eligible ? " · GitHub" : ""}
    {plugin.ownership?.state === "owned" && <><br />{t("已接管，商店检查已暂停")}</>}
  </Heading>;
  if (readOnly) return <>
    {checkRecord && <CheckRecord {...checkRecord} historical />}
    <PanelSection>{pluginHeading}
      <Status title={t("仅供查看")}>{managementReason(plugin)}</Status>
      <Status title={t("包内版本")}>{plugin.version ?? t("未核验")}</Status>
      <Status title={t("Loader记录版本")}>{plugin.management?.version ?? t("未知")}</Status>
      {plugin.folder && <Status title={t("磁盘目录")}>{plugin.folder}</Status>}
      <Status title={t("商店来源")}>{plugin.in_store === null ? t("未知") : plugin.in_store ? t("当前商店已收录") : t("当前商店未收录")}</Status>
    </PanelSection>
  </>;
  const ownershipAction = async (action: "return" | "read" | "continue") => {
    await changeOwnership(plugin, action);
    onOwnershipChanged?.();
    await refreshPlugins();
  };
  if (!eligible) return <>
    {checkRecord && <CheckRecord {...checkRecord} historical />}
    <PanelSection>{pluginHeading}
      <Status title={plugin.memory_changed ? t("身份已变化，需重新确认") : plugin.ownership?.state === "pending" ? t("接管结果未确认") : pluginCanTakeOver(plugin) ? t("由商店负责") : t("归属待确认")}>{plugin.ownership?.error}</Status>
      <Explanation>{t("仓库关联、已加载状态和旧检查记录不代表接管授权。")}</Explanation>
      {plugin.memory_changed && plugin.ownership?.intent?.state === "active" ? <Action kind="returnStore" disabled={blocked || ownership.busy} onClick={() => void ownershipAction("return")}>{t("交还商店")}</Action> : plugin.ownership?.intent && plugin.ownership.intent.state !== "released" ? <>
        <Explanation>{t("此前操作尚需回查。继续会读取最新冻结列表，仅调整此插件；未知结果不会自动重试或恢复旧列表。")}</Explanation>
        <Action kind="readOwnership" disabled={blocked || ownership.busy} onClick={() => void ownershipAction("read")}>{t("回查接管结果")}</Action>
        <Action kind="takeover" disabled={blocked || ownership.busy} onClick={() => void ownershipAction("continue")}>{t("显式继续冻结操作")}</Action>
      </> : pluginCanTakeOver(plugin) && <Action kind="takeover" disabled={blocked || ownership.busy} onClick={onBinding}>{t("接管商店插件")}</Action>}
      {pluginCanBind(plugin) && <Action kind="binding" disabled={blocked} onClick={onBinding}>{t("关联仓库以修复非商店插件追踪")}</Action>}
      {ownership.error && <Status tone="error" title={t("接管未确认")}>{ownership.error}</Status>}
    </PanelSection>
  </>;
  if (section === "versions") return <PanelSection>
    <Heading title={t("选择版本")}>{plugin.name}</Heading>
    {skippedNotice}
    {releaseStatus}
    {releaseState.snapshot && <ReleasePicker presentation="list" releases={releases} tag={selection.tag} zip={selection.zip} skipped={plugin.skipped_tags ?? []}
      onChange={(tag, zip) => { setSelection({ tag, zip }); onVersionSelected?.(); }} />}
  </PanelSection>;
  if (section === "maintenance") return <>
    <PanelSection><Heading title={t("维护此插件")}>{plugin.name}</Heading>
      {skippedNotice}
      <Status tone="warning" title={t("维护前请确认")}>{t("日常更新不需要来这里。重装、来源变更和卸载均保留独立确认。")}</Status>
      {plugin.repo && releaseStatus}
      {release?.comparison === 0 && action && <Action secondary kind="replace" disabled={busy || blocked || ownership.busy || !selection.zip || !channel.ok} onClick={() => void install()}>{action.label}</Action>}
      <Action secondary kind="reinstall" description={release ? t("所选版本 {0}", [release.tag]) : undefined} disabled={busy || blocked || ownership.busy || !selection.zip || !channel.ok || !action} onClick={() => void install("reinstall")}>{t("卸载并重新安装")}</Action>
      {onVersions && <Action secondary kind="readReleases" onClick={onVersions}>{t("选择其他版本")}</Action>}
      {error && <Status tone="error" title={t("操作失败")}>{error}</Status>}
      {!channel.ok && <Status tone="error" title={t("安装通道不可用")}>{channel.reason}</Status>}
    </PanelSection>
    <PanelSection>{plugin.ownership?.state === "owned" ? <>
      <Explanation>{t("交还仅恢复接管前的商店冻结策略，停止本插件追踪，保留程序、数据和旧仓库关联。原本冻结的插件仍保持冻结。")}</Explanation>
      <Action kind="returnStore" disabled={blocked || ownership.busy} onClick={() => void ownershipAction("return")}>{t("交还商店")}</Action>
      {ownership.error && <Status tone="error" title={t("交还未确认")}>{ownership.error}</Status>}
    </> : <Action secondary kind="manageBinding" description={plugin.repo ?? t("尚未关联")} onClick={onBinding}>{t("管理仓库关联")}</Action>}
    </PanelSection>
    <PanelSection>
    <Action secondary kind="refreshRecognition" description={t("识别结果不对？试试刷新")} disabled={blocked || busy || ownership.busy} onClick={() => void refreshPlugins(true)}>{t("重新识别插件")}</Action>
    </PanelSection>
    <PanelSection title={t("移除插件")}><Action secondary appearance="danger" kind="uninstall" description={t("卸载前仍需在原生窗口确认。")} disabled={!channel.ok || blocked || ownership.busy} onClick={() => { void requestUninstall(plugin.name).catch((err) => setError(errorText(err))); }}>{t("卸载插件")}</Action></PanelSection>
  </>;
  return <>
    {checkRecord && <CheckRecord {...checkRecord} historical={!checkFor(currentPlugin ?? plugin, { checkedAt: checkRecord.checkedAt, items: [checkRecord.item] })} />}
    <PanelSection>{pluginHeading}
      {skippedNotice}
      {managementReason(plugin) && <Status title={t("Loader 管理状态")}>{managementReason(plugin)}</Status>}
      {plugin.management?.versionMismatch && <Status tone="warning" title={t("版本记录不一致")}>{t("包内版本")} {plugin.version ?? t("未知")}{t("，Loader记录版本")} {plugin.management.version ?? t("未知")}{t("。不会自动修复或判定安装成功。")}</Status>}
      {!plugin.repo ? <Action kind="binding" onClick={onBinding}>{t("关联 GitHub 仓库")}</Action> : <>
        {releaseStatus}
        {actionBusy && <Status tone="loading" title={t("正在处理…")} />}
        {releaseState.snapshot && <>
          {release?.comparison === null && <Status tone="warning" title={t("版本无法自动比较")}>{t("当前")} {plugin.version ?? t("未知")}{t("，目标")} {release.tag}{t("。请核对后安装。")}</Status>}
          {release?.comparison === 0 && <Status title={t("该版本已安装，无需操作")}>{t("需要重装时，请进入维护此插件。")}</Status>}
          <ReleasePicker presentation="summary" currentVersion={plugin.version} releases={releases} tag={selection.tag} zip={selection.zip} skipped={plugin.skipped_tags ?? []} onChange={(tag, zip) => setSelection({ tag, zip })} />
        </>}
      </>}
      {error && <Status tone="error" title={t("操作失败")}>{error}</Status>}
      {!channel.ok && <Status tone="error" title={t("安装通道不可用")}>{channel.reason}</Status>}
      {(plugin.repo && (onVersions || release) || onMaintenance) && <div className="gss-detail-navigation">
        {plugin.repo && onVersions && <Action secondary kind="readReleases" onClick={onVersions}>{t("选择其他版本")}</Action>}
        {onMaintenance && <Action secondary kind="maintenance" onClick={onMaintenance}>{t("维护此插件")}</Action>}
        {plugin.repo && release && release.comparison !== 0 && <Action secondary kind="skipRelease" disabled={busy || blocked} onClick={() => void toggleSkip()}>{(plugin.skipped_tags ?? []).includes(release.tag) ? t("取消跳过") : t("跳过此版本")}</Action>}
      </div>}
    </PanelSection>
  </>;
}
