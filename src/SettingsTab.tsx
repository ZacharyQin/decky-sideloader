import { t } from "./i18n";
import { DropdownItem, Field, PanelSectionRow, ToggleField } from "@decky/ui";
import { FaChevronRight, FaClock, FaFileAlt, FaGlobe } from "react-icons/fa";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { deleteBackups, getAppSettings, setAutoCheck, setCheckNotifications, setDeletePackageAfterInstall, setShowHomeTaskNotice } from "./api";
import { Action, Heading, PanelSection, Status } from "./components";
import { errorText } from "./errors";
import { confirmScheduledNotifications, refreshPlugins, suppressScheduledNotifications, useChecks } from "./checkController";
import { compactCheckSummary, latestScheduledReport, shortCheckTime } from "./viewModel";
import { networkSummary } from "./NetworkSettings";
import { LanguagePicker } from "./LanguagePicker";
import { confirmDeleteBackups } from "./loaderBridge";
import { refreshOperation } from "./installController";
import type { AppSettings, BackupCleanup, CheckReport } from "./types";
import { RepositoryMapSettings } from "./RepositoryMapSettings";

let showHomeTaskNotice = false;
let showHomeTaskNoticeRevision = 0;
const showHomeTaskNoticeListeners = new Set<() => void>();
function publishShowHomeTaskNotice(enabled: boolean) {
  if (showHomeTaskNotice === enabled) return;
  showHomeTaskNotice = enabled;
  for (const listener of showHomeTaskNoticeListeners) listener();
}
export function resetShowHomeTaskNotice() {
  showHomeTaskNoticeRevision += 1;
  publishShowHomeTaskNotice(false);
}
export function applyShowHomeTaskNotice(enabled: boolean) {
  showHomeTaskNoticeRevision += 1;
  publishShowHomeTaskNotice(enabled);
}
export function loadShowHomeTaskNotice() {
  const ticket = ++showHomeTaskNoticeRevision;
  return getAppSettings().then((value) => {
    if (ticket === showHomeTaskNoticeRevision) publishShowHomeTaskNotice(value.showHomeTaskNotice !== false);
  }).catch(() => {});
}
export async function saveShowHomeTaskNotice(enabled: boolean) {
  ++showHomeTaskNoticeRevision;
  const saved = await setShowHomeTaskNotice(enabled);
  applyShowHomeTaskNotice(saved.showHomeTaskNotice !== false);
  return saved;
}
export function useShowHomeTaskNotice() {
  return useSyncExternalStore((listener) => {
    showHomeTaskNoticeListeners.add(listener);
    return () => { showHomeTaskNoticeListeners.delete(listener); };
  }, () => showHomeTaskNotice);
}

