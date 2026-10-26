import { getLocale, t } from "./i18n";
import { DropdownItem, Field, PanelSectionRow } from "@decky/ui";
import { FaArrowRight, FaBoxOpen, FaCheck, FaChevronRight, FaDownload } from "react-icons/fa";
import { Status } from "./components";
import type { ReleaseRow } from "./types";
import { onlyAsset } from "./viewModel";
import { ReleaseNotesPreview } from "./ReleaseNotes";

export function ReleasePicker({ releases, tag, zip, onChange, disabled = false, skipped = [], presentation = "selector", currentVersion }: {
  releases: ReleaseRow[]; tag: string | null; zip: string | null; skipped?: string[]; presentation?: "selector" | "summary" | "list"; currentVersion?: string | null;
  onChange: (tag: string, zip: string | null) => void; disabled?: boolean;
}) {
  const release = releases.find((r) => r.tag === tag);
  const releaseDescription = (r: ReleaseRow, index: number) => [index === 0 ? t("最新发布") : null, r.comparison === 0 ? t("该版本已安装，无需操作") : null,
    r.prerelease ? t("预发布") : null, !r.has_zip ? t("此版本没有安装包") : null, skipped.includes(r.tag) ? t("已跳过此版本") : null].filter(Boolean).join(" · ");
  if (!releases.length) return <Status title={t("仓库没有已发布版本")} />;
  if (presentation === "list") return <>{releases.map((r, index) => {
    const choose = () => { if (!disabled) onChange(r.tag, onlyAsset(r)); };
    return <PanelSectionRow key={r.tag}><Field className="gss-row gss-version-row" data-release-tag={r.tag} aria-current={r.tag === tag}
      label={r.tag} description={releaseDescription(r, index) || r.zips.map(asset => asset.name).join(" · ")} preferredFocus={r.tag === tag}
      disabled={disabled} focusable={!disabled} highlightOnFocus bottomSeparator="none" childrenContainerWidth="min" inlineWrap="keep-inline"
      onOKActionDescription={t("选择版本")} onActivate={choose} onClick={choose}>
      {r.tag === tag ? <FaCheck className="gss-chevron" aria-hidden focusable="false" /> : <FaChevronRight className="gss-chevron" aria-hidden focusable="false" />}
    </Field></PanelSectionRow>;
  })}</>;
  const count = release?.download_count;
  const downloads = typeof count === "number" && Number.isSafeInteger(count) && count >= 0 ? count.toLocaleString(getLocale()) : t("未知");
  return <>
    {presentation === "selector" ? <PanelSectionRow><DropdownItem label={t("目标版本")} selectedOption={tag} disabled={disabled}
      rgOptions={releases.map((r, i) => ({ data: r.tag, label: `${r.tag}${i === 0 ? t(" · 最新发布") : ""}${r.prerelease ? t(" · 预发布") : ""}${r.has_zip ? "" : t(" · 无安装包")}${skipped.includes(r.tag) ? t(" · 已跳过") : ""}` }))}
      onChange={(option) => onChange(option.data, onlyAsset(releases.find((r) => r.tag === option.data)))} /></PanelSectionRow> : release ? <>
      <div className="gss-release-label"><span>{release === releases[0] ? t("最新发布") : t("目标版本")}</span>{release.prerelease && <span className="gss-tag">{t("预发布")}</span>}</div>
      <div className="gss-version">{currentVersion && release.comparison !== 0 && <><span className="gss-from">{currentVersion}</span><FaArrowRight aria-hidden focusable="false" /></>}<strong>{release.tag}</strong></div>
    </> : <Status title={t("请选择目标版本")} />}
    {release && !release.has_zip && <Status tone="warning" title={t("此版本没有安装包")}>{t("请选择其他版本。不会自动切换到旧版。")}</Status>}
    {release && release.zips.length > 1 && <PanelSectionRow><DropdownItem label={t("安装包")} selectedOption={zip} disabled={disabled}
      strDefaultLabel={t("请选择安装包")} rgOptions={release.zips.map((a) => ({ data: a.url, label: a.name }))}
      onChange={(option) => onChange(release.tag, option.data)} /></PanelSectionRow>}
    {release && <div className="gss-release-meta">
      {release.zips.length === 1 && <div><FaBoxOpen aria-hidden focusable="false" /><span>{release.zips[0].name}</span></div>}
      <div><FaDownload aria-hidden focusable="false" /><span>{t("版本下载量：{0}", [downloads])}</span></div>
    </div>}
    {release && <ReleaseNotesPreview release={release} />}
  </>;
}
