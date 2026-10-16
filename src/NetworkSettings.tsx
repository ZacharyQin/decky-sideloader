// Semantic index:
// - participant: docs/upstream-conflict/sideload/plugin-management.md -- show full proxy risk and retain network selection and request guards across QAM mounts.
import { DropdownItem, PanelSection, PanelSectionRow } from "@decky/ui";
import { useEffect } from "react";
import { getAppSettings, setNetworkMode } from "./api";
import { Action, errorText, Status } from "./components";
import { getViewState, updateViewState, useViewState } from "./viewState";
import type { AppSettings, NetworkMode } from "./types";

const modes = { direct: "仅直连", auto: "直连失败后使用 GH-Proxy", "gh-proxy": "仅 GH-Proxy" };
export function networkSummary(settings: AppSettings) {
  const mode = settings.networkMode === "auto" ? "自动备用" : modes[settings.networkMode ?? "direct"];
  return `${mode}，${settings.lastNetworkRoute ? `最近使用${settings.lastNetworkRoute.route === "direct" ? "直连" : "GH-Proxy"}` : "尚无实际通道记录"}`;
}
const updateNetwork = (patch: Partial<ReturnType<typeof getViewState>["network"]>) =>
  updateViewState((current) => ({ ...current, network: { ...current.network, ...patch } }));
function loadNetwork() {
  const current = getViewState().network;
  if (current.busy) return;
  const request = current.request + 1;
  updateNetwork({ request, error: null });
  void getAppSettings().then((settings) => {
    if (getViewState().network.request === request && !getViewState().network.busy)
      updateNetwork({ settings, selection: null });
  }).catch((error) => {
    if (getViewState().network.request === request) updateNetwork({ error: errorText(error) });
  });
}
async function saveNetwork(mode: NetworkMode) {
  if (getViewState().network.busy) return;
  updateNetwork({ busy: true, selection: mode, request: getViewState().network.request + 1, error: null });
  try { updateNetwork({ settings: await setNetworkMode(mode), selection: null }); }
  catch (error) { updateNetwork({ error: errorText(error), selection: null }); }
  finally { updateNetwork({ busy: false }); }
}
export function NetworkSettings() {
  const { network: { settings, selection, busy, error } } = useViewState();
  useEffect(loadNetwork, []);
  return <>
    <PanelSection title="网络连接">
      <Status tone="warning" title="GH-Proxy 是第三方服务">使用 https://gh-proxy.org/ 访问公开 GitHub 资源。第三方可见查询与下载内容，也可修改返回内容。TLS 和 ZIP 校验不保证作者或代码可信。不改变系统代理，进行中的任务保留开始时的模式。</Status>
      {settings && <PanelSectionRow><DropdownItem label="GitHub 连接方式" bottomSeparator="none"
        description={busy ? "正在保存连接方式…" : "默认仅直连，备用服务只访问公开 GitHub 资源"}
        selectedOption={selection ?? settings.networkMode ?? "direct"} disabled={busy}
        rgOptions={Object.entries(modes).map(([data, label]) => ({ data, label }))}
        onChange={(value) => void saveNetwork(value.data as NetworkMode)} /></PanelSectionRow>}
      {!settings && !error && <Status tone="loading" title="正在读取网络设置…" />}
      {error && <><Status tone="error" title="网络设置操作失败">{error}</Status><Action kind="reloadSettings" onClick={loadNetwork}>重试</Action></>}
    </PanelSection>
    <PanelSection title="通道记录">
      <Status title={settings?.lastNetworkRoute ? `最近实际查询通道：${settings.lastNetworkRoute.route === "direct" ? "直连" : "GH-Proxy"}` : "尚无实际查询通道记录"}>
        {settings?.lastNetworkRoute && `${new Date(settings.lastNetworkRoute.timestamp * 1000).toLocaleString()}。`}历史记录不保证当前连通，下载通道以任务记录为准。
      </Status>
    </PanelSection>
  </>;
}