export function SettingsTab({ onResults = () => {}, focusResults = false, report: fallback, onNetwork = () => {}, focusNetwork = false, onDiagnostics = () => {}, focusDiagnostics = false, onLicenses, focusLicenses = false, onTask, onStore }: { onLicenses?: () => void; focusLicenses?: boolean; onTask?: () => void; onStore?: () => void; onDiagnostics?: () => void; focusDiagnostics?: boolean; onNetwork?: () => void; focusNetwork?: boolean; onResults?: (report: CheckReport) => void; focusResults?: boolean; report?: CheckReport | null }) {
  const { scheduledReport, checking, refreshing, listError } = useChecks();
  const homeTaskNotice = useShowHomeTaskNotice();
  const saving = useRef(false);
  const request = useRef(0);
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [cleanup, setCleanup] = useState<BackupCleanup | null>(null);
  const load = () => {
    const id = ++request.current;
    const noticeRevision = showHomeTaskNoticeRevision;
    void getAppSettings().then((value) => {
      if (id === request.current && !saving.current) {
        setSettings(value);
        if (noticeRevision === showHomeTaskNoticeRevision) applyShowHomeTaskNotice(value.showHomeTaskNotice !== false);
      }
    })
      .catch((err) => { if (id === request.current) setError(errorText(err)); });
  };
  useEffect(load, []);
  const save = async (enabled: boolean, minutes: number) => {
    saving.current = true; ++request.current; setBusy(true); setError(null);
    try { setSettings(await setAutoCheck(enabled, minutes)); }
    catch (err) { setError(errorText(err)); }
    finally { saving.current = false; setBusy(false); }
  };
  const saveNotifications = async (enabled: boolean) => {
    saving.current = true; ++request.current; setBusy(true); setError(null);
    suppressScheduledNotifications(!enabled);
    try {
      const saved = await setCheckNotifications(enabled);
      confirmScheduledNotifications(saved.checkNotifications ?? true);
      setSettings(saved);
    }
    catch (err) { setError(errorText(err)); }
    finally { suppressScheduledNotifications(false); saving.current = false; setBusy(false); }
  };
  const saveHomeTaskNotice = async (enabled: boolean) => {
    saving.current = true; ++request.current; setBusy(true); setError(null);
    try {
      setSettings(await saveShowHomeTaskNotice(enabled));
    }
    catch (err) { setError(errorText(err)); }
    finally { saving.current = false; setBusy(false); }
  };
  const saveDeletePackage = async (enabled: boolean) => {
    saving.current = true; ++request.current; setBusy(true); setError(null);
    try { setSettings(await setDeletePackageAfterInstall(enabled)); }
    catch (err) { setError(errorText(err)); }
    finally { saving.current = false; setBusy(false); }
  };
  const clearBackups = async () => {
    if (saving.current) return;
    saving.current = true; setBusy(true); setError(null); setCleanup(null);
    try {
      if (!await confirmDeleteBackups()) return;
      setCleanup(await deleteBackups());
      await refreshOperation();
    } catch (err) { setError(errorText(err)); }
    finally { saving.current = false; setBusy(false); }
  };
  const scheduled = latestScheduledReport(settings?.lastScheduledCheck ?? fallback, scheduledReport);
  const openResults = () => { if (scheduled && !busy) onResults(scheduled); };
  return <div className="gss-form gss-settings"><Heading title={t("设置")} />
    <PanelSection title={t("更新")}>
    {settings && <>
      <PanelSectionRow><ToggleField bottomSeparator="none" label={t("定期检查更新")} description={t("按间隔检查并保存结果，安装由你确认")}
        checked={settings.autoCheck} disabled={busy} onChange={(v) => void save(v, settings.autoCheckMinutes)} /></PanelSectionRow>
      <PanelSectionRow><DropdownItem bottomSeparator="none" label={t("检查间隔")} selectedOption={settings.autoCheckMinutes} disabled={busy}
        rgOptions={[60, 360, 720, 1440].map((v) => ({ data: v, label: t("每 {0} 小时", [v / 60]) }))}
        onChange={(v) => void save(settings.autoCheck, v.data)} /></PanelSectionRow>
      <PanelSectionRow><ToggleField bottomSeparator="none" label={t("定时检查通知")} description={t("每轮完成后通知汇总，关闭后仍保存检查结果")}
        checked={settings.checkNotifications ?? true} disabled={busy} onChange={(v) => void saveNotifications(v)} /></PanelSectionRow>
    </>}
    </PanelSection><PanelSection title={t("安装与任务")}>
    {settings && <>
      <PanelSectionRow><ToggleField bottomSeparator="none" label={t("显示任务提示")} description={t("关闭后不显示任务横栏，仍可在下方查看最近安装任务")}
        checked={homeTaskNotice} disabled={busy} onChange={(v) => void saveHomeTaskNotice(v)} /></PanelSectionRow>
      <PanelSectionRow><ToggleField bottomSeparator="none" label={t("安装完成后删除安装包")} description={t("只删除本次下载的安装包，不删除数据备份")}
        checked={settings.deletePackageAfterInstall !== false} disabled={busy} onChange={(v) => void saveDeletePackage(v)} /></PanelSectionRow>
    </>}
    <Action secondary kind="deleteBackups" appearance="danger" disabled={busy} onClick={() => void clearBackups()}
      description={t("备份超过 7 天自动删除；活动任务的备份暂不清理")}>{t("清理数据备份")}</Action>
    {cleanup && <Status tone={cleanup.failed ? "warning" : "success"} title={t("已删除 {0} 份备份，释放 {1} MiB", [cleanup.deleted, (cleanup.bytes / 1024 / 1024).toFixed(1)])}>
      {t("跳过 {0} 项，删除失败 {1} 项", [cleanup.skipped, cleanup.failed])}
    </Status>}
    </PanelSection>
    <RepositoryMapSettings disabled={busy} />
    <PanelSection title={t("连接与管理")}>
    <PanelSectionRow><Field className="gss-row gss-action-row" bottomSeparator="none" data-action-key="checkResults" label={t("最近定时检查")}
      description={scheduled ? `${shortCheckTime(scheduled.checkedAt)}，${compactCheckSummary(scheduled)}` : t("尚未定时检查")}
      icon={<FaClock aria-hidden focusable="false" />} childrenContainerWidth="min" inlineWrap="keep-inline"
      focusable={!!scheduled && !busy} highlightOnFocus preferredFocus={focusResults} disabled={!scheduled || busy}
      onOKActionDescription={t("查看详情")} onActivate={openResults} onClick={openResults}>
      {scheduled && <FaChevronRight className="gss-chevron" aria-hidden focusable="false" />}
    </Field></PanelSectionRow>
    <PanelSectionRow><Field className="gss-row gss-action-row" data-action-key="network" label={t("网络连接")} description={settings ? networkSummary(settings) : t("读取连接方式…")}
      icon={<FaGlobe aria-hidden focusable="false" />} childrenContainerWidth="min" inlineWrap="keep-inline"
      focusable={!busy} highlightOnFocus preferredFocus={focusNetwork} disabled={busy} bottomSeparator="none"
      onOKActionDescription={t("查看网络设置")} onActivate={() => { if (!busy) onNetwork(); }} onClick={() => { if (!busy) onNetwork(); }}>
      <FaChevronRight className="gss-chevron" aria-hidden focusable="false" />
    </Field></PanelSectionRow>
    {onStore && <Action secondary kind="takeover" description={t("默认仍由商店负责更新")} onClick={onStore}>{t("商店插件")}</Action>}
    <Action secondary kind="refreshRecognition" loading={refreshing} disabled={busy || checking || refreshing} onClick={() => void refreshPlugins(true)}>{t("识别结果不对？试试刷新")}</Action>
    {listError && <Status tone="error" title={t("刷新失败")}>{listError}</Status>}
    {!settings && !error && <Status tone="loading" title={t("正在读取设置…")} />}
    {error && <><Status tone="error" title={t("设置操作失败")}>{error}</Status><Action kind="reloadSettings" onClick={load}>{t("重试")}</Action></>}
    {onTask && <Action kind="viewTask" secondary onClick={onTask}>{t("查看最近安装任务")}</Action>}
    <PanelSectionRow><Field className="gss-row gss-action-row" data-action-key="diagnostics" label={t("问题反馈 / 诊断日志")} description={t("生成下载二维码")}
      icon={<span className="gss-row-icon"><FaFileAlt aria-hidden focusable="false" /></span>} bottomSeparator="none" preferredFocus={focusDiagnostics}
      childrenContainerWidth="min" inlineWrap="keep-inline" focusable highlightOnFocus
      onOKActionDescription={t("打开诊断日志")} onActivate={onDiagnostics} onClick={onDiagnostics}>
      <FaChevronRight className="gss-chevron" aria-hidden focusable="false" />
    </Field></PanelSectionRow>
    {onLicenses && <Action secondary kind="licenses" preferredFocus={focusLicenses} onClick={onLicenses}>{t("开源许可")}</Action>}
  </PanelSection>
  <LanguagePicker />
  </div>;
}
