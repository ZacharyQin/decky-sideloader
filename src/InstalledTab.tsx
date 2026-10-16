// Semantic index:
// - participant: docs/upstream-conflict/sideload/plugin-management.md -- keep ordinary install distinct from confirmed reinstall preparation and native standalone uninstall.
import { PanelSection } from "@decky/ui";
import { useEffect, useState } from "react";
import { managementReason, pluginKey, pluginReadOnly, pluginEligible, pluginCanBind } from "./loaderManagement";
import { listReleases } from "./api";
import { Action, errorText, Explanation, Status } from "./components";
import { ReleasePicker } from "./ReleasePicker";
import { CheckRecord } from "./CheckResults";
import { probeLoaderChannel } from "./loaderBridge";
import { isActive, requestUninstall, startInstallation, useInstallation } from "./installController";
import { checkFor, initialSelection, versionAction } from "./viewModel";
import { getViewState, updateViewState, useViewState } from "./viewState";
import { changeOwnership, useOwnership } from "./ownershipController";
import { refreshPlugins, useChecks } from "./checkController";
import type { PluginRow, ReleaseRow, UpdateInfo } from "./types";

export function InstalledTab({ plugin: selectedPlugin, onBinding, onOperation, onOwnershipChanged, checkRecord }: { plugin: PluginRow; onBinding: () => void; onOperation: () => void; onOwnershipChanged?: () => void; checkRecord?: { item: UpdateInfo; checkedAt: string } }) {
  const { list } = useChecks();
  const currentPlugin = list?.plugins.find((p) => p.name === selectedPlugin.name && p.folder === selectedPlugin.folder);
  // A cached list must not grant permissions over a newer selected pending or
  // returned snapshot. Explicit ownership completion reloads the selection.
  const owner = selectedPlugin.ownership && !selectedPlugin.ownership.eligible ? selectedPlugin.ownership : currentPlugin?.ownership ?? selectedPlugin.ownership;
  const plugin = { ...selectedPlugin, ownership: owner,
    in_store: owner === currentPlugin?.ownership ? currentPlugin?.in_store ?? selectedPlugin.in_store : selectedPlugin.in_store,
    ...(owner?.state === "owned" && owner.intent ? { repo: owner.intent.repo, repo_source: "user" as const } : {}) };
  const ownership = useOwnership(plugin);
  const { operation } = useInstallation();
  const readOnly = pluginReadOnly(plugin);
  const eligible = pluginEligible(plugin);
  const blocked = isActive(operation);
  const [releases, setReleases] = useState<ReleaseRow[]>([]);
  const key = JSON.stringify([pluginKey(plugin), plugin.repo, plugin.version]);
  const savedSelection = useViewState().detailSelection;
  const selection = savedSelection.key === key ? savedSelection : { tag: null, zip: null };
  const setSelection = (next: { tag: string | null; zip: string | null }) => updateViewState((current) => ({ ...current, detailSelection: { key, ...next } }));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);
  const channel = probeLoaderChannel();
  useEffect(() => {
    let active = true;
    setReleases([]); setError(null); setBusy(false);
    if (!eligible || !plugin.repo) return;
    setBusy(true);
    void listReleases(plugin.repo, plugin.version).then((rows) => {
      if (active) {
        setReleases(rows);
        const saved = getViewState().detailSelection;
        const selected = rows.find((row) => row.tag === saved.tag);
        setSelection(saved.key === key ? {
          tag: selected?.tag ?? null,
          zip: selected?.zips.some((asset) => asset.url === saved.zip) ? saved.zip : null,
        } : initialSelection(rows));
      }
    }).catch((err) => { if (active) setError(errorText(err)); })
      .finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [key, eligible, retry]);
  const release = releases.find((r) => r.tag === selection.tag);
  const action = release ? versionAction(release, true) : null;
  const install = async (mode: "install" | "reinstall" = "install") => {
    if (!eligible || !release || !selection.zip || !action) return;
    setBusy(true); setError(null);
    try { if (await startInstallation(release, selection.zip, plugin, undefined, mode)) onOperation(); }
    catch (err) { setError(errorText(err)); }
    finally { setBusy(false); }
  };
  if (readOnly) return <>
    {checkRecord && <CheckRecord {...checkRecord} historical />}
    <PanelSection title={plugin.name}>
      <Status title="仅供查看">{managementReason(plugin)}</Status>
      <Status title="包内版本">{plugin.version ?? "未核验"}</Status>
      <Status title="Loader记录版本">{plugin.management?.version ?? "未知"}</Status>
      {plugin.folder && <Status title="磁盘目录">{plugin.folder}</Status>}
      <Status title="商店来源">{plugin.in_store === null ? "未知" : plugin.in_store ? "当前商店已收录" : "当前商店未收录"}</Status>
    </PanelSection>
  </>;
  const ownershipAction = async (action: "return" | "read" | "continue") => {
    await changeOwnership(plugin, action);
    onOwnershipChanged?.();
    await refreshPlugins();
  };
  if (!eligible) return <>
    {checkRecord && <CheckRecord {...checkRecord} historical />}
    <PanelSection title={plugin.name}>
      <Status title={plugin.ownership?.state === "pending" ? "接管结果未确认" : plugin.in_store === true || plugin.ownership?.state === "store" ? "由商店负责" : "归属未知"}>{plugin.ownership?.error}</Status>
      <Explanation>仓库关联、已加载状态和旧检查记录不代表接管授权。</Explanation>
      {plugin.ownership?.intent && plugin.ownership.intent.state !== "released" ? <>
        <Explanation>此前操作尚需回查。继续会读取最新冻结列表，仅调整此插件；未知结果不会自动重试或恢复旧列表。</Explanation>
        <Action kind="readOwnership" disabled={blocked || ownership.busy} onClick={() => void ownershipAction("read")}>回查接管结果</Action>
        <Action kind="takeover" disabled={blocked || ownership.busy} onClick={() => void ownershipAction("continue")}>显式继续冻结操作</Action>
      </> : plugin.in_store === true && <Action kind="takeover" disabled={blocked || ownership.busy} onClick={onBinding}>接管商店插件</Action>}
      {pluginCanBind(plugin) && <Action kind="binding" disabled={blocked} onClick={onBinding}>关联仓库以修复非商店插件追踪</Action>}
      {ownership.error && <Status tone="error" title="接管未确认">{ownership.error}</Status>}
    </PanelSection>
  </>;
  return <>
    {checkRecord && <CheckRecord {...checkRecord} historical={!checkFor(currentPlugin ?? plugin, { checkedAt: checkRecord.checkedAt, items: [checkRecord.item] })} />}
    <PanelSection title={plugin.name}>
      <Status title={`当前版本 ${plugin.version ?? "未知"}`}>{plugin.ownership?.state === "owned" ? "已接管，商店检查已暂停" : plugin.repo_source === "user" ? "手动加入管理" : plugin.in_store === false ? "当前商店未收录" : plugin.in_store === null ? "来源尚未识别" : "当前商店已收录"}</Status>
      {managementReason(plugin) && <Status title="Loader 管理状态">{managementReason(plugin)}</Status>}
      {plugin.management?.versionMismatch && <Status tone="warning" title="版本记录不一致">包内版本 {plugin.version ?? "未知"}，Loader记录版本 {plugin.management.version ?? "未知"}。不会自动修复或判定安装成功。</Status>}
      {!plugin.repo ? <Action kind="binding" onClick={onBinding}>关联 GitHub 仓库</Action> : <>
        {busy && <Status tone="loading" title="正在处理…" />}
        {!busy && !error && <ReleasePicker releases={releases} tag={selection.tag} zip={selection.zip} onChange={(tag, zip) => setSelection({ tag, zip })} />}
        {release?.comparison === null && <Status tone="warning" title="版本无法自动比较">当前 {plugin.version ?? "未知"}，目标 {release.tag}。请核对后安装。</Status>}
        {action && <Action kind="replace" disabled={busy || blocked || ownership.busy || !selection.zip || !channel.ok} onClick={() => void install()}>{action.label}</Action>}
        {action && <Action kind="reinstall" disabled={busy || blocked || ownership.busy || !selection.zip || !channel.ok} onClick={() => void install("reinstall")}>卸载并安装</Action>}
      </>}
      {error && <><Status tone="error" title="操作失败">{error}</Status><Action kind="readReleases" onClick={() => setRetry(retry + 1)}>重新读取版本</Action></>}
      {!channel.ok && <Status tone="error" title="安装通道不可用">{channel.reason}</Status>}
    </PanelSection>
    <PanelSection title="GitHub 仓库"><Status title={plugin.repo ?? "尚未关联"} />{plugin.ownership?.state === "owned" ? <>
      <Explanation>已接管，商店检查已暂停。交还仅恢复本次接管前的冻结策略，停止本插件追踪，保留程序、数据和旧仓库关联。原本冻结的插件仍保持冻结。</Explanation>
      <Action kind="returnStore" disabled={blocked || ownership.busy} onClick={() => void ownershipAction("return")}>交还商店</Action>
      {ownership.error && <Status tone="error" title="交还未确认">{ownership.error}</Status>}
    </> : <Action kind="manageBinding" onClick={onBinding}>管理仓库关联</Action>}</PanelSection>
    <PanelSection><Action kind="uninstall" disabled={!channel.ok || blocked || ownership.busy} onClick={() => { void requestUninstall(plugin.name).catch((err) => setError(errorText(err))); }}>卸载插件</Action></PanelSection>
  </>;
}
