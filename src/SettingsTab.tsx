// Semantic index:
// - participant: docs/upstream-conflict/sideload/plugin-management.md -- keep compact scheduled-result and retained task entries without changing notification saves.
// - participant: docs/upstream-conflict/sideload/diagnostics-sharing.md -- enter the download QR inner page only on explicit activation.
// - participant: docs/upstream-conflict/sideload/diagnostics.md -- expose one diagnostics inner-page entry without changing preferences.
import { DropdownItem, Field, PanelSection, PanelSectionRow, ToggleField } from "@decky/ui";
import { FaChevronRight, FaClock, FaGlobe } from "react-icons/fa";
import { useEffect, useRef, useState } from "react";
import { getAppSettings, setAutoCheck, setCheckNotifications } from "./api";
import { Action, errorText, Status } from "./components";
import { confirmScheduledNotifications, suppressScheduledNotifications, useChecks } from "./checkController";
import { compactCheckSummary, latestScheduledReport, shortCheckTime } from "./viewModel";
import { networkSummary } from "./NetworkSettings";
import type { AppSettings, CheckReport } from "./types";

export function SettingsTab({ onResults = () => {}, focusResults = false, report: fallback, onNetwork = () => {}, focusNetwork = false, onDiagnostics = () => {}, focusDiagnostics = false, onTask }: { onTask?: () => void; onDiagnostics?: () => void; focusDiagnostics?: boolean; onNetwork?: () => void; focusNetwork?: boolean; onResults?: (report: CheckReport) => void; focusResults?: boolean; report?: CheckReport | null }) {
  const { scheduledReport } = useChecks();
  const saving = useRef(false);
  const request = useRef(0);
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const load = () => {
    const id = ++request.current;
    void getAppSettings().then((value) => { if (id === request.current && !saving.current) setSettings(value); })
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
  const scheduled = latestScheduledReport(settings?.lastScheduledCheck ?? fallback, scheduledReport);
  const openResults = () => { if (scheduled && !busy) onResults(scheduled); };
  return <><PanelSection title="设置">
    {settings && <>
      <PanelSectionRow><ToggleField bottomSeparator="none" label="定期检查更新" description="按间隔检查并保存结果，安装由你确认"
        checked={settings.autoCheck} disabled={busy} onChange={(v) => void save(v, settings.autoCheckMinutes)} /></PanelSectionRow>
      <PanelSectionRow><DropdownItem bottomSeparator="none" label="检查间隔" selectedOption={settings.autoCheckMinutes} disabled={busy}
        rgOptions={[60, 360, 720, 1440].map((v) => ({ data: v, label: `每 ${v / 60} 小时` }))}
        onChange={(v) => void save(settings.autoCheck, v.data)} /></PanelSectionRow>
      <PanelSectionRow><ToggleField bottomSeparator="none" label="定时检查通知" description="每轮完成后通知汇总，关闭后仍保存检查结果"
        checked={settings.checkNotifications ?? true} disabled={busy} onChange={(v) => void saveNotifications(v)} /></PanelSectionRow>
    </>}
    </PanelSection><PanelSection title="检查与连接">
    <PanelSectionRow><Field bottomSeparator="none" data-action-key="checkResults" label="最近定时检查"
      description={scheduled ? `${shortCheckTime(scheduled.checkedAt)}，${compactCheckSummary(scheduled)}` : "尚未定时检查"}
      icon={<FaClock aria-hidden focusable="false" />} childrenContainerWidth="min" inlineWrap="keep-inline"
      focusable={!!scheduled && !busy} highlightOnFocus preferredFocus={focusResults} disabled={!scheduled || busy}
      onOKActionDescription="查看详情" onActivate={openResults} onClick={openResults}>
      {scheduled && <FaChevronRight aria-hidden focusable="false" />}
    </Field></PanelSectionRow>
    <PanelSectionRow><Field data-action-key="network" label="网络连接" description={settings ? networkSummary(settings) : "读取连接方式…"}
      icon={<FaGlobe aria-hidden focusable="false" />} childrenContainerWidth="min" inlineWrap="keep-inline"
      focusable={!busy} highlightOnFocus preferredFocus={focusNetwork} disabled={busy} bottomSeparator="none"
      onOKActionDescription="查看网络设置" onActivate={() => { if (!busy) onNetwork(); }} onClick={() => { if (!busy) onNetwork(); }}>
      <FaChevronRight aria-hidden focusable="false" />
    </Field></PanelSectionRow>
    {!settings && !error && <Status tone="loading" title="正在读取设置…" />}
    {error && <><Status tone="error" title="设置操作失败">{error}</Status><Action kind="reloadSettings" onClick={load}>重试</Action></>}
    {onTask && <Action kind="viewTask" secondary onClick={onTask}>查看最近安装任务</Action>}
    <PanelSectionRow><Field data-action-key="diagnostics" label="问题反馈 / 诊断日志"
      icon={<FaChevronRight aria-hidden focusable="false" />} bottomSeparator="none" preferredFocus={focusDiagnostics}
      focusable highlightOnFocus onOKActionDescription="打开诊断日志" onActivate={onDiagnostics} onClick={onDiagnostics} /></PanelSectionRow>
  </PanelSection></>;
}
