import { useEffect } from "react";
import { Field, PanelSectionRow, ProgressBar } from "@decky/ui";
import { Action, PanelSection, Status } from "./components";
import { useChecks } from "./checkController";
import { isActive, useInstallation } from "./installController";
import { cancelCurrentSelfUpdate, checkForSelfUpdate, downloadSelfUpdate, installPreparedSelfUpdate, refreshSelfUpdate, selfUpdateActive, useSelfUpdate } from "./selfUpdateController";
import { t } from "./i18n";
import { messages, type Message } from "./locales";
import { shortCheckTime } from "./viewModel";
import { updateViewState, useViewState } from "./viewState";
import type { SelfUpdateOperation } from "./types";

const message = (text: string) => Object.prototype.hasOwnProperty.call(messages, text) ? t(text as Message) : text;
const stageLabels: Record<SelfUpdateOperation["stage"], Message> = {
  downloading: "正在下载更新", ready: "准备完成", awaiting: "请在 Decky 原生窗口确认", installing: "Decky 正在安装",
  verifying: "正在核验新版", unknown: "安装结果尚未确认", completed: "Sideloader 更新已完成", cancelled: "任务已取消", failed: "自更新失败",
};

export function SelfUpdateSettings({ disabled = false }: { disabled?: boolean }) {
  const { status, busy, error } = useSelfUpdate();
  const { checking, refreshing } = useChecks();
  const { releaseNotesFocus } = useViewState();
  const { operation, preparing } = useInstallation();
  useEffect(() => { void refreshSelfUpdate(); }, []);
  const op = status?.operation;
  const release = status?.check?.release;
  const active = selfUpdateActive(op);
  const blocked = disabled || checking || refreshing || preparing || isActive(operation) || busy || !!status?.error;
  const failure = error || status?.error || op?.error || status?.check?.error;
  const progress = op?.progress;
  const cancellable = op && (["downloading", "ready"].includes(op.stage) || ["awaiting", "unknown"].includes(op.stage) && !!op.request_id && !op.saw_start);
  return <PanelSection title={t("Sideloader 更新")}>
    {status ? <Status title={t("当前版本：{0}", [status.current])}>
      {release && t("最新正式版：{0}", [release.version])}
    </Status> : !error && <Status tone="loading" title={t("正在读取更新状态…")} />}
    {status?.check && <Status title={t("最近检查：{0}", [shortCheckTime(status.check.checked_at)])}>
      {status.check.status === "current" ? t("已是最新正式版") : status.check.status === "ahead" ? t("当前版本高于最新正式版") : status.check.status === "update" ? t("有新的 Sideloader 版本") : undefined}
    </Status>}
    {op && <Status tone={op.stage === "completed" || op.stage === "ready" ? "success" : op.stage === "failed" ? "error" : op.stage === "unknown" || op.stage === "cancelled" ? "warning" : "loading"}
      title={t(stageLabels[op.stage])}>{op.release.tag}</Status>}
    {op?.stage === "downloading" && <PanelSectionRow><Field className="gss-status" label={t("正在下载更新")}
      description={progress ? `${(progress.received / 1024 / 1024).toFixed(2)} MiB${progress.total !== null ? ` / ${(progress.total / 1024 / 1024).toFixed(2)} MiB` : ""}` : undefined}
      focusable={false} childrenLayout="below" spacingBetweenLabelAndChild="none">
      <ProgressBar focusable={false} indeterminate={!progress?.total} nProgress={progress?.total ? Math.min(100, progress.received / progress.total * 100) : 0} nTransitionSec={0.2} />
    </Field></PanelSectionRow>}
    {failure && <Status tone={op?.stage === "unknown" ? "warning" : "error"} title={message(failure)} />}
    <Action secondary kind="selfUpdateCheck" loading={busy && !active || !!status?.checking} disabled={blocked || active}
      onClick={() => void checkForSelfUpdate()}>{t("检查 Sideloader 更新")}</Action>
    {release?.body.trim() && <Action secondary kind="releaseNotes" preferredFocus={releaseNotesFocus} onClick={() => updateViewState(current => ({
      ...current, page: "releaseNotes", releaseNotes: { release, origin: "settings" }, releaseNotesFocus: false,
    }))}>{t("查看完整发布说明")}</Action>}
    {status?.check?.status === "update" && !active && <Action kind="selfUpdateDownload" disabled={blocked || !status.frontend_compatible}
      onClick={() => void downloadSelfUpdate()}>{t("下载 Sideloader 更新")}</Action>}
    {op?.stage === "ready" && <Action kind="selfUpdateInstall" disabled={blocked || !status?.frontend_compatible || op.cancel_requested}
      onClick={() => void installPreparedSelfUpdate()}>{t("继续到 Decky 安装确认")}</Action>}
    {cancellable && <Action kind="selfUpdateCancel" appearance="quiet" disabled={busy || op.cancel_requested}
      onClick={() => void cancelCurrentSelfUpdate()}>{op.stage === "awaiting" || op.stage === "unknown" ? t("取消待确认请求") : t("取消任务")}</Action>}
    {active && <Action secondary kind="refreshTask" disabled={busy} onClick={() => void refreshSelfUpdate()}>{t("重新核对结果")}</Action>}
    {status && !status.frontend_compatible && <Status tone="warning" title={t("前后端版本不一致，请重新打开插件")} />}
  </PanelSection>;
}
