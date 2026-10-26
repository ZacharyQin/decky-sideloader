import { t } from "./i18n";
import { useEffect, useMemo, useRef, useState } from "react";
import { DropdownItem, Field, PanelSectionRow } from "@decky/ui";
import qrcode from "qrcode-generator";
import { Action, Explanation, Heading, PanelSection, Status } from "./components";
import { diagnosticAddressSelector, refreshDiagnosticShare, requestDiagnosticShare, stopDiagnosticShare, useDiagnosticShare } from "./diagnosticsController";

export function diagnosticSize(bytes: number): string {
  return bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(1)} KiB` : `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
}

function LocalQR({ url }: { url: string }) {
  const qr = useMemo(() => {
    try {
      const value = qrcode(0, "M");
      value.addData(url, "Byte"); value.make();
      const size = value.getModuleCount();
      let path = "";
      for (let row = 0; row < size; row++) for (let col = 0; col < size; col++) {
        if (value.isDark(row, col)) path += `M${col + 4} ${row + 4}h1v1h-1z`;
      }
      return { path, size: size + 8 };
    } catch { return null; }
  }, [url]);
  // Decky has no QR control. A decorative, non-focusable SVG renders the local matrix;
  // black/white and four-module quiet zone are barcode contrast, not UI theme colors.
  return qr ? <PanelSectionRow><Field focusable={false} childrenLayout="below">
    <svg aria-hidden="true" focusable="false" viewBox={`0 0 ${qr.size} ${qr.size}`} width="100%" style={{ width: "100%", height: "auto", maxWidth: "18em", display: "block" }} shapeRendering="crispEdges">
      <rect width={qr.size} height={qr.size} fill="white" /><path d={qr.path} fill="black" />
    </svg>
  </Field></PanelSectionRow> : <Explanation>{t("二维码生成失败，可复制下方链接下载。")}</Explanation>;
}

export function DiagnosticsSettings() {
  const share = useDiagnosticShare();
  const status = share.status;
  const selectAddress = diagnosticAddressSelector();
  const [details, setDetails] = useState(false);
  const [copy, setCopy] = useState<string | null>(null);
  const address = share.selectedAddress ?? "";
  const busy = status.state === "preparing" || status.state === "draining";
  const active = status.state === "active";
  const link = active && !share.error ? status.url : undefined;
  const copyRevision = useRef(0);
  useEffect(() => { setCopy(null); setDetails(false); }, [status.session, status.state]);
  useEffect(() => { ++copyRevision.current; return () => { ++copyRevision.current; }; }, [link]);
  const copyLink = async () => {
    if (!link) return;
    const revision = ++copyRevision.current;
    try {
      if (!navigator.clipboard || typeof navigator.clipboard.writeText !== "function") throw new Error();
      await navigator.clipboard.writeText(link);
      if (revision === copyRevision.current) setCopy(t("链接已复制"));
    } catch { if (revision === copyRevision.current) setCopy(t("复制失败，请扫描二维码或手动复制链接")); }
  };
  return <PanelSection><Heading title={t("问题反馈 / 诊断日志")} />
    <Explanation>{t("日志最多 4 MiB，每包最多 6 MiB，保留最近 3 包。插件不会自动上传，请自行附到问题反馈。")}</Explanation>
    <Explanation>{t("手机或电脑需在同一可信局域网。下载使用未加密 HTTP，持有链接的人可下载当前包。5 分钟或完成 3 次传输后停止，可随时停止。")}</Explanation>
    {!active && !busy && share.addresses.length > 1 && <PanelSectionRow><DropdownItem label={t("下载局域网地址")} rgOptions={share.addresses.map((value) => ({ data: value, label: value }))}
      selectedOption={address} strDefaultLabel={t("选择局域网地址")} disabled={share.loading || !share.addresses.length}
      onChange={(option) => selectAddress(option.data)} /></PanelSectionRow>}
    {!active && !busy && share.addresses.length === 1 && <PanelSectionRow><Field className={!address ? "gss-row gss-action-row" : "gss-status"} label={t("局域网地址：{0}", [share.addresses[0]])}
      description={!address ? t("点击使用此地址") : undefined} focusable={!address && !share.loading && !share.error} highlightOnFocus={!address}
      onOKActionDescription={!address ? t("使用此地址") : undefined}
      onActivate={!address ? () => selectAddress(share.addresses[0]) : undefined}
      onClick={!address ? () => selectAddress(share.addresses[0]) : undefined} bottomSeparator="none" /></PanelSectionRow>}
    {!active && !busy && !share.loading && share.addresses.length === 0 && <Explanation>{t("没有可用的私有 IPv4 局域网地址，请连接可信局域网后刷新。")}</Explanation>}
    {!active && status.state !== "draining" && <Action kind="shareDiagnostics" loading={status.state === "preparing"}
      disabled={busy || share.loading || Boolean(share.error) || !share.addresses.includes(address)} onClick={() => { if (!busy) void requestDiagnosticShare(address); }}>
      {status.state === "preparing" ? t("正在准备诊断包…") : t("生成下载二维码")}
    </Action>}
    {!busy && !active && <Action kind="reloadSettings" secondary disabled={share.loading} onClick={() => { void refreshDiagnosticShare(); }}>{t("刷新状态")}</Action>}
    {status.package && <Status title={t("诊断包已准备")}>{diagnosticSize(status.package.bytes)}</Status>}
    {link && <>
      <Explanation>{t("用手机或电脑扫描二维码下载当前诊断包。")}</Explanation>
      <LocalQR url={link} />
      <Explanation><span style={{ overflowWrap: "anywhere" }}>{link}</span></Explanation>
      <Explanation>{t("剩余")} {status.remaining}  {t("秒，已完成")} {status.downloads}  {t("/ 3 次服务器传输。传输完成不代表文件已保存或反馈已发送。")}</Explanation>
      <Action kind="copyDiagnostics" onClick={() => { void copyLink(); }}>{t("复制下载链接")}</Action>
      {copy && <Explanation>{copy}</Explanation>}
    </>}
    {status.session && (active || busy || Boolean(share.error)) && <Action kind="stopDiagnostics" secondary loading={status.state === "draining"}
      disabled={status.state === "draining"} onClick={() => { void stopDiagnosticShare(); }}>{t("停止下载服务")}</Action>}
    {status.state === "expired" && <Status title={t("下载已过期")}>{t("可重新生成下载二维码。")}</Status>}
    {status.state === "exhausted" && <Status title={t("本次下载已结束")}>{t("服务器已完成 3 次传输，可重新生成二维码。")}</Status>}
    {status.state === "stopped" && <Status title={t("下载已停止")} />}
    {(share.error || status.error) && <Status tone="error" title={t("诊断下载未确认")}>{share.error || status.error}</Status>}
    {status.package && <>
      <Action kind="diagnosticDetails" secondary onClick={() => setDetails(!details)}>{details ? t("收起文件详情") : t("文件详情")}</Action>
      {details && <Explanation><span style={{ overflowWrap: "anywhere" }}>{status.package.file}<br />{status.package.path}</span></Explanation>}
    </>}
  </PanelSection>;
}
