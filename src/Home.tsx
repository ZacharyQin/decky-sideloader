// Semantic index:
// - participant: docs/upstream-conflict/sideload/plugin-management.md -- show one explicit refresh icon and stable managed rows without exposing cache mechanics.
import { Field, PanelSection, PanelSectionRow } from "@decky/ui";
import { PluginIcon } from "./pluginIcons";
import { useEffect, useRef, useState } from "react";
import { refreshPlugins, runCheck, useChecks } from "./checkController";
import { Action, Explanation, Status, StatusText, checkTone } from "./components";
import type { CheckReport, PluginRow } from "./types";
import { updateIcon as UpdateIcon, updateClassName } from "./uiIcons";
import { checkFor } from "./viewModel";

import { managementReason, pluginKey, pluginReadOnly, pluginEligible, pluginCanTakeOver } from "./loaderManagement";

let showStorePlugins = false;

function pluginDescription(plugin: PluginRow, report: CheckReport | null) {
  if (pluginReadOnly(plugin)) return <>{managementReason(plugin)}{plugin.management?.version !== null ? `，Loader记录版本 ${plugin.management?.version ?? "未知"}` : ""}</>;
  if (!pluginEligible(plugin)) return <>{plugin.memory_changed ? "身份已变化，需重新确认" : plugin.ownership?.state === "pending" ? "接管结果未确认" : pluginCanTakeOver(plugin) ? "由商店负责" : "归属待确认"}{managementReason(plugin) ? `，${managementReason(plugin)}` : ""}</>;
  const result = checkFor(plugin, report);
  const status = !plugin.repo ? plugin.repo_source === "disabled" ? "已停止更新追踪" : "需补充 GitHub"
    : !result ? "尚未检查更新" : {
      update: "可更新", current: "已是最新", ahead: "本机版本高于最新发布", uncomparable: "版本无法比较",
      unbound: "需补充 GitHub", no_release: "仓库没有发布版本", no_asset: "没有安装包", error: "检查失败，进入详情重试",
    }[result.status];
  return <>{plugin.version ?? "未知版本"}{managementReason(plugin) ? `，${managementReason(plugin)}` : ""}{plugin.management?.versionMismatch ? "，Loader记录版本不一致" : ""}，<StatusText tone={!plugin.repo || !result ? "muted" : checkTone(result.status)}>{result?.status === "update" && <UpdateIcon aria-hidden focusable="false" className={updateClassName} />}{status}</StatusText>{result?.latest && ["update", "uncomparable", "no_asset"].includes(result.status) ? `，远端 ${result.latest}` : ""}</>;
}

