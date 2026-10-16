// Semantic index:
// - participant: docs/upstream-conflict/sideload/plugin-management.md -- show independent source refresh feedback and identity-matched states while retaining one native focus and stable rows.
import { Field, PanelSection, PanelSectionRow } from "@decky/ui";
import { PluginIcon } from "./pluginIcons";
import { useEffect, useRef, useState } from "react";
import { refreshPlugins, runCheck, useChecks } from "./checkController";
import { Action, Explanation, Status, StatusText, checkTone } from "./components";
import type { CheckReport, PluginRow } from "./types";
import { updateIcon as UpdateIcon, updateClassName } from "./uiIcons";
import { checkFor } from "./viewModel";

import { managementReason, pluginKey, pluginReadOnly, pluginEligible } from "./loaderManagement";

let showStorePlugins = false;

function pluginDescription(plugin: PluginRow, report: CheckReport | null) {
  if (pluginReadOnly(plugin)) return <>{managementReason(plugin)}{plugin.management?.version !== null ? `，Loader记录版本 ${plugin.management?.version ?? "未知"}` : ""}</>;
  if (!pluginEligible(plugin)) return <>{plugin.ownership?.state === "pending" ? "接管结果未确认" : plugin.in_store === true || plugin.ownership?.state === "store" ? "由商店负责" : "归属未知"}{managementReason(plugin) ? `，${managementReason(plugin)}` : ""}{plugin.in_store === null ? "，商店来源未知" : ""}{plugin.registration === "registered" ? "，官方数据库已登记" : ""}</>;
  const result = checkFor(plugin, report);
  const status = !plugin.repo ? plugin.repo_source === "disabled" ? "已停止更新追踪" : "需补充 GitHub"
    : !result ? "尚未检查更新" : {
      update: "可更新", current: "已是最新", ahead: "本机版本高于最新发布", uncomparable: "版本无法比较",
      unbound: "需补充 GitHub", no_release: "仓库没有发布版本", no_asset: "没有安装包", error: "检查失败，进入详情重试",
    }[result.status];
  return <>{plugin.version ?? "未知版本"}{managementReason(plugin) ? `，${managementReason(plugin)}` : ""}{plugin.management?.versionMismatch ? "，Loader记录版本不一致" : ""}，<StatusText tone={!plugin.repo || !result ? "muted" : checkTone(result.status)}>{result?.status === "update" && <UpdateIcon aria-hidden focusable="false" className={updateClassName} />}{status}</StatusText>{result?.latest && ["update", "uncomparable", "no_asset"].includes(result.status) ? `，远端 ${result.latest}` : ""}{plugin.registration === "registered" ? "，官方数据库已登记" : plugin.in_store === null ? "，商店来源未知" : ""}</>;
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
  const plugins = list?.plugins.filter((p) => !p.is_self && (pluginReadOnly(p) || ["ambiguous", "unregistered", "disabled"].includes(p.management?.state ?? "") || p.management?.versionMismatch || pluginEligible(p) || p.ownership?.state === "pending" || p.in_store === null)) ?? [];
  const storePlugins = list?.plugins.filter((p) => !p.is_self && p.in_store === true && !pluginReadOnly(p) && !pluginEligible(p) && p.ownership?.state !== "pending" && !plugins.some((current) => pluginKey(current) === pluginKey(p))) ?? [];
  const updates = plugins.filter((p) => checkFor(p, report)?.status === "update");
  const failures = plugins.filter((p) => checkFor(p, report)?.status === "error");
  const titles = ["可更新", "待关联仓库", "其余插件"];
  for (const p of plugins) {
    if (!grouping.current.has(pluginKey(p))) grouping.current.set(pluginKey(p), checkFor(p, report)?.status === "update" ? titles[0] : pluginEligible(p) && !p.repo ? titles[1] : titles[2]);
  }
  return <>
    <PanelSection>
      <Status icon={updates.length ? UpdateIcon : undefined} iconClassName={updates.length ? updateClassName : undefined} tone={report?.error ? "error" : undefined} title={report?.error ? `检查失败：${report.error}` : report ? `${updates.length} 个可更新${failures.length ? `，${failures.length} 个检查失败` : ""}` : "尚未检查更新"}>
        {report ? `上次检查：${new Date(report.checkedAt).toLocaleString()}` : "关联 GitHub 后可检查版本"}
      </Status>
      <Action kind="checkUpdates" loading={busy} onClick={() => void runCheck()} disabled={busy}>{busy ? "正在检查…" : "检查更新"}</Action>
      <Action secondary kind="newInstall" onClick={onInstall}>从 GitHub 安装</Action>
      {checkError && <Status tone="error" title="检查失败">{checkError}</Status>}
      {listError && <Status tone="error" title="来源读取失败">{listError}</Status>}
      {list?.loader && <Status tone={list.loader.ok ? undefined : "warning"} title={list.loader.ok ? `Loader 管理清单：${list.loader.count} 项` : "Loader 管理读取失败"}>{list.loader.error}</Status>}
      {list?.catalog && <Status tone={list.catalog.stale || list.catalog.source === "registry" || list.catalog.source === "unavailable" ? "warning" : undefined}
        title={{ live: "来源：实时商店目录", cache: list.catalog.stale ? "来源：过期商店缓存" : "来源：商店缓存", registry: "来源：官方仓库登记辅助", unavailable: "来源：完全不可用" }[list.catalog.source]}>
        {list.catalog.timestamp !== null && `目录时间：${new Date(list.catalog.timestamp * 1000).toLocaleString()}。`}
        {list.catalog.source === "registry" && "登记不等于当前商店收录，未匹配项保持未知。"}
        {list.catalog.stale && "缓存已过期，当前商店收录情况未知。"}
        {list.catalog.warning}
      </Status>}
      {((list && !list.store_ok) || listError || (list?.loader && !list.loader.ok)) && <>
        {list && !list.catalog && <Status tone="warning" title="来源识别失败">{list.store_error}。仍可管理已关联项。</Status>}
        <Action kind="refreshTask" loading={refreshing} onClick={() => void refreshPlugins()} disabled={busy || refreshing}>{refreshing ? busy ? "等待检查完成…" : "正在识别来源…" : "重试来源识别"}</Action>
      </>}
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
