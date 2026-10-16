// Semantic index:
// - participant: docs/upstream-conflict/sideload/plugin-management.md -- compact scheduled result rows preserve snapshot details and stable focus during live refreshes.
import { Field, PanelSection, PanelSectionRow } from "@decky/ui";
import { useEffect, useRef } from "react";
import { refreshPlugins, useChecks } from "./checkController";
import { Status, StatusText, checkTone } from "./components";
import { pluginEligible } from "./loaderManagement";
import { checkFor, checkItemKey, checkStatusLabels, latestScheduledReport, orderedCheckItems, shortCheckTime } from "./viewModel";
import { checkStatusIcons, historicalIcon, updateClassName } from "./uiIcons";
import type { CheckReport, PluginRow, UpdateInfo } from "./types";

export function CheckResults({ report: saved, selected, onOpen }: {
  report: CheckReport; selected: string | null;
  onOpen: (item: UpdateInfo, report: CheckReport, plugin: PluginRow | null) => void;
}) {
  const { list, scheduledReport } = useChecks();
  const report = latestScheduledReport(saved, scheduledReport)!;
  const order = useRef(new Map<string, number>());
  const items = orderedCheckItems(report);
  for (const item of items) if (!order.current.has(checkItemKey(item))) order.current.set(checkItemKey(item), order.current.size);
  items.sort((a, b) => order.current.get(checkItemKey(a))! - order.current.get(checkItemKey(b))!);
  useEffect(() => { void refreshPlugins(); }, []);
  return <PanelSection title="定时检查结果">
    <Status title={shortCheckTime(report.checkedAt)} />
    {report.error && <Status tone="error" title="整轮检查失败">{report.error}</Status>}
    {!report.error && !items.length && <Status title="没有可检查的插件" />}
    {items.map((item) => {
      const key = checkItemKey(item);
      const plugin = list?.plugins.find((p) => !p.is_self && p.name === item.name && p.repo === item.repo && p.version === item.current) ?? null;
      const changed = !!list && (!plugin || !pluginEligible(plugin) || checkFor(plugin, report) !== item);
      const Icon = changed ? historicalIcon : checkStatusIcons[item.status === "update" && !plugin ? "uncomparable" : item.status];
      const versions = item.status === "update" && item.latest ? `${item.current ?? "未知"} → ${item.latest}` : undefined;
      const open = () => onOpen(item, report, plugin && pluginEligible(plugin) && changed ? null : plugin);
      return <PanelSectionRow key={key}><Field data-check-key={key} aria-label={item.name} label={<span title={item.name} style={{ display: "block", maxWidth: "100%", minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{item.name}</span>}
        description={versions && (versions.length <= 32 ? versions : "查看版本详情")}
        icon={<Icon aria-hidden focusable="false" className={item.status === "update" && plugin && !changed ? updateClassName : undefined} />}
        bottomSeparator="none"
        childrenContainerWidth="min" inlineWrap="keep-inline" focusable highlightOnFocus preferredFocus={key === selected}
        onOKActionDescription="查看详情" onActivate={open} onClick={open}>
        <span style={{ whiteSpace: "nowrap", textAlign: "right" }}><StatusText tone={changed ? "muted" : checkTone(item.status)}>{changed ? "历史记录" : checkStatusLabels[item.status]}</StatusText></span>
      </Field></PanelSectionRow>;
    })}
  </PanelSection>;
}

export function CheckRecord({ item, checkedAt, historical = false }: { item: UpdateInfo; checkedAt: string; historical?: boolean }) {
  return <PanelSection title="定时检查记录">
    <Status title={item.name}><StatusText tone={historical ? "muted" : checkTone(item.status)}>{checkStatusLabels[item.status]}</StatusText>，{shortCheckTime(checkedAt)}</Status>
    <Status title="检查时的仓库">{item.repo ?? "未关联仓库"}</Status>
    <Status title="检查时的版本">{item.current ?? "未知版本"}{item.latest ? ` → ${item.latest}` : ""}</Status>
    {item.error && <Status tone="error" title="检查失败原因">{item.error}</Status>}
  </PanelSection>;
}
