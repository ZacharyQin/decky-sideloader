import { ButtonItem, DropdownItem, PanelSection, PanelSectionRow, TextField } from "@decky/ui";
import { useEffect, useState } from "react";

import { clearBinding, listReleases, listSideloadPlugins, setBinding } from "./api";
import { InstallType, installFromArtifact, probeLoaderChannel, uninstallPlugin } from "./loaderBridge";
import { toastError, toastInfo } from "./toast";
import type { PluginListResponse, PluginRow, ReleaseRow } from "./types";

function pluginDescription(plugin: PluginRow): string {
  const version = plugin.version ?? "未知版本";
  if (!plugin.repo) {
    return `${version} · 未绑定 GitHub`;
  }
  const source = plugin.repo_source === "user" ? "手填" : "package.json";
  return `${version} · ${plugin.repo}（${source}）`;
}

function installTypeFor(release: ReleaseRow): InstallType {
  if (release.newer === true) {
    return InstallType.UPDATE;
  }
  if (release.newer === false) {
    return InstallType.DOWNGRADE;
  }
  return InstallType.OVERWRITE;
}

export function InstalledTab() {
  const [list, setList] = useState<PluginListResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [repoDraft, setRepoDraft] = useState("");
  const [releases, setReleases] = useState<ReleaseRow[]>([]);
  const [tag, setTag] = useState<string | null>(null);
  const [zipUrl, setZipUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const channel = probeLoaderChannel();

  const sideload = list?.plugins.filter((plugin) => plugin.sideload) ?? [];
  const current = sideload.find((plugin) => plugin.name === selected) ?? null;
  const currentRelease = releases.find((item) => item.tag === tag) ?? null;
  const zipOptions = currentRelease?.zips ?? [];

  const refresh = async () => {
    setBusy(true);
    setError(null);
    try {
      const next = await listSideloadPlugins();
      setList(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  useEffect(() => {
    if (!current) {
      setRepoDraft("");
      setReleases([]);
      setTag(null);
      setZipUrl(null);
      return;
    }
    setRepoDraft(current.repo ?? "");
  }, [current?.name, current?.repo]);

  const loadReleases = async (plugin: PluginRow, repo: string) => {
    setBusy(true);
    try {
      const rows = await listReleases(repo, plugin.version);
      setReleases(rows);
      const first = rows.find((item) => item.has_zip) ?? null;
      setTag(first?.tag ?? null);
      setZipUrl(first?.zips[0]?.url ?? null);
    } catch (err) {
      setReleases([]);
      setTag(null);
      setZipUrl(null);
      toastError(err);
    } finally {
      setBusy(false);
    }
  };

  const onSaveBinding = async () => {
    if (!current) {
      return;
    }
    setBusy(true);
    try {
      const saved = await setBinding(current.name, repoDraft);
      toastInfo(`已绑定 ${saved.repo}`);
      await refresh();
      await loadReleases(current, saved.repo);
    } catch (err) {
      toastError(err);
    } finally {
      setBusy(false);
    }
  };

  const onClearBinding = async () => {
    if (!current) {
      return;
    }
    setBusy(true);
    try {
      await clearBinding(current.name);
      await refresh();
    } catch (err) {
      toastError(err);
    } finally {
      setBusy(false);
    }
  };

  const onInstallVersion = async () => {
    if (!current || !currentRelease || !zipUrl) {
      return;
    }
    try {
      await installFromArtifact(zipUrl, current.name, currentRelease.tag, installTypeFor(currentRelease));
      toastInfo("已交给 Decky 安装确认框");
    } catch (err) {
      toastError(err);
    }
  };

  const onUninstall = () => {
    if (!current) {
      return;
    }
    try {
      uninstallPlugin(current.name);
    } catch (err) {
      toastError(err);
    }
  };

  return (
    <>
      <PanelSection title="非商店插件">
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
              description={pluginDescription(plugin)}
              layout="below"
              onClick={() => setSelected(plugin.name)}
            >
              {selected === plugin.name ? "已选中" : "管理"}
            </ButtonItem>
          </PanelSectionRow>
        ))}
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={busy} onClick={() => void refresh()}>
            刷新列表
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>
      {current ? (
        <PanelSection title={current.name}>
          <PanelSectionRow>
            <TextField
              label="GitHub 仓库"
              description="owner/repo 或 github.com 链接"
              value={repoDraft}
              onChange={(event) => setRepoDraft(event.target.value)}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem layout="below" disabled={busy || repoDraft.trim().length === 0} onClick={() => void onSaveBinding()}>
              保存绑定
            </ButtonItem>
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem layout="below" disabled={busy || current.repo_source !== "user"} onClick={() => void onClearBinding()}>
              清除手填绑定
            </ButtonItem>
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              disabled={busy || !current.repo}
              onClick={() => current.repo && void loadReleases(current, current.repo)}
            >
              拉取版本
            </ButtonItem>
          </PanelSectionRow>
          {releases.length > 0 ? (
            <PanelSectionRow>
              <DropdownItem
                label="版本"
                rgOptions={releases.map((item) => ({
                  label: `${item.tag}${item.newer === true ? " · 可更新" : ""}${item.has_zip ? "" : " · 无 zip"}`,
                  data: item.tag,
                }))}
                selectedOption={tag}
                onChange={(option) => {
                  const next = releases.find((item) => item.tag === option.data);
                  setTag(option.data);
                  setZipUrl(next?.zips[0]?.url ?? null);
                }}
                strDefaultLabel="选择版本"
              />
            </PanelSectionRow>
          ) : null}
          {zipOptions.length > 1 ? (
            <PanelSectionRow>
              <DropdownItem
                label="zip"
                rgOptions={zipOptions.map((item) => ({ label: item.name, data: item.url }))}
                selectedOption={zipUrl}
                onChange={(option) => setZipUrl(option.data)}
                strDefaultLabel="选择 zip"
              />
            </PanelSectionRow>
          ) : null}
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              disabled={!channel.ok || !zipUrl || busy}
              onClick={() => void onInstallVersion()}
            >
              {channel.ok ? "安装该版本" : channel.reason}
            </ButtonItem>
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem layout="below" disabled={!channel.ok} onClick={onUninstall}>
              卸载
            </ButtonItem>
          </PanelSectionRow>
        </PanelSection>
      ) : null}
    </>
  );
}