export function Home({ onOpen, onInstall, onSettings, selected }: {
  onOpen: (plugin: PluginRow) => void; onInstall: () => void; onSettings: () => void; selected: string | null;
}) {
  const { list, checking: busy, refreshing, listError, checkError, error, revision } = useChecks();
  const [showStore, setShowStore] = useState(showStorePlugins);
  // Freeze grouping within this visit so background refresh cannot move a focused row.
  const grouping = useRef(new Map<string, string>());
  const report = list?.last_check ?? null;
  useEffect(() => { void refreshPlugins(); }, []);
  const groupedRevision = useRef(revision);
  if (groupedRevision.current !== revision) {
    grouping.current.clear(); groupedRevision.current = revision;
  }
  const plugins = list?.plugins.filter((p) => !p.is_self && (pluginReadOnly(p) || ["ambiguous", "unregistered", "disabled"].includes(p.management?.state ?? "") || p.management?.versionMismatch || pluginEligible(p) || p.ownership?.state === "pending" || p.memory_changed || !pluginCanTakeOver(p))) ?? [];
  const storePlugins = list?.plugins.filter((p) => pluginCanTakeOver(p) && !pluginEligible(p) && p.ownership?.state !== "pending" && !plugins.some((current) => pluginKey(current) === pluginKey(p))) ?? [];
  const updates = plugins.filter((p) => checkFor(p, report)?.status === "update");
  const failures = plugins.filter((p) => checkFor(p, report)?.status === "error");
  const titles = ["可更新", "待关联仓库", "其余插件"];
  for (const p of plugins) {
    if (!grouping.current.has(pluginKey(p))) grouping.current.set(pluginKey(p), checkFor(p, report)?.status === "update" ? titles[0] : pluginEligible(p) && !p.repo ? titles[1] : titles[2]);
  }
  return <>
    <PanelSection>
      <Status action={<Action iconOnly kind="refreshTask" loading={refreshing} disabled={busy || refreshing} onClick={() => void refreshPlugins(true)}>刷新列表</Action>} icon={updates.length ? UpdateIcon : undefined} iconClassName={updates.length ? updateClassName : undefined} tone={report?.error ? "error" : undefined} title={report?.error ? `检查失败：${report.error}` : report ? `${updates.length} 个可更新${failures.length ? `，${failures.length} 个检查失败` : ""}` : "尚未检查更新"}>
        {report ? `上次检查：${new Date(report.checkedAt).toLocaleString()}` : "关联 GitHub 后可检查版本"}
      </Status>
      <Action kind="checkUpdates" loading={busy} onClick={() => void runCheck()} disabled={busy || refreshing}>{busy ? "正在检查…" : "检查更新"}</Action>
      <Action secondary kind="newInstall" onClick={onInstall}>从 GitHub 安装</Action>
      {checkError && <Status tone="error" title="检查失败">{checkError}</Status>}
      {listError && <Status tone="error" title="刷新失败">{listError}</Status>}
      {list?.loader && !list.loader.ok && <Status tone="warning" title="插件列表读取失败">{list.loader.error}</Status>}
      {!list && !error && <Status tone="loading" title="正在读取插件…" />}
    </PanelSection>
    {titles.map((title) => {
      const items = plugins.filter((p) => grouping.current.get(pluginKey(p)) === title);
      // Keep focused rows in place, but never retain an update claim for stale results.
      const heading = title === "可更新" && items.some((p) => checkFor(p, report)?.status !== "update") ? "检查结果"
        : title === "待关联仓库" && items.some((p) => !pluginEligible(p)) ? "归属待确认" : title;
      return items.length > 0 && <PanelSection key={title} title={heading}>{items.map((plugin) => <PanelSectionRow key={pluginKey(plugin)}>
        <Field label={plugin.name} icon={<PluginIcon plugin={plugin} />} description={pluginDescription(plugin, report)} focusable highlightOnFocus
          preferredFocus={pluginKey(plugin) === selected} onOKActionDescription="管理"
          onActivate={() => onOpen(plugin)} onClick={() => onOpen(plugin)} />
      </PanelSectionRow>)}</PanelSection>;
    })}
    {showStore && <PanelSection title="接管商店插件">
      <Explanation>这些插件由商店负责。进入详情确认仓库后，可显式接管并暂停该插件的商店检查。</Explanation>
      {!storePlugins.length && <Status title="没有可接管的商店插件" />}
      {storePlugins.map((plugin) => <PanelSectionRow key={pluginKey(plugin)}><Field label={plugin.name} description="由商店负责" icon={<PluginIcon plugin={plugin} />} focusable highlightOnFocus onOKActionDescription="查看接管" onActivate={() => onOpen(plugin)} onClick={() => onOpen(plugin)} /></PanelSectionRow>)}
    </PanelSection>}
    <PanelSection>
      {list?.store_ok && plugins.length === 0 && <Status title="尚无可管理的插件">可从 GitHub 安装，或显式接管商店插件。</Status>}
      <Action secondary kind="takeover" onClick={() => { showStorePlugins = !showStore; setShowStore(!showStore); }}>{showStore ? "收起商店插件" : "接管商店插件"}</Action>
      <Action secondary kind="settings" onClick={onSettings}>设置</Action>
    </PanelSection>
  </>;
}
