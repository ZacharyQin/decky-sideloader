import { t } from "./i18n";
import { DialogButton, Focusable, NavEntryPositionPreferences, ScrollPanel, staticClasses } from "@decky/ui";
import { useEffect, useRef, useState } from "react";
import { addEventListener, definePlugin, removeEventListener } from "@decky/api";
import { FaCog, FaDownload, FaGithub } from "react-icons/fa";
import { stopOwnershipController } from "./ownershipController";
import { BindingEditor } from "./BindingEditor";
import { Home, StorePlugins } from "./Home";
import { InstallTab } from "./InstallTab";
import { InstalledTab } from "./InstalledTab";
import { ReleaseNotesPage } from "./ReleaseNotes";
import { NetworkSettings } from "./NetworkSettings";
import { DiagnosticsSettings } from "./DiagnosticsSettings";
import { LicensesPage } from "./LicensesPage";
import { SettingsTab, loadShowHomeTaskNotice, saveShowHomeTaskNotice, useShowHomeTaskNotice } from "./SettingsTab";
import { CheckRecord, CheckResults } from "./CheckResults";
import { recordDiagnosticView, recordUIAction, startDiagnostics } from "./diagnosticsController";
import { getDiagnosticsAddresses, getDiagnosticsShare, startDiagnosticsShare, stopDiagnosticsShare, exportDiagnostics, getDiagnosticsStatus, recordDiagnosticEvents, listSideloadPlugins } from "./api";
import { Action, PanelSection, Status } from "./components";
import { OperationPanel, stageLabel } from "./OperationPanel";
import { dismissTaskNotice, isActive, refreshOperation, startInstallController, useInstallation } from "./installController";
import { toastError, toastInfo } from "./toast";
import { getViewState, returnToInstallSearch, updateViewState, useViewState, type Page } from "./viewState";
import { receiveScheduledCheck, refreshPlugins, refreshAfterPluginMutation, disposeReleaseReads } from "./checkController";
import { pluginKey, pluginReadOnly } from "./loaderManagement";
import { checkItemKey, checkReportSummary } from "./viewModel";
import type { CheckReport, PluginRow, ScheduledCheckEvent, UpdateInfo } from "./types";
import { useLocale } from "./i18n";
import { presentationStyles } from "./presentation";
import { startNativeManagement } from "./nativeManagement";
import { DiagnosticBoundary } from "./DiagnosticBoundary";

