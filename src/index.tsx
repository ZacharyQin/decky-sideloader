// Semantic index:
// - owner: docs/upstream-conflict/sideload/plugin-management.md -- own native inner-page navigation and terminal notice dismissal with retained task access and one-shot return focus.
// - participant: docs/upstream-conflict/sideload/diagnostics-sharing.md -- wire pickup lifecycle once per plugin load without QAM replay.
// - participant: docs/upstream-conflict/sideload/diagnostics.md -- own diagnostic timers and lifecycle outside QAM component mounts.
import { Focusable, NavEntryPositionPreferences, PanelSection, ScrollPanel, staticClasses } from "@decky/ui";
import { addEventListener, definePlugin, removeEventListener } from "@decky/api";
import { FaGithub } from "react-icons/fa";
import { stopOwnershipController } from "./ownershipController";
import { BindingEditor } from "./BindingEditor";
import { Home } from "./Home";
import { InstallTab } from "./InstallTab";
import { InstalledTab } from "./InstalledTab";
import { NetworkSettings } from "./NetworkSettings";
import { DiagnosticsSettings } from "./DiagnosticsSettings";
import { SettingsTab } from "./SettingsTab";
import { CheckRecord, CheckResults } from "./CheckResults";
import { startDiagnostics } from "./diagnosticsController";
import { getDiagnosticsAddresses, getDiagnosticsShare, startDiagnosticsShare, stopDiagnosticsShare, exportDiagnostics, getDiagnosticsStatus, recordDiagnosticEvents, listSideloadPlugins } from "./api";
import { Action, Status } from "./components";
import { OperationPanel, stageLabel } from "./OperationPanel";
import { dismissTaskNotice, isActive, refreshOperation, startInstallController, useInstallation } from "./installController";
import { toastError, toastInfo } from "./toast";
import { updateViewState, useViewState, type Page } from "./viewState";
import { receiveScheduledCheck, refreshPlugins } from "./checkController";
import { pluginKey, pluginReadOnly } from "./loaderManagement";
import { checkItemKey, checkReportSummary } from "./viewModel";
import type { CheckReport, PluginRow, ScheduledCheckEvent, UpdateInfo } from "./types";

