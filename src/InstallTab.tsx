import { ButtonItem, DropdownItem, PanelSection, PanelSectionRow, TextField } from "@decky/ui";
import { useState } from "react";

import { listReleases } from "./api";
import { InstallType, installFromArtifact, probeLoaderChannel } from "./loaderBridge";
import { toastError, toastInfo } from "./toast";
import type { ReleaseRow } from "./types";

export function InstallTab() {
  const [repoDraft, setRepoDraft] = useState("");
  const [releases, setReleases] = useState<ReleaseRow[]>([]);
  const [tag, setTag] = useState<string | null>(null);
  const [zipUrl, setZipUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const channel = probeLoaderChannel();
  const currentRelease = releases.find((item) => item.tag === tag) ?? null;
  const zipOptions = currentRelease?.zips ?? [];

  const onFetch = async () => {
    setBusy(true);
    try {
      const rows = await listReleases(repoDraft, null);
      setReleases(rows);
      const first = rows.find((item) => item.has_zip) ?? null;
      setTag(first?.tag ?? null);
      setZipUrl(first?.zips[0]?.url ?? null);
      if (!first) {
        toastInfo("没有带 zip 的 Release");
      }
    } catch (err) {
      setReleases([]);
      setTag(null);
      setZipUrl(null);
      toastError(err);
    } finally {
      setBusy(false);
    }
  };

  const onInstall = async () => {
    if (!zipUrl) {
      return;
    }
    try {
      await installFromArtifact(zipUrl, "plugin", "dev", InstallType.INSTALL);
      toastInfo("已交给 Decky 安装确认框");
    } catch (err) {
      toastError(err);
    }
  };

  return (
    <PanelSection title="从 GitHub 安装">
      <PanelSectionRow>
        <TextField
          label="GitHub 链接"
          description="默认装最新带 zip 的 Release，也可改选历史版本"
          value={repoDraft}
          onChange={(event) => setRepoDraft(event.target.value)}
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem layout="below" disabled={busy || repoDraft.trim().length === 0} onClick={() => void onFetch()}>
          拉取版本
        </ButtonItem>
      </PanelSectionRow>
      {releases.length > 0 ? (
        <PanelSectionRow>
          <DropdownItem
            label="版本"
            rgOptions={releases.map((item) => ({
              label: `${item.tag}${item.prerelease ? " · pre" : ""}${item.has_zip ? "" : " · 无 zip"}`,
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
        <ButtonItem layout="below" disabled={!channel.ok || !zipUrl} onClick={() => void onInstall()}>
          {channel.ok ? "安装" : channel.reason}
        </ButtonItem>
      </PanelSectionRow>
    </PanelSection>
  );
}
