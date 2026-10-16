// Semantic index:
// - participant: docs/upstream-conflict/sideload/plugin-management.md -- reuse native repository search presentation for binding lists and new-install dropdowns.
import { DropdownItem, Field, PanelSectionRow, TextField } from "@decky/ui";
import { FaGithub } from "react-icons/fa";
import { Action, Status } from "./components";
import type { RepositoryRow } from "./types";

export const repositoryDetails = (repo: RepositoryRow) => <>
  {repo.description && <div>{repo.description}</div>}
  <div>{[`${repo.stars} 个 Star`, repo.fork && "Fork", repo.archived && "已归档"].filter(Boolean).join("，")}</div>
</>;

export function RepositorySearch({ query, results, selected, busy, disabled = false, dropdown = false, onQuery, onSearch, onSelect }: {
  query: string; results: RepositoryRow[] | null; selected: string | null; busy: boolean;
  disabled?: boolean; dropdown?: boolean; onQuery: (query: string) => void;
  onSearch: () => void; onSelect: (repo: RepositoryRow) => void;
}) {
  const choice = results?.find((r) => r.repo === selected);
  return <>
    <PanelSectionRow><TextField label="搜索 GitHub 仓库" description="插件名或关键词"
      value={query} disabled={disabled} onChange={(e) => onQuery(e.target.value)} /></PanelSectionRow>
    <Action kind="search" disabled={disabled || busy || !query.trim()} onClick={onSearch}>{busy ? "正在搜索…" : "搜索仓库"}</Action>
    {results?.length === 0 && <Status title="未找到仓库">请更换关键词，或直接填写仓库。</Status>}
    {dropdown && !!results?.length && <>
      <PanelSectionRow><DropdownItem label="搜索结果" selectedOption={selected} disabled={disabled || busy}
        strDefaultLabel="选择 GitHub 仓库" rgOptions={results.map((r) => ({ data: r.repo, label: `${r.repo}${r.fork ? " · Fork" : ""}${r.archived ? " · 已归档" : ""}` }))}
        onChange={(option) => { const repo = results.find((r) => r.repo === option.data); if (repo) onSelect(repo); }} /></PanelSectionRow>
      {choice && <Status title={choice.repo}>{repositoryDetails(choice)}</Status>}
    </>}
    {!dropdown && results?.map((repo) => <PanelSectionRow key={repo.repo}>
      <Field label={repo.repo} icon={<FaGithub aria-hidden focusable="false" />} description={repositoryDetails(repo)} focusable highlightOnFocus
        preferredFocus={selected === repo.repo} disabled={disabled} onActivate={() => onSelect(repo)} onClick={() => onSelect(repo)} onOKActionDescription="选择仓库" />
    </PanelSectionRow>)}
    {results && results.length >= 20 && <Status title="显示前 20 条结果">可细化关键词继续搜索。</Status>}
  </>;
}
