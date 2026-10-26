import { Field, Navigation, PanelSectionRow } from "@decky/ui";
import { useEffect, useState } from "react";
import { FaChevronRight, FaFileAlt } from "react-icons/fa";
import { getOpenSourceNotices } from "./api";
import { Action, Heading, PanelSection, Status } from "./components";
import { errorText } from "./errors";
import { t } from "./i18n";
import type { LicenseEntry } from "./types";
import { updateViewState, useViewState } from "./viewState";

const groups = [
  ["project", "本插件"], ["decky", "Decky"], ["icons", "图标"], ["qr", "二维码"],
  ["markdown", "Markdown"], ["versions", "版本比较"], ["transitive", "Markdown 间接依赖"], ["embedded", "内嵌代码来源"],
] as const;
let pending: Promise<{ entries: LicenseEntry[] }> | null = null;
function readNotices() {
  return pending ??= getOpenSourceNotices().catch(error => { pending = null; throw error; });
}
function NoticeRow({ title, description, id, onClick }: { title: string; description?: string; id: string; onClick: () => void }) {
  const { licenses } = useViewState();
  return <PanelSectionRow><Field className="gss-row gss-action-row" data-action-key={id} label={title} description={description}
    icon={<span className="gss-row-icon"><FaFileAlt aria-hidden focusable="false" /></span>} bottomSeparator="none"
    focusable highlightOnFocus preferredFocus={licenses.focus === id} childrenContainerWidth="min" inlineWrap="keep-inline"
    onOKActionDescription={title} onActivate={onClick} onClick={onClick}>
    <FaChevronRight className="gss-chevron" aria-hidden focusable="false" />
  </Field></PanelSectionRow>;
}
function LicenseContent({ entries }: { entries: LicenseEntry[] }) {
  const { licenses } = useViewState();
  const entry = entries.find(item => item.id === licenses.entry);
  if (licenses.entry && !entry) return <Status tone="error" title={t("找不到许可材料")} />;
  if (entry) {
    const doc = licenses.document === null ? null : entry.documents[licenses.document];
    if (licenses.document !== null && !doc) return <Status tone="error" title={t("找不到许可材料")} />;
    if (doc) return <PanelSection><Heading title={t("许可证全文")}>{entry.name} · {doc.title}</Heading>
      <div className="gss-license-text" aria-label={t("许可证全文")}>
        {doc.text.split(/(\n[\t ]*\n)/).map((paragraph, index) => <Field key={index} className="gss-license-paragraph"
          label={paragraph} focusable={Boolean(paragraph.trim())} highlightOnFocus={false} bottomSeparator="none" />)}
      </div>
    </PanelSection>;
    return <>
      <PanelSection><Heading title={entry.name}>{entry.license}{entry.host ? ` · ${t("宿主提供")}` : ""}</Heading>
        <Status title={entry.host ? t("开发接口版本") : t("版本")}>{entry.version}</Status>
        {entry.attribution && <Status title={t("版权署名")}><span className="gss-license-attribution">{entry.attribution}</span></Status>}
        {entry.note === "font-awesome-svg" && <Status title={t("图标")}>{t("通过 React Icons 转为 React SVG 组件使用，未修改图案。")}</Status>}
        <NoticeRow id="license-source" title={t("项目或许可来源")} description={entry.source}
          onClick={() => { if (/^https?:\/\//.test(entry.source)) Navigation.NavigateToExternalWeb(entry.source); }} />
      </PanelSection>
      <PanelSection title={t("许可证与声明")}>{entry.documents.map((doc, index) => <NoticeRow key={index} id={`license-document-${index}`}
        title={doc.title} description={t("查看全文")} onClick={() => updateViewState(current => ({ ...current,
          licenses: { ...current.licenses, document: index, focus: null } }))} />)}</PanelSection>
    </>;
  }
  return <><Heading title={t("开源许可")} />{groups.map(([id, title]) => {
    const items = entries.filter(item => item.group === id);
    if (!items.length) return null;
    return <PanelSection key={id} title={t(title)}>
      {id === "transitive" && <NoticeRow id="license-dependencies" title={t(licenses.expanded ? "收起间接依赖" : "展开间接依赖")}
        onClick={() => updateViewState(current => ({ ...current, licenses: { ...current.licenses, expanded: !current.licenses.expanded, focus: "license-dependencies" } }))} />}
      {(id !== "transitive" || licenses.expanded) && items.map(item => <NoticeRow key={item.id} id={`license-entry-${item.id}`} title={item.name}
        description={`${item.license}${item.host ? ` · ${t("宿主提供")}` : ` · ${item.version}`}`}
        onClick={() => updateViewState(current => ({ ...current, licenses: { ...current.licenses, entry: item.id, document: null, focus: null } }))} />)}
    </PanelSection>;
  })}</>;
}
export function LicensesPage() {
  const [entries, setEntries] = useState<LicenseEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let active = true;
    setError(null);
    void readNotices().then(result => { if (active) setEntries(result.entries); }, error => { if (active) setError(errorText(error)); });
    return () => { active = false; };
  }, [attempt]);
  if (error) return <PanelSection><Status tone="error" title={t("许可材料读取失败")}>{error}</Status>
    <Action kind="reloadSettings" onClick={() => setAttempt(attempt + 1)}>{t("重试")}</Action></PanelSection>;
  return entries ? <LicenseContent entries={entries} /> : <Status tone="loading" title={t("正在读取许可材料…")} />;
}
