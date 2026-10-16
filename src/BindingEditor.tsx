// Semantic index:
// - participant: docs/upstream-conflict/sideload/plugin-management.md -- search and select a repository before explicit binding; preserve drafts and reject stale search results.
import { Focusable, NavEntryPositionPreferences, PanelSection, PanelSectionRow, TextField } from "@decky/ui";
import { useEffect } from "react";
import { clearBinding, searchRepositories, setBinding } from "./api";
import { Action, errorText, Explanation, Status } from "./components";
import { RepositorySearch, repositoryDetails } from "./RepositorySearch";
import { createBindingDraft, getViewState, updateViewState, useViewState, type BindingDraft } from "./viewState";
import { changeOwnership, useOwnership } from "./ownershipController";
import { pluginEligible, pluginCanBind, pluginCanTakeOver } from "./loaderManagement";
import type { PluginRow, RepositoryRow } from "./types";

let requestSequence = 0;

export function BindingEditor({ plugin, onSaved }: { plugin: PluginRow; onSaved: () => void }) {
  const ownership = useOwnership(plugin);
  const takeover = pluginCanTakeOver(plugin) && !pluginEligible(plugin) && (!plugin.ownership?.intent || plugin.ownership.intent.state === "released");
  const key = JSON.stringify([plugin.folder, plugin.repo]);
  const cached = useViewState().bindingDraft;
  const form = cached.key === key ? cached : createBindingDraft(plugin);
  useEffect(() => {
    if (getViewState().bindingDraft.key !== key) {
      updateViewState((current) => ({ ...current, bindingDraft: createBindingDraft(plugin) }));
    }
  }, [key]);
  const change = (patch: Partial<BindingDraft>, request?: number) => updateViewState((current) => {
    if (current.bindingDraft.key !== key || request !== undefined && current.bindingDraft.request !== request) return current;
    return { ...current, bindingDraft: { ...current.bindingDraft, ...patch } };
  });
  const search = async () => {
    const current = getViewState().bindingDraft;
    if (current.key !== key || current.busy || current.searching || !current.query.trim()) return;
    const request = ++requestSequence;
    change({ request, searching: true, error: null, results: null, selected: null });
    try { change({ results: await searchRepositories(current.query.trim()), searching: false }, request); }
    catch (err) { change({ error: errorText(err), searching: false }, request); }
  };
  const select = (repo: RepositoryRow) => {
    const current = getViewState().bindingDraft;
    if (current.key === key && current.results?.some((row) => row.repo === repo.repo)) {
      change({ selected: repo, value: repo.repo, error: null });
    }
  };
  const save = async (clear: boolean) => {
    const current = getViewState().bindingDraft;
    if (current.key !== key || current.busy) return;
    change({ busy: true, error: null });
    try {
      if (clear) await clearBinding(plugin.name);
      else if (takeover) {
        if (!await changeOwnership(plugin, "takeover", current.value)) { onSaved(); return; }
      } else await setBinding(plugin.name, current.value);
      onSaved();
    } catch (err) { change({ error: errorText(err) }); }
    finally { change({ busy: false }); }
  };
  const phase = form.mode === "manual" ? "manual" : form.selected ? "selected" : "search";
  const backToSearch = () => change({ mode: "search", selected: null, error: null });
  if (!pluginCanBind(plugin) && !takeover) return <PanelSection><Status title="归属未确认">请从插件详情回查接管状态。</Status></PanelSection>;
  return <Focusable key={phase} autoFocus flow-children="column" navEntryPreferPosition={NavEntryPositionPreferences.PREFERRED_CHILD}
    onCancel={(event) => {
      if (phase !== "search") { event.preventDefault(); event.stopPropagation(); if (!form.busy) backToSearch(); }
    }} onCancelActionDescription={phase === "search" ? "返回插件详情" : "返回搜索"}>
    <PanelSection title={`${takeover ? "接管商店插件" : "关联仓库"}：${plugin.name}`}>
      {plugin.repo && <Status title="当前关联">{plugin.repo}</Status>}
      {plugin.repo_source === "package.json" && <Explanation>仓库来自插件元数据，保存前请核对作者与插件对应关系。</Explanation>}
      {takeover && <Explanation>此插件目前由商店负责。接管会暂停它的商店更新检查，由本插件追踪所确认的 GitHub 仓库。不会安装或卸载程序；可从详情交还商店。原生冻结不禁止手动商店安装，也不保证取消已在途检查。</Explanation>}
      {ownership.error && <Status tone="error" title="接管未确认">{ownership.error}</Status>}
      {form.error && <Status tone="error" title="操作失败">{form.error}</Status>}
      {phase === "search" && <>
        <RepositorySearch query={form.query} results={form.results} selected={form.value} busy={form.searching} disabled={form.busy}
          onQuery={(query) => change({ query, results: null, error: null, searching: false, request: ++requestSequence })}
          onSearch={() => void search()} onSelect={select} />
        <Action kind="manualRepo" disabled={form.busy} onClick={() => change({ mode: "manual", searching: false, request: ++requestSequence, error: null })}>直接填写仓库</Action>
      </>}
      {phase === "selected" && form.selected && <>
        <Status title="已选择仓库">{form.selected.repo}{repositoryDetails(form.selected)}</Status>
        <Explanation>保存后，插件会从此仓库追踪版本。安装时仍会校验发布包身份。</Explanation>
      </>}
      {phase === "manual" && <PanelSectionRow><TextField label="GitHub 仓库" description="owner/repo 或 github.com 链接"
        value={form.value} disabled={form.busy} onChange={(e) => change({ value: e.target.value, error: null })} /></PanelSectionRow>}
      {phase !== "search" && <>
        <Action kind="saveBinding" disabled={form.busy || ownership.busy || !form.value.trim()} onClick={() => void save(false)}>{form.busy || ownership.busy ? "正在保存…" : takeover ? "接管并暂停商店检查" : "保存关联"}</Action>
        <Action kind="returnSearch" disabled={form.busy} onClick={backToSearch}>返回搜索</Action>
      </>}
      {plugin.repo && !takeover && <>
        <Action kind="unlink" disabled={form.busy} onClick={() => void save(true)}>解除关联</Action>
        <Explanation>解除关联后，插件会停止版本追踪，保留已安装程序。</Explanation>
      </>}
    </PanelSection>
  </Focusable>;
}
