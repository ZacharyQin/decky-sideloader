import { t } from "./i18n";
import { Field, PanelSectionRow, ProgressBar } from "@decky/ui";
import { Action, Disclosure, Heading, PanelSection, Status } from "./components";
import { cancelCurrentOperation, cancellationReason, continueDownloaded, dispatchPrepared, finishTracking, isDispatchBlocked, refreshOperation, removeInstallPackage, retryFailedDownload, useInstallation } from "./installController";
import type { DependencyDownload, InstallOperation, OperationStage } from "./types";
import { networkRouteLabels } from "./network";
import { operationProgress } from "./viewModel";

export const stageLabel: Record<OperationStage, string> = {
  get uninstalling() { return t("正在卸载，等待目录回查"); }, get checking() { return t("正在下载并检查安装包"); }, get backup() { return t("正在备份数据"); }, get ready() { return t("准备完成"); }, get validating() { return t("正在复核安装条件"); },
  get awaiting() { return t("等待 Decky 确认或安装结果"); }, get installing() { return t("Decky 正在安装"); }, get verified() { return t("安装文件已核验，正在保存结果"); },
  get failed() { return t("安装任务失败"); }, get unknown() { return t("安装结果尚未确认"); }, get cancelled() { return t("任务已取消"); }, get closed() { return t("跟踪已结束"); },
};
function observationText(label: string): string {
  switch (label) {
    case "主安装包下载": case "安装包校验": case "额外依赖准备": case "数据备份":
    case "等待原生确认": case "Loader 安装": case "安装文件核验": case "保存安装结果": case "原生卸载":
    case "无额外依赖": case "读取本地安装包": case "卸载旧版": case "解压安装包": case "检查额外依赖": return t(label);
    default: return label;
  }
}

function OperationSteps({ operation: op }: { operation: InstallOperation }) {
  const { steps, current, observation } = operationProgress(op);
  const label = current < 0 ? stageLabel[op.stage] : `${current + 1} / ${steps.length} · ${op.outcome === "success" ? t("安装已完成") : t(steps[current][1])}`;
  const detail = observation?.detail && observation.detail !== op.error ? observationText(observation.detail) : undefined;
  const state = op.outcome === "success" ? "success" : op.stage === "failed" ? "error" : ["unknown", "cancelled", "closed"].includes(op.stage) ? "stopped" : "active";
  return <PanelSectionRow><Field className="gss-status gss-operation-steps" focusable={false} bottomSeparator="none"
    label={<><ol className="gss-step-dots" data-result={state} aria-label={t("安装步骤")}>
      {steps.map(([id, title], index) => <li key={id} aria-label={t(title)} aria-current={index === current ? "step" : undefined}
        data-step={id} data-state={index < current ? "past" : index === current ? "current" : "future"} />)}
    </ol><span className="gss-step-label" aria-live="polite">{label}</span></>}
    description={detail} />
  </PanelSectionRow>;
}

