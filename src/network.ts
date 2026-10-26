import { t } from "./i18n";
import type { NetworkMode, NetworkRoute } from "./types";

export const networkRouteLabels: Record<NetworkRoute, string> = {
  get direct() { return t("直连"); }, "gh-proxy": "GH-Proxy", ghfast: "GHFast",
};
export const networkModes: Record<NetworkMode, string> = {
  get direct() { return t("仅直连"); }, get auto() { return t("自动备用"); }, get "gh-proxy"() { return t("仅 GH-Proxy"); }, get ghfast() { return t("GHFast（文件加速）"); },
};
export const networkModeDescriptions: Record<NetworkMode, string> = {
  get direct() { return t("所有 GitHub 请求直连。"); },
  get auto() { return t("文件：直连 → GH-Proxy → GHFast；API：直连 → GH-Proxy。仅网络故障或限流时切换。"); },
  get "gh-proxy"() { return t("所有 GitHub 请求使用 gh-proxy.org。"); },
  get ghfast() { return t("文件使用 ghfast.top；GHFast 不支持 API，仓库搜索和版本列表使用直连，失败后备用 GH-Proxy。"); },
};
