// Semantic index:
// - participant: docs/upstream-conflict/sideload/plugin-management.md -- latest release and ambiguous assets require explicit selection.
import { DropdownItem, PanelSectionRow } from "@decky/ui";
import { useState } from "react";
import { FaBoxOpen, FaFileAlt } from "react-icons/fa";
import { Action, Status } from "./components";
import type { ReleaseRow } from "./types";
import { onlyAsset } from "./viewModel";

export function ReleasePicker({ releases, tag, zip, onChange, disabled = false }: {
  releases: ReleaseRow[]; tag: string | null; zip: string | null;
  onChange: (tag: string, zip: string | null) => void; disabled?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const release = releases.find((r) => r.tag === tag);
  if (!releases.length) return <Status title="仓库没有已发布版本" />;
  return <>
    <PanelSectionRow><DropdownItem label="目标版本" selectedOption={tag} disabled={disabled}
      rgOptions={releases.map((r, i) => ({ data: r.tag, label: `${r.tag}${i === 0 ? " · 最新发布" : ""}${r.prerelease ? " · 预发布" : ""}${r.has_zip ? "" : " · 无安装包"}` }))}
      onChange={(option) => { setExpanded(false); onChange(option.data, onlyAsset(releases.find((r) => r.tag === option.data))); }} /></PanelSectionRow>
    {release && !release.has_zip && <Status tone="warning" title="此版本没有安装包">请选择其他版本。不会自动切换到旧版。</Status>}
    {release?.zips.length === 1 && <Status icon={FaBoxOpen} title="安装包">{release.zips[0].name}</Status>}
    {release && release.zips.length > 1 && <PanelSectionRow><DropdownItem label="安装包" selectedOption={zip} disabled={disabled}
      strDefaultLabel="请选择安装包" rgOptions={release.zips.map((a) => ({ data: a.url, label: a.name }))}
      onChange={(option) => onChange(release.tag, option.data)} /></PanelSectionRow>}
    {release && <Status icon={FaFileAlt} title="发布说明"><span style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{release.body ? (expanded ? release.body : release.body.slice(0, 200)) : "作者未提供发布说明"}</span></Status>}
    {release && release.body.length > 200 && <Action kind="releaseNotes" onClick={() => setExpanded(!expanded)}>{expanded ? "收起发布说明" : "查看完整发布说明"}</Action>}
  </>;
}