function Content() {
  const { operation, error: operationError, dismissing } = useInstallation();
  const { page, plugin, detailOrigin, settingsFocus, networkFocus, diagnosticsFocus, focusReturn, checkReport, checkItem, checkSelection } = useViewState();
  const navigate = (next: Page, selected = plugin) => {
    updateViewState((current) => ({ ...current, page: next, plugin: selected, focusReturn: current.page === "details" && (next === "home" || next === "checks") ? next : null, settingsFocus: next === "settings" && current.page === "checks", diagnosticsFocus: next === "settings" && current.page === "diagnostics", networkFocus: next === "settings" && current.page === "network" }));
  };
  const openPlugin = (next: PluginRow) => updateViewState((current) => ({ ...current, page: "details", plugin: next, detailOrigin: "home", checkItem: null }));
  const openCheck = (item: UpdateInfo, report: CheckReport, currentPlugin: PluginRow | null) => updateViewState((current) => ({
    ...current, page: "details", plugin: currentPlugin, detailOrigin: "checks", checkItem: item, checkReport: report, checkSelection: checkItemKey(item),
  }));
  const backPage = page === "binding" ? "details" : page === "checks" || page === "network" || page === "diagnostics" ? "settings" : page === "details" && detailOrigin === "checks" ? "checks" : "home";
  const back = () => navigate(backPage);
  const backLabel = backPage === "details" ? "插件详情" : backPage === "checks" ? "检查结果" : backPage === "settings" ? "设置" : "我的插件";
  const bindingSaved = async () => {
    try {
      const listing = await listSideloadPlugins();
      const next = listing.plugins.find((p) => plugin && pluginKey(p) === pluginKey(plugin)) ?? null;
      void refreshPlugins();
      navigate(next ? "details" : "home", next);
    } catch (err) { toastError(err); }
  };
  return <Focusable key={page} autoFocus flow-children="column" navEntryPreferPosition={focusReturn || settingsFocus || networkFocus || diagnosticsFocus ? NavEntryPositionPreferences.PREFERRED_CHILD : NavEntryPositionPreferences.FIRST}
    onGamepadFocus={() => { if (focusReturn || settingsFocus || networkFocus || diagnosticsFocus) updateViewState((current) => ({ ...current, focusReturn: null, settingsFocus: false, networkFocus: false, diagnosticsFocus: false })); }} onCancel={(event) => {
    if (page !== "home") { event.preventDefault(); event.stopPropagation(); back(); }
  }} onCancelActionDescription="返回">
    <ScrollPanel>
      {page !== "operation" && operation && !operation.notice_dismissed && <PanelSection><Action secondary kind="viewTask" onClick={() => navigate("operation")}>{operation.outcome === "success" ? "安装已完成" : stageLabel[operation.stage]}，查看任务</Action>{!isActive(operation) && <Action secondary kind="dismissTask" loading={dismissing} onClick={() => void dismissTaskNotice(operation.id)}>关闭任务提示</Action>}</PanelSection>}
      {page !== "operation" && operationError && <PanelSection><Status tone="error" title="安装状态读取失败">{operationError}</Status><Action kind="refreshTask" onClick={() => void refreshOperation()}>重新读取任务</Action></PanelSection>}
      {page !== "home" && <PanelSection><Action kind="back" onClick={back}>返回{backLabel}</Action></PanelSection>}
      {page === "home" && <Home selected={focusReturn === "home" && plugin ? pluginKey(plugin) : null} onOpen={openPlugin} onInstall={() => navigate("install")} onSettings={() => navigate("settings")} />}
      {page === "details" && plugin && <InstalledTab plugin={plugin} checkRecord={detailOrigin === "checks" && checkItem && checkReport ? { item: checkItem, checkedAt: checkReport.checkedAt } : undefined} onBinding={() => navigate("binding")} onOwnershipChanged={() => void bindingSaved()} onOperation={() => navigate("operation")} />}
      {page === "details" && !plugin && checkItem && checkReport && <><Status tone="warning" title="历史检查记录">该记录未匹配当前插件，仅供查看。</Status><CheckRecord item={checkItem} checkedAt={checkReport.checkedAt} historical /></>}
      {page === "binding" && plugin && !pluginReadOnly(plugin) && <BindingEditor plugin={plugin} onSaved={() => void bindingSaved()} />}
      {page === "install" && <InstallTab onExisting={openPlugin} onOperation={() => navigate("operation")} />}
      {page === "settings" && <SettingsTab onTask={operation ? () => navigate("operation") : undefined} onDiagnostics={() => navigate("diagnostics")} focusDiagnostics={diagnosticsFocus} onNetwork={() => navigate("network")} focusNetwork={networkFocus} focusResults={settingsFocus} report={settingsFocus ? checkReport : null} onResults={(report) => updateViewState((current) => ({ ...current, page: "checks", checkReport: report, checkSelection: null }))} />}
      {page === "diagnostics" && <DiagnosticsSettings />}
      {page === "network" && <NetworkSettings />}
      {page === "checks" && checkReport && <CheckResults report={checkReport} selected={focusReturn === "checks" ? checkSelection : null} onOpen={openCheck} />}
      {page === "operation" && <OperationPanel />}
    </ScrollPanel>
  </Focusable>;
}

export default definePlugin(() => {
  const stopDiagnostics = startDiagnostics({ record: recordDiagnosticEvents, export: exportDiagnostics, status: getDiagnosticsStatus,
    sharing: { addresses: getDiagnosticsAddresses, status: getDiagnosticsShare, start: startDiagnosticsShare, stop: stopDiagnosticsShare } });
  const stopInstallController = startInstallController();
  const listener = addEventListener<[event: ScheduledCheckEvent]>("scheduled_check_completed", (event) => {
    if (receiveScheduledCheck(event)) toastInfo(checkReportSummary(event.report));
  });
  return {
    name: "GitHub Sideload",
    titleView: <div className={staticClasses.Title}>GitHub Sideload</div>,
    content: <Content />,
    icon: <FaGithub />,
    onDismount() { stopOwnershipController(); stopDiagnostics(); stopInstallController(); removeEventListener("scheduled_check_completed", listener); },
  };
});