function DependencyProgress({ download }: { download: DependencyDownload }) {
  const downloading = download.status === "connecting" || download.status === "downloading";
  const title = t("额外依赖下载：{0}（{1}/{2}）", [download.name, download.index, download.count]);
  const status = { connecting: t("正在连接下载"), downloading: t("正在下载"), complete: t("下载完成"), failed: t("下载已停止") }[download.status];
  const size = download.total === null ? t("{0} 字节", [download.received]) + t("，总量未知") : t("{0} / {1} 字节", [download.received, download.total]);
  const detail = <>{status}<br />{size}<br />{t("{0}，第 {1} 次尝试", [networkRouteLabels[download.route], download.attempt])}</>;
  if (!downloading && download.total === null) return <Status title={title} tone={download.status === "failed" ? "error" : "success"}>{detail}</Status>;
  return <PanelSectionRow><Field className="gss-status" label={title} description={detail} focusable={false} childrenLayout="below" spacingBetweenLabelAndChild="none">
    <ProgressBar focusable={false} indeterminate={downloading && download.total === null}
      nProgress={download.total ? Math.min(100, download.received / download.total * 100) : 0} nTransitionSec={0.2} />
  </Field></PanelSectionRow>;
}
export function OperationPanel() {
  const { operation: op, error, preparing, cancelling } = useInstallation();
  if (preparing) return <PanelSection><Heading title={stageLabel.validating} /><Status tone="loading" title={t("正在准备…")} /></PanelSection>;
  if (!op) return <PanelSection><Status title={t("当前没有安装任务")} />{error && <Status tone="error" title={t("读取失败")}>{error}</Status>}<Action kind="refreshTask" onClick={() => void refreshOperation()}>{t("重新读取")}</Action></PanelSection>;
  const cancelReason = cancellationReason(op);
  const download = op.download;
  const downloading = op.stage === "checking" && !!download && ["connecting", "downloading"].includes(download.status);
  const size = download ? `${(download.received / 1024 / 1024).toFixed(2)} MiB${download.total !== null ? ` / ${(download.total / 1024 / 1024).toFixed(2)} MiB` : t("，总量未知")}` : "";
  const downloadLabel = download ? { connecting: t("正在连接下载"), downloading: t("正在下载"), complete: op.stage === "verified" || op.outcome === "success" ? t("下载完成") : t("下载完成，安装仍须核验"), failed: t("下载已停止") }[download.status] : "";
  return <PanelSection>
    <Heading title={op.outcome === "success" ? t("安装已完成") : stageLabel[op.stage]}>{op.package?.name ?? op.existing_name ?? op.repo}</Heading>
    <OperationSteps operation={op} />
    {op.error && <Status tone={op.stage === "failed" ? "error" : undefined} title={op.stage === "failed" ? t("失败原因") : t("任务说明")}>{op.error}</Status>}
    {error && <Status tone="error" title={t("通信错误")}>{error}</Status>}
    {op.outcome === "success" && <Status tone="success" title={t("回查结果")}>{t("收到 Loader 完成事件，安装文件与所选包一致")}{t("，GitHub 关联已保存。")}</Status>}
    <Status tone={op.outcome === "success" || op.stage === "ready" ? "success" : op.stage === "failed" ? "error" : op.stage === "unknown" || op.stage === "cancelled" ? "warning" : op.stage === "closed" ? undefined : "loading"} title={op.outcome === "success" ? t("安装已核验，任务已自动结束") : t("目标 Release")}>
      {op.tag}{op.package && t("，包内版本 {0}", [op.package.version])}
    </Status>
    {download && <>
      {(downloading || download.total !== null) && <PanelSectionRow><Field className="gss-status"
        label={`${t("主安装包下载")} · ${downloadLabel}`} focusable={false} childrenLayout="below" spacingBetweenLabelAndChild="none"
        description={<>{`${size}${download.total !== null ? `，${download.total ? (download.received / download.total * 100).toFixed(1) : "0.0"}%` : ""}`}<br />{t("{0}，第 {1} 次尝试", [networkRouteLabels[download.route], download.attempt])}</>}>
        <ProgressBar focusable={false} indeterminate={downloading && download.total === null}
          nProgress={download.total ? Math.min(100, download.received / download.total * 100) : 0} nTransitionSec={0.2} />
      </Field></PanelSectionRow>}
      {!downloading && download.total === null && <Status title={`${t("主安装包下载")} · ${downloadLabel}`}>{size}。{networkRouteLabels[download.route]}{t("，第")} {download.attempt}  {t("次尝试。")}</Status>}
    </>}
    {op.dependency_download && <DependencyProgress download={op.dependency_download} />}
    {op.backup && <Disclosure title={t("备份详情")}>
      <Status title={t("已备份 {0} 个文件", [op.backup.files])}>{new Date(op.backup.created_at).toLocaleString()}，{(op.backup.bytes / 1024 / 1024).toFixed(1)} MiB</Status>
      <Status title={t("备份范围")}>{op.backup.paths.length ? op.backup.paths.join("\n") : t("标准设置与数据目录中未发现数据")}</Status>
      <Status title={t("备份限制")}>{op.backup.scope}</Status>
      {op.backup_deleted ? <Status title={t("数据备份已删除")} /> : <Status title={t("备份位置")}><span style={{ overflowWrap: "anywhere" }}>{op.backup.archive}</span></Status>}
    </Disclosure>}
    {op.stage === "ready" && <Action kind="confirmInstall" disabled={isDispatchBlocked(op.id) || !!op.cancel_requested} onClick={() => void dispatchPrepared(undefined, op.id)}>{op.mode === "reinstall" ? t("继续到卸载确认") : t("继续到 Decky 安装确认")}</Action>}
    {op.stage === "ready" && isDispatchBlocked(op.id) && !op.cancel_requested && !cancelling && <Status title={t("已停止自动提交，可重试取消或重新核对结果")} />}
    {op.stage === "awaiting" && <Status title={t("请在 Decky 原生窗口确认")}>{t("请求已提交，尚无安装完成证据。关闭本面板不会取消安装。")}</Status>}
    {op.artifact_deleted && <Status title={t("安装包已删除")}>{op.backup && !op.backup_deleted ? t("数据备份仍保留。") : undefined}</Status>}
    {["failed", "cancelled", "closed"].includes(op.stage) && op.download?.status === "complete" && !op.artifact_deleted && <Action appearance="quiet" kind="deletePackage" onClick={() => void removeInstallPackage(op.id)}>{t("删除安装包")}</Action>}
    {op.stage === "failed" && op.download?.status === "complete" && !op.artifact_deleted && !op.request_id && !op.saw_start && <Action kind="continueInstall" onClick={() => void continueDownloaded(op.id)}>{t("用已下载的包继续安装")}</Action>}
    {op.stage === "failed" && op.download?.status === "failed" && <Action kind="retryDownload" onClick={() => void retryFailedDownload(op.id)}>{t("重试下载")}</Action>}
    {op.stage === "verified" && <Status title={t("正在保存关联与成功结果")}>{t("收尾尚未完成，会重试保存，不会重复安装。")}</Status>}
    {op.mode === "reinstall" && <Status title={t("本次模式：卸载并安装")}>{t("仅确认卸载完成且程序目录消失后，才打开安装确认。取消、失败或重载不自动继续。")}</Status>}
    {op.stage === "cancelled" && <Status title={t("请求已取消")}>{t("未继续安装。若仍有 Decky 原生确认窗口，请关闭。")}</Status>}
    {["awaiting", "installing", "unknown", "verified", "uninstalling"].includes(op.stage) && <Action kind="refreshTask" onClick={() => void refreshOperation()}>{t("重新核对结果")}</Action>}
    {!["failed", "cancelled", "closed"].includes(op.stage) && <>
      <Action appearance="quiet" kind="cancelRequest" loading={cancelling} disabled={!!cancelReason} onClick={() => void cancelCurrentOperation(op.id)}>{op.stage === "uninstalling" ? t("停止后续安装") : op.stage === "awaiting" ? t("取消待确认请求") : t("取消任务")}</Action>
      {cancelReason && <Status title={cancelReason} />}
    </>}
    {op.cancel_requested && op.uninstall_started && op.stage === "uninstalling" && <Status title={t("停止后续安装，等待Decky卸载结果")} /> }
    {op.stage === "unknown" && !op.cancel_requested && !op.uninstall_blocked && <>
      <Status tone="warning" title={t("结束跟踪不会中止 Loader 安装")}>{t("先关闭原生确认框，并确认 Decky 中的任务已处理。不要在安装仍进行时再次安装。")}</Status>
      <Action kind="finishTracking" onClick={() => void finishTracking(op.id)}>{t("我已处理 Decky 任务，结束跟踪")}</Action>
    </>}
    {["failed", "cancelled", "ready"].includes(op.stage) && <Action kind="finishTracking" onClick={() => void finishTracking(op.id)}>{op.stage === "ready" ? t("放弃本次准备") : t("结束本次任务")}</Action>}
  </PanelSection>;
}
