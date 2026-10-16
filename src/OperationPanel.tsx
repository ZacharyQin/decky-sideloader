// Semantic index:
// - participant: docs/upstream-conflict/sideload/plugin-management.md -- render actual progress, phase-qualified cancellation and retained backup evidence without invented success.
import { Field, PanelSection, PanelSectionRow, ProgressBar } from "@decky/ui";
import { Action, Status } from "./components";
import { cancelCurrentOperation, cancellationReason, dispatchPrepared, isDispatchBlocked, finishTracking, refreshOperation, useInstallation } from "./installController";
import type { OperationStage } from "./types";

export const stageLabel: Record<OperationStage, string> = {
  uninstalling: "正在卸载，等待目录回查", checking: "正在下载并检查安装包", backup: "正在备份数据", ready: "准备完成", validating: "正在复核安装条件",
  awaiting: "等待 Decky 确认或安装结果", installing: "Decky 正在安装", verified: "安装文件已核验，正在保存结果",
  failed: "准备失败，未提交安装", unknown: "安装结果尚未确认", cancelled: "任务已取消", closed: "跟踪已结束",
};
export function OperationPanel() {
  const { operation: op, error, cancelling } = useInstallation();
  if (!op) return <PanelSection><Status title="当前没有安装任务" />{error && <Status tone="error" title="读取失败">{error}</Status>}<Action kind="refreshTask" onClick={() => void refreshOperation()}>重新读取</Action></PanelSection>;
  const cancelReason = cancellationReason(op);
  const download = op.download;
  const downloading = op.stage === "checking" && !!download && ["connecting", "downloading"].includes(download.status);
  const size = download ? `${(download.received / 1024 / 1024).toFixed(2)} MiB${download.total !== null ? ` / ${(download.total / 1024 / 1024).toFixed(2)} MiB` : "，总量未知"}` : "";
  const downloadLabel = download ? { connecting: "正在连接下载", downloading: "正在下载", complete: op.stage === "verified" || op.outcome === "success" ? "下载完成" : "下载完成，安装仍须核验", failed: "下载已停止" }[download.status] : "";
  return <PanelSection title={op.package?.name ?? op.existing_name ?? op.repo}>
    <Status tone={op.outcome === "success" || op.stage === "ready" ? "success" : op.stage === "failed" ? "error" : op.stage === "unknown" || op.stage === "cancelled" ? "warning" : op.stage === "closed" ? undefined : "loading"} title={op.outcome === "success" ? "安装已核验，任务已自动结束" : stageLabel[op.stage]}>目标 Release {op.tag}{op.package && `，包内版本 ${op.package.version}`}</Status>
    {download && <>
      {(downloading || download.total !== null) && <PanelSectionRow><Field
        label={downloadLabel} focusable={false} childrenLayout="below" spacingBetweenLabelAndChild="none"
        description={<>{`${size}${download.total !== null ? `，${download.total ? (download.received / download.total * 100).toFixed(1) : "0.0"}%` : ""}`}<br />{`${download.route === "direct" ? "直连" : "GH-Proxy"}，第 ${download.attempt} 次尝试`}</>}>
        <ProgressBar focusable={false} indeterminate={downloading && download.total === null}
          nProgress={download.total ? Math.min(100, download.received / download.total * 100) : 0} nTransitionSec={0.2} />
      </Field></PanelSectionRow>}
      {!downloading && download.total === null && <Status title={downloadLabel}>{size}。{download.route === "direct" ? "直连" : "GH-Proxy"}，第 {download.attempt} 次尝试。</Status>}
    </>}
    {op.backup && <>
      <Status title={`已备份 ${op.backup.files} 个文件`}>{new Date(op.backup.created_at).toLocaleString()}，{(op.backup.bytes / 1024 / 1024).toFixed(1)} MiB</Status>
      <Status title="备份范围">{op.backup.paths.length ? op.backup.paths.join("\n") : "标准设置与数据目录中未发现数据"}</Status>
      <Status title="备份限制">{op.backup.scope}</Status>
      <Status title="备份位置"><span style={{ overflowWrap: "anywhere" }}>{op.backup.archive}</span></Status>
    </>}
    {op.package?.warnings?.map((warning) => <Status tone="warning" key={warning} title="安装前请核对">{warning}</Status>)}
    {op.stage === "ready" && <Action kind="confirmInstall" disabled={isDispatchBlocked(op.id) || !!op.cancel_requested} onClick={() => void dispatchPrepared(undefined, op.id)}>{op.mode === "reinstall" ? "继续到卸载确认" : "继续到 Decky 安装确认"}</Action>}
    {op.stage === "ready" && isDispatchBlocked(op.id) && !op.cancel_requested && !cancelling && <Status title="已停止自动提交，可重试取消或重新核对结果" />}
    {op.stage === "awaiting" && <Status title="请在 Decky 原生窗口确认">请求已提交，尚无安装完成证据。关闭本面板不会取消安装。</Status>}
    {op.outcome === "success" && <Status tone="success" title="回查结果">收到 Loader 完成事件，安装文件与所选包一致，GitHub 关联已保存。插件加载与运行功能仍需在 Decky 中检查。</Status>}
    {op.stage === "verified" && <Status title="正在保存关联与成功结果">收尾尚未完成，会重试保存，不会重复安装。</Status>}
    {op.mode === "reinstall" && <Status title="本次模式：卸载并安装">仅确认卸载完成且程序目录消失后，才打开安装确认。取消、失败或重载不自动继续。</Status>}
    {op.stage === "cancelled" && <Status title="请求已取消">未继续安装。若仍有 Decky 原生确认窗口，请关闭。</Status>}
    {op.error && <Status title="任务说明">{op.error}</Status>}
    {error && <Status tone="error" title="通信错误">{error}</Status>}
    {["awaiting", "installing", "unknown", "verified", "uninstalling"].includes(op.stage) && <Action kind="refreshTask" onClick={() => void refreshOperation()}>重新核对结果</Action>}
    {!["failed", "cancelled", "closed"].includes(op.stage) && <>
      <Action kind="cancelRequest" loading={cancelling} disabled={!!cancelReason} onClick={() => void cancelCurrentOperation(op.id)}>{op.stage === "uninstalling" ? "停止后续安装" : op.stage === "awaiting" ? "取消待确认请求" : "取消任务"}</Action>
      {cancelReason && <Status title={cancelReason} />}
    </>}
    {op.cancel_requested && op.uninstall_started && op.stage === "uninstalling" && <Status title="停止后续安装，等待Decky卸载结果" /> }
    {op.stage === "unknown" && !op.cancel_requested && !op.uninstall_blocked && <>
      <Status tone="warning" title="结束跟踪不会中止 Loader 安装">先关闭原生确认框，并确认 Decky 中的任务已处理。不要在安装仍进行时再次安装。</Status>
      <Action kind="finishTracking" onClick={() => void finishTracking(op.id)}>我已处理 Decky 任务，结束跟踪</Action>
    </>}
    {["failed", "cancelled", "ready"].includes(op.stage) && <Action kind="finishTracking" onClick={() => void finishTracking(op.id)}>{op.stage === "ready" ? "放弃本次准备" : "结束本次任务"}</Action>}
  </PanelSection>;
}