function Content() {
  useLocale();
  const { operation, error: operationError, dismissing } = useInstallation();
  const showHomeTaskNotice = useShowHomeTaskNotice();
  const mutingRef = useRef(false);
  const [muting, setMuting] = useState(false);
  const muteTaskNotice = async () => {
    if (!operation || isActive(operation) || dismissing || mutingRef.current) return;
    const identifier = operation.id;
    mutingRef.current = true; setMuting(true);
    try {
      await saveShowHomeTaskNotice(false);
      await dismissTaskNotice(identifier);
    } catch (error) { toastError(error); }
    finally { mutingRef.current = false; setMuting(false); }
  };
  useEffect(() => { void loadShowHomeTaskNotice(); }, []);
  const { page, plugin, install, detailSelection, detailOrigin, operationOrigin, bindingOrigin, networkOrigin, releaseNotes, releaseNotesFocus, licenses, licensesFocus, settingsFocus, networkFocus, diagnosticsFocus, focusReturn, checkReport, checkItem, checkSelection } = useViewState();
  const selectedRelease = page === "install" ? install.selection.tag : detailSelection.tag;
  useEffect(() => { recordDiagnosticView(page, plugin?.name, selectedRelease); }, [page, plugin?.name, selectedRelease]);
  const completedId = operation?.outcome === "success" ? operation.id : null;
  useEffect(() => {
    if (!completedId) return;
    const selected = getViewState().plugin;
    let active = true;
    void refreshAfterPluginMutation().then((listing) => {
      if (!active || !listing || !selected) return;
      updateViewState((current) => {
        if (current.plugin !== selected) return current;
        const next = listing.plugins.find(row => row.name === selected.name && row.folder === selected.folder);
        return next ? { ...current, plugin: next } : current;
      });
    }).catch((error) => { if (active) toastError(error); });
    return () => { active = false; };
  }, [completedId]);
  const navigate = (next: Page, selected = plugin) => {
    updateViewState((current) => ({ ...current, page: next, plugin: selected,
      bindingOrigin: next === "binding" ? current.page === "maintenance" ? "maintenance" : "details" : current.bindingOrigin,
      networkOrigin: next === "network" ? current.page === "details" ? "details" : "settings" : current.networkOrigin,
      operationOrigin: next === "operation" ? current.page === "install" ? "install" : ["details", "versions", "maintenance"].includes(current.page) ? "details" : "home" : current.operationOrigin,
      releaseNotesFocus: current.page === "releaseNotes" && next === current.releaseNotes?.origin,
      releaseNotes: next === "releaseNotes" ? current.releaseNotes : null,
      licensesFocus: next === "settings" && current.page === "licenses",
      focusReturn: current.page === "details" && (next === "home" || next === "checks" || next === "source") ? next : null,
      settingsFocus: next === "settings" && current.page === "checks", diagnosticsFocus: next === "settings" && current.page === "diagnostics", networkFocus: next === "settings" && current.page === "network" }));
  };
  const openPlugin = (next: PluginRow) => updateViewState((current) => ({ ...current, page: "details", plugin: next, detailOrigin: "home", checkItem: null }));
  const openCheck = (item: UpdateInfo, report: CheckReport, currentPlugin: PluginRow | null) => updateViewState((current) => ({
    ...current, page: "details", plugin: currentPlugin, detailOrigin: "checks", checkItem: item, checkReport: report, checkSelection: checkItemKey(item),
  }));
  const backToSearch = page === "install" && !!install.probe || page === "details" && detailOrigin === "install";
  const backPage = backToSearch ? "install" : page === "operation" ? operationOrigin : page === "releaseNotes" ? releaseNotes?.origin ?? "home" : page === "binding" ? bindingOrigin : page === "maintenance" || page === "versions" ? "details" : page === "network" ? networkOrigin : page === "checks" || page === "diagnostics" || page === "source" || page === "licenses" ? "settings" : page === "details" ? detailOrigin : "home";
  const backLabel = page === "licenses" && licenses.document !== null ? t("返回组件详情") : page === "licenses" && licenses.entry ? t("返回开源许可") : backToSearch ? t("返回搜索") : t("返回到{0}页", [t(({ home: "插件列表", details: "插件详情", maintenance: "插件维护", settings: "设置", checks: "检查结果", source: "商店插件", install: "安装预览" } as const)[backPage])]);
  const back = () => {
    if (page === "licenses" && licenses.document !== null) updateViewState(current => ({ ...current,
      licenses: { ...current.licenses, document: null, focus: `license-document-${licenses.document}` } }));
    else if (page === "licenses" && licenses.entry) updateViewState(current => ({ ...current,
      licenses: { ...current.licenses, entry: null, focus: `license-entry-${licenses.entry}` } }));
    else if (backToSearch) returnToInstallSearch();
    else navigate(backPage);
  };
  const bindingSaved = async () => {
    try {
      const listing = await listSideloadPlugins();
      const next = listing.plugins.find((p) => plugin && pluginKey(p) === pluginKey(plugin)) ?? null;
      void refreshPlugins();
      navigate(next ? "details" : "home", next);
    } catch (err) { toastError(err); }
  };
  const taskNotice = page !== "operation" && operation && !operation.notice_dismissed && showHomeTaskNotice ? <div className="gss-task-notice">
    <Action secondary kind="viewTask" description={operation.package?.name ?? operation.existing_name ?? operation.repo} onClick={() => navigate("operation")}>{operation.outcome === "success" ? t("安装已完成") : stageLabel[operation.stage]}{t("，查看任务")}</Action>
    {!isActive(operation) && <Focusable className="gss-task-actions" flow-children="row">
      <DialogButton data-action-key="dismissTask" disabled={dismissing || muting} onClick={() => { recordUIAction("dismissTask"); void dismissTaskNotice(operation.id); }}>{t("关闭任务提醒")}</DialogButton>
      <DialogButton data-action-key="muteTaskNotice" disabled={dismissing || muting} onClick={() => { recordUIAction("muteTaskNotice"); void muteTaskNotice(); }}>{t("不再提醒")}</DialogButton>
    </Focusable>}
  </div> : null;
  return <Focusable key={page === "licenses" ? `${page}:${licenses.entry}:${licenses.document}` : page} className={`decky-sideloader-layout${page === "home" ? " gss-home" : ""}`} autoFocus flow-children="column" navEntryPreferPosition={focusReturn || settingsFocus || networkFocus || diagnosticsFocus || releaseNotesFocus || licensesFocus || page === "licenses" && licenses.focus ? NavEntryPositionPreferences.PREFERRED_CHILD : NavEntryPositionPreferences.FIRST}
    onGamepadFocus={() => { if (focusReturn || settingsFocus || networkFocus || diagnosticsFocus || releaseNotesFocus || licensesFocus) updateViewState((current) => ({ ...current, focusReturn: null, settingsFocus: false, networkFocus: false, diagnosticsFocus: false, releaseNotesFocus: false, licensesFocus: false })); }} onCancel={(event) => {
    if (page !== "home") { event.preventDefault(); event.stopPropagation(); recordUIAction("back"); back(); }
  }} onCancelActionDescription={page === "home" ? t("返回") : backLabel}>
    <style>{presentationStyles}</style>
    <div className="decky-sideloader-body">
    <ScrollPanel>
      {page !== "home" && <div className="gss-back"><Action appearance="inline" kind="back" onClick={back}>{backLabel}</Action></div>}
      {page !== "home" && taskNotice}
      {page !== "operation" && operationError && <PanelSection><Status tone="error" title={t("安装状态读取失败")}>{operationError}</Status><Action kind="refreshTask" onClick={() => void refreshOperation()}>{t("重新读取任务")}</Action></PanelSection>}
      {page === "home" && <Home notice={taskNotice} selected={focusReturn === "home" && plugin ? pluginKey(plugin) : null} onOpen={openPlugin} actions={<Focusable className="gss-home-actions" flow-children="row">
        <DialogButton data-action-key="newInstall" aria-label={t("从 GitHub 安装")} onClick={() => { recordUIAction("newInstall"); navigate("install"); }}><FaDownload aria-hidden focusable="false" />{t("从 GitHub 安装")}</DialogButton>
        <DialogButton data-action-key="settings" aria-label={t("设置")} onClick={() => { recordUIAction("settings"); navigate("settings"); }}><FaCog aria-hidden focusable="false" />{t("设置")}</DialogButton>
      </Focusable>} />}
      {page === "source" && <StorePlugins selected={focusReturn === "source" && plugin ? pluginKey(plugin) : null} onOpen={(selected) => updateViewState(current => ({ ...current, page: "details", plugin: selected, detailOrigin: "source", checkItem: null }))} />}
      {(["details", "maintenance", "versions"] as Page[]).includes(page) && plugin && <InstalledTab section={page === "maintenance" ? "maintenance" : page === "versions" ? "versions" : "overview"} plugin={plugin} checkRecord={detailOrigin === "checks" && checkItem && checkReport ? { item: checkItem, checkedAt: checkReport.checkedAt } : undefined}
        onVersions={() => navigate("versions")} onVersionSelected={() => navigate("details")} onMaintenance={() => navigate("maintenance")} onNetwork={() => navigate("network")} onBinding={() => navigate("binding")} onOwnershipChanged={() => void bindingSaved()} onOperation={() => navigate("operation")} />}
      {page === "details" && !plugin && checkItem && checkReport && <><Status tone="warning" title={t("历史检查记录")}>{t("该记录未匹配当前插件，仅供查看。")}</Status><CheckRecord item={checkItem} checkedAt={checkReport.checkedAt} historical /></>}
      {page === "binding" && plugin && !pluginReadOnly(plugin) && <BindingEditor plugin={plugin} onSaved={() => void bindingSaved()} />}
      {page === "install" && <InstallTab onExisting={(selected) => updateViewState(current => ({ ...current, page: "details", plugin: selected, detailOrigin: "install", checkItem: null }))} onOperation={() => navigate("operation")} />}
      {page === "settings" && <SettingsTab onLicenses={() => navigate("licenses")} focusLicenses={licensesFocus} onStore={() => navigate("source")} onTask={operation ? () => navigate("operation") : undefined} onDiagnostics={() => navigate("diagnostics")} focusDiagnostics={diagnosticsFocus} onNetwork={() => navigate("network")} focusNetwork={networkFocus} focusResults={settingsFocus} report={settingsFocus ? checkReport : null} onResults={(report) => updateViewState((current) => ({ ...current, page: "checks", checkReport: report, checkSelection: null }))} />}
      {page === "releaseNotes" && releaseNotes && <ReleaseNotesPage release={releaseNotes.release} />}
      {page === "diagnostics" && <DiagnosticsSettings />}
      {page === "licenses" && <LicensesPage />}
      {page === "network" && <NetworkSettings />}
      {page === "checks" && checkReport && <CheckResults report={checkReport} selected={focusReturn === "checks" ? checkSelection : null} onOpen={openCheck} />}
      {page === "operation" && <OperationPanel />}
    </ScrollPanel>
    </div>
  </Focusable>;
}

export default definePlugin(() => {
  const stopNativeManagement = startNativeManagement();
  const stopDiagnostics = startDiagnostics({ record: recordDiagnosticEvents, export: exportDiagnostics, status: getDiagnosticsStatus,
    sharing: { addresses: getDiagnosticsAddresses, status: getDiagnosticsShare, start: startDiagnosticsShare, stop: stopDiagnosticsShare } });
  const stopInstallController = startInstallController();
  const listener = addEventListener<[event: ScheduledCheckEvent]>("scheduled_check_completed", (event) => {
    if (receiveScheduledCheck(event)) toastInfo(checkReportSummary(event.report));
  });
  return {
    name: "decky-sideloader",
    titleView: <div className={staticClasses.Title}>decky-sideloader</div>,
    content: <DiagnosticBoundary><Content /></DiagnosticBoundary>,
    icon: <FaGithub />,
    onDismount() { stopNativeManagement(); disposeReleaseReads(); stopOwnershipController(); stopDiagnostics(); stopInstallController(); removeEventListener("scheduled_check_completed", listener); },
  };
});
