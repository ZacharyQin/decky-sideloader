import { t } from "./i18n";
import { DropdownItem, PanelSectionRow } from "@decky/ui";
import { useEffect } from "react";
import { getAppSettings, setDownloadTimeout, setNetworkMode } from "./api";
import { Action, Choice, Disclosure, Heading, PanelSection, Status } from "./components";
import { errorText } from "./errors";
import { getViewState, updateViewState, useViewState } from "./viewState";
import type { AppSettings, NetworkMode } from "./types";
import { networkModeDescriptions, networkModes, networkRouteLabels } from "./network";
import { confirmNetworkMode } from "./loaderBridge";

export function networkSummary(settings: AppSettings) {
  const mode = networkModes[settings.networkMode ?? "direct"];
  return `${mode}，${settings.lastNetworkRoute ? t("最近使用{0}", [networkRouteLabels[settings.lastNetworkRoute.route]]) : t("尚无实际通道记录")}`;
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
  const current = getViewState().network;
  if (current.busy || mode === (current.settings?.networkMode ?? "direct")) return;
  updateNetwork({ busy: true, selection: mode, request: current.request + 1, error: null });
  try {
    if (mode !== "direct" && !await confirmNetworkMode()) { updateNetwork({ selection: null }); return; }
    updateNetwork({ settings: await setNetworkMode(mode), selection: null });
  }
  catch (error) { updateNetwork({ error: errorText(error), selection: null }); }
  finally { updateNetwork({ busy: false }); }
}
async function saveTimeout(minutes: number) {
  if (getViewState().network.busy) return;
  updateNetwork({ busy: true, request: getViewState().network.request + 1, error: null });
  try { updateNetwork({ settings: await setDownloadTimeout(minutes) }); }
  catch (error) { updateNetwork({ error: errorText(error) }); }
  finally { updateNetwork({ busy: false }); }
}
export function NetworkSettings() {
  const { network: { settings, selection, busy, error } } = useViewState();
  useEffect(loadNetwork, []);
  return <>
    <PanelSection>
      <Heading title={t("网络连接")}>{t("选择访问 GitHub 的方式。")}</Heading>
      {settings && <div role="radiogroup" aria-label={t("GitHub 连接方式")}>
        {Object.entries(networkModes).map(([mode, title]) => <Choice key={mode} value={mode} title={title}
          description={networkModeDescriptions[mode as NetworkMode]} checked={(selection ?? settings.networkMode ?? "direct") === mode}
          disabled={busy} onSelect={() => void saveNetwork(mode as NetworkMode)} />)}
      </div>}
      {busy && <Status tone="loading" title={t("正在保存连接方式…")} />}
      <Disclosure title={t("第三方服务与隐私")}>{t("GH-Proxy：https://gh-proxy.org/；GHFast：https://ghfast.top/。第三方可见查询与下载内容，也可修改返回内容。TLS 和 ZIP 校验不保证作者或代码可信。不改变系统代理，进行中的任务保留开始时的模式。")}</Disclosure>
      {settings && <PanelSectionRow><DropdownItem label={t("单通道下载时限")} description={t("每条通道独立计时；自动模式超时后尝试下一条通道。")} selectedOption={settings.downloadTimeoutMinutes ?? 3} disabled={busy}
        rgOptions={[3, 5, 10, 15, 30].map(data => ({ data, label: t("{0} 分钟", [data]) }))} onChange={value => void saveTimeout(value.data)} /></PanelSectionRow>}
      {!settings && !error && <Status tone="loading" title={t("正在读取网络设置…")} />}
      {error && <><Status tone="error" title={t("网络设置操作失败")}>{error}</Status><Action kind="reloadSettings" onClick={loadNetwork}>{t("重试")}</Action></>}
    </PanelSection>
    <PanelSection title={t("通道记录")}>
      <Status title={settings?.lastNetworkRoute ? t("最近实际查询通道：{0}", [networkRouteLabels[settings.lastNetworkRoute.route]]) : t("尚无实际查询通道记录")}>
        {settings?.lastNetworkRoute && `${new Date(settings.lastNetworkRoute.timestamp * 1000).toLocaleString()}。`}{t("历史记录不保证当前连通，下载通道以任务记录为准。")}
      </Status>
    </PanelSection>
  </>;
}
