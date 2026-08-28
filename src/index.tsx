import { ButtonItem, PanelSection, PanelSectionRow, TextField, staticClasses } from "@decky/ui";
import { definePlugin, toaster } from "@decky/api";
import { useEffect, useState } from "react";
import { FaGithub } from "react-icons/fa";

import { listSideloadPlugins } from "./api";
import { installFromArtifact, probeLoaderChannel, uninstallPlugin } from "./loaderBridge";
import type { PluginListResponse, PluginRow } from "./types";

function pluginLabel(plugin: PluginRow): string {
  const version = plugin.version ?? "未知版本";
  const repo = plugin.repo ? ` · ${plugin.repo}` : " · 未绑定 GitHub";
  return `${version}${repo}`;
}

function Content() {
  const [list, setList] = useState<PluginListResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [installUrl, setInstallUrl] = useState("");
  const channel = probeLoaderChannel();

  const refresh = async () => {
    setLoading(true);
    setError(null);
    try {
      setList(await listSideloadPlugins());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const sideload = list?.plugins.filter((plugin) => plugin.sideload) ?? [];

  const onUninstall = (name: string) => {
    try {
      uninstallPlugin(name);
    } catch (err) {
      toaster.toast({
        title: "GitHub Sideload",
        body: err instanceof Error ? err.message : String(err),
      });
    }
  };

  const onInstallUrl = async () => {
    const raw = installUrl.trim();
    if (!raw) {
      return;
    }
    const artifact = /^https?:\/\//i.test(raw) ? raw : `https://${raw}`;
    const name = artifact.split("/").pop()?.replace(/\.zip$/i, "") || "plugin";
    try {
      await installFromArtifact(artifact, name);
      toaster.toast({
        title: "GitHub Sideload",
        body: "已交给 Decky 安装确认框",
      });
    } catch (err) {
      toaster.toast({
        title: "GitHub Sideload",
        body: err instanceof Error ? err.message : String(err),
      });
    }
  };

  return (
    <>
      <PanelSection title="安装通道">
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => void refresh()} disabled={loading}>
            {channel.ok ? "通道可用 · 刷新列表" : channel.reason}
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>
      <PanelSection title="非商店插件">
        {!list && !error ? (
          <PanelSectionRow>
            <ButtonItem layout="below" disabled>
              加载中
            </ButtonItem>
          </PanelSectionRow>
        ) : null}
        {error ? (
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={() => void refresh()}>
              {error}
            </ButtonItem>
          </PanelSectionRow>
        ) : null}
        {list && !list.store_ok ? (
          <PanelSectionRow>
            <ButtonItem layout="below" disabled>
              {`商店目录失败：${list.store_error ?? "未知错误"}`}
            </ButtonItem>
          </PanelSectionRow>
        ) : null}
        {list?.store_ok && sideload.length === 0 ? (
          <PanelSectionRow>
            <ButtonItem layout="below" disabled>
              没有非商店插件
            </ButtonItem>
          </PanelSectionRow>
        ) : null}
        {sideload.map((plugin) => (
          <PanelSectionRow key={plugin.folder}>
            <ButtonItem
              label={plugin.name}
              description={pluginLabel(plugin)}
              layout="below"
              disabled={!channel.ok || plugin.is_self}
              onClick={() => onUninstall(plugin.name)}
            >
              卸载
            </ButtonItem>
          </PanelSectionRow>
        ))}
      </PanelSection>
      <PanelSection title="从 zip URL 安装">
        <PanelSectionRow>
          <TextField
            label="zip 地址"
            value={installUrl}
            onChange={(event) => setInstallUrl(event.target.value)}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={!channel.ok || installUrl.trim().length === 0} onClick={() => void onInstallUrl()}>
            安装
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>
    </>
  );
}

export default definePlugin(() => {
  return {
    name: "GitHub Sideload",
    titleView: <div className={staticClasses.Title}>GitHub Sideload</div>,
    content: <Content />,
    icon: <FaGithub />,
    onDismount() {},
  };
});
