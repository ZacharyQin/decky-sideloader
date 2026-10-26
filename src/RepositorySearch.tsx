import { t } from "./i18n";
import { Field, Focusable, PanelSectionRow, TextField } from "@decky/ui";
import { FaChevronRight, FaGithub } from "react-icons/fa";
import { Action, Status } from "./components";
import type { RepositoryRow } from "./types";

export const repositoryDetails = (repo: RepositoryRow) => <>
  {repo.description && <div>{repo.description}</div>}
  <div>{[t("{0} 个 Star", [repo.stars]), repo.fork && "Fork", repo.archived && t("已归档")].filter(Boolean).join("，")}</div>
</>;

export function RepositorySearch({ query, results, selected, busy, disabled = false, label = t("搜索 GitHub 仓库"), actionLabel = t("搜索仓库"), onQuery, onSearch, onSelect }: {
  query: string; results: RepositoryRow[] | null; selected: string | null; busy: boolean;
  disabled?: boolean; label?: string; actionLabel?: string; onQuery: (query: string) => void;
  onSearch: () => void; onSelect: (repo: RepositoryRow) => void;
}) {
  const submit = () => { if (!disabled && !busy && query.trim()) onSearch(); };
  return <>
    <PanelSectionRow><Focusable className="gss-repository-input" flow-children="row">
      <TextField label={label} value={query} disabled={disabled}
        onChange={(e) => onQuery(e.target.value)} onKeyDown={(e) => {
          if (e.key === "Enter" && !e.nativeEvent.isComposing && !e.repeat) {
            e.preventDefault(); e.stopPropagation();
            if (!disabled && !busy && query.trim()) { e.currentTarget.blur(); submit(); }
          }
        }} inlineControls={<Action kind="search" iconOnly loading={busy || disabled} disabled={disabled || busy || !query.trim()} onClick={submit}>
          {busy ? t("正在搜索…") : actionLabel}
        </Action>} />
    </Focusable></PanelSectionRow>
    {results?.length === 0 && <Status title={t("未找到仓库")}>{t("请更换关键词，或直接填写仓库。")}</Status>}
    {results?.map((repo) => <PanelSectionRow key={repo.repo}>
      <Field className="gss-row" label={repo.repo} icon={<FaGithub aria-hidden focusable="false" />} description={repositoryDetails(repo)} focusable={!disabled && !busy} highlightOnFocus
        preferredFocus={selected === repo.repo} disabled={disabled || busy} bottomSeparator="none" childrenContainerWidth="min" inlineWrap="keep-inline"
        onActivate={() => { if (!disabled && !busy) onSelect(repo); }} onClick={() => { if (!disabled && !busy) onSelect(repo); }} onOKActionDescription={t("选择仓库")}>
        <FaChevronRight className="gss-chevron" aria-hidden focusable="false" />
      </Field>
    </PanelSectionRow>)}
    {results && results.length >= 20 && <Status title={t("显示前 20 条结果")}>{t("可细化关键词继续搜索。")}</Status>}
  </>;
}
