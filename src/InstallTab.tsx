// Semantic index:
// - participant: docs/upstream-conflict/sideload/plugin-management.md -- direct URLs and searched repositories share Decky discovery, explicit monorepo selection and ZIP identity checks.
import { DropdownItem, Focusable, NavEntryPositionPreferences, PanelSection, PanelSectionRow, TextField } from "@decky/ui";
import { listReleases, listSideloadPlugins, probeRepository, searchRepositories } from "./api";
import { FaPuzzlePiece } from "react-icons/fa";
import { Action, errorText, Explanation, Status } from "./components";
import { ReleasePicker } from "./ReleasePicker";
import { RepositorySearch } from "./RepositorySearch";
import { probeLoaderChannel } from "./loaderBridge";
import { isActive, startInstallation, useInstallation } from "./installController";
import { pluginReadOnly, pluginEligible } from "./loaderManagement";
import { initialSelection } from "./viewModel";
import { getViewState, resetInstallView, updateViewState, useViewState, type InstallDraft } from "./viewState";
import type { PluginRow, RepositoryPlugin, RepositoryProbe, RepositoryRow } from "./types";

let requestSequence = 0;
const emptySelection = () => ({ tag: null, zip: null });

export function InstallTab({ onExisting, onOperation }: { onExisting: (plugin: PluginRow) => void; onOperation: () => void }) {
  const { operation } = useInstallation();
  const form = useViewState().install;
  const current = () => getViewState().install;
  const valid = (request: number) => current().request === request;
  const change = (patch: Partial<InstallDraft>, request?: number) => updateViewState((state) => request !== undefined && state.install.request !== request ? state
    : { ...state, install: { ...state.install, ...patch } });
  const sourceChanged = (patch: Partial<InstallDraft>) => change({ probe: null, pluginPath: null, releases: null,
    selection: emptySelection(), busy: false, searching: false, error: null, request: ++requestSequence, ...patch });
  const selectRepository = (repo: RepositoryRow) => {
    const state = current();
    if (state.mode === "search" && state.repositories?.some((r) => r.repo === repo.repo)) sourceChanged({ repository: repo.repo });
  };
  const search = async () => {
    const state = current();
    if (state.busy || state.searching || !state.query.trim()) return;
    const request = ++requestSequence;
    change({ request, searching: true, repositories: null, repository: null, error: null });
    try { change({ repositories: await searchRepositories(state.query.trim()), searching: false }, request); }
    catch (err) { change({ error: errorText(err), searching: false }, request); }
  };
  const loadVersions = async (request: number, probe: RepositoryProbe, candidate: RepositoryPlugin) => {
    const listing = await listSideloadPlugins();
    if (!valid(request)) return;
    const existing = listing.plugins.find((p) => !p.is_self && p.name === candidate.name && (pluginReadOnly(p) || !pluginEligible(p) || p.repo?.toLowerCase() === probe.repo.toLowerCase()));
    if (existing) { resetInstallView(); onExisting(existing); return; }
    const rows = await listReleases(probe.repo, null);
    if (!valid(request)) return;
    change({ releases: rows, selection: initialSelection(rows) }, request);
  };
  const read = async () => {
    const state = current(), source = state.mode === "search" ? state.repository : state.draft.trim();
    if (state.busy || state.searching || !source) return;
    const request = ++requestSequence;
    change({ request, busy: true, error: null });
    try {
      const probe = await probeRepository(source);
      if (!valid(request)) return;
      const candidate = probe.plugins.length === 1 ? probe.plugins[0] : null;
      change({ probe, pluginPath: candidate?.path ?? null }, request);
      if (candidate) await loadVersions(request, probe, candidate);
    } catch (err) { change({ error: errorText(err) }, request); }
    finally { change({ busy: false }, request); }
  };
  const readVersions = async () => {
    const state = current(), candidate = state.probe?.plugins.find((p) => p.path === state.pluginPath);
    if (state.busy || !state.probe || !candidate) return;
    const request = ++requestSequence;
    change({ request, busy: true, error: null });
    try { await loadVersions(request, state.probe, candidate); }
    catch (err) { change({ error: errorText(err) }, request); }
    finally { change({ busy: false }, request); }
  };
  const install = async () => {
    const state = current(), release = state.releases?.find((r) => r.tag === state.selection.tag);
    if (state.busy || !release || !state.selection.zip || !state.probe || state.pluginPath === null) return;
    const request = ++requestSequence;
    change({ request, busy: true, error: null });
    try {
      await startInstallation(release, state.selection.zip, null, state.pluginPath);
      if (valid(request)) { resetInstallView(); onOperation(); }
    } catch (err) { change({ error: errorText(err) }, request); }
    finally { change({ busy: false }, request); }
  };
  const channel = probeLoaderChannel();
  const candidate = form.probe?.plugins.find((p) => p.path === form.pluginPath);
  const phase = form.releases ? "preview" : form.probe ? "projects" : "input";
  const back = () => {
    if (form.busy) return;
    if (form.releases) change({ releases: null, selection: emptySelection(), error: null, request: ++requestSequence });
    else sourceChanged({});
  };
  return <Focusable key={`${phase}-${phase === "input" ? form.mode : ""}`} autoFocus flow-children="column" navEntryPreferPosition={NavEntryPositionPreferences.PREFERRED_CHILD}
    onCancel={(event) => { if (phase !== "input") { event.preventDefault(); event.stopPropagation(); back(); } }}
    onCancelActionDescription={phase === "input" ? "返回我的插件" : phase === "preview" ? "返回插件选择" : "返回仓库输入"}>
    <PanelSection title={phase === "preview" ? "安装预览" : phase === "projects" ? "仓库插件探查" : "从 GitHub 安装"}>
      {phase === "input" && <>
        {form.mode === "manual" ? <>
          <PanelSectionRow><TextField label="GitHub 仓库" description="完整 github.com 地址或 owner/repo"
            value={form.draft} disabled={form.busy} onChange={(e) => sourceChanged({ draft: e.target.value })} /></PanelSectionRow>
          <Action kind="searchMode" disabled={form.busy} onClick={() => sourceChanged({ mode: "search" })}>通过搜索选择仓库</Action>
        </> : <>
          <RepositorySearch dropdown query={form.query} results={form.repositories} selected={form.repository} busy={form.searching} disabled={form.busy}
            onQuery={(query) => sourceChanged({ query, repositories: null, repository: null })} onSearch={() => void search()} onSelect={selectRepository} />
          <Action kind="manualRepo" disabled={form.busy} onClick={() => sourceChanged({ mode: "manual" })}>直接填写仓库</Action>
        </>}
        <Action kind="probeRepo" disabled={form.busy || form.searching || !(form.mode === "search" ? form.repository : form.draft.trim())} onClick={() => void read()}>{form.busy ? "正在探查…" : "读取仓库"}</Action>
        <Explanation>插件会静态读取仓库信息，识别可安装的 Decky 插件。</Explanation>
      </>}
      {form.probe && <>
        <Status title={form.probe.repo} />
        {form.probe.plugins.length === 0 && <Status tone="warning" title="未确认 Decky 插件">未找到同目录的有效 plugin.json、package.json 和 Decky 实现特征，不能继续安装。</Status>}
        {phase === "projects" && form.probe.plugins.length > 1 && <PanelSectionRow><DropdownItem label="仓库内的插件" selectedOption={form.pluginPath} disabled={form.busy}
          strDefaultLabel="请选择要安装的插件" rgOptions={form.probe.plugins.map((p) => ({ data: p.path, label: `${p.name} · ${p.path || "根目录"}` }))}
          onChange={(option) => {
            const state = current();
            if (state.probe?.repo === form.probe?.repo && state.probe?.tree_sha === form.probe?.tree_sha && state.probe?.plugins.some((p) => p.path === option.data)) {
              change({ pluginPath: option.data, releases: null, selection: emptySelection(), error: null, request: ++requestSequence });
            }
          }} /></PanelSectionRow>}
        {candidate && <Status icon={FaPuzzlePiece} title={`已识别：${candidate.name}`}>{candidate.path || "仓库根目录"}<br />{candidate.evidence.join("，")}</Status>}
        {(form.probe.plugins.length > 1 || !!candidate?.path) && <Status tone="warning" title="请核对插件对应的发布与 ZIP">monorepo 的发布可能属于其他插件。准备安装时会校验插件名和包名，不能只凭 ZIP 文件名判断。</Status>}
      </>}
      {phase === "projects" && !!form.probe?.plugins.length && <Action kind="readReleases" disabled={form.busy || !candidate} onClick={() => void readVersions()}>{form.busy ? "正在读取版本…" : "读取版本"}</Action>}
      {phase === "preview" && form.releases && <>
        <ReleasePicker releases={form.releases} tag={form.selection.tag} zip={form.selection.zip} disabled={form.busy}
          onChange={(tag, zip) => {
            const state = current(), release = state.releases?.find((r) => r.tag === tag);
            if (valid(form.request) && release && (zip === null || release.zips.some((a) => a.url === zip))) change({ selection: { tag, zip } });
          }} />
        <Action kind="installPackage" disabled={form.busy || isActive(operation) || !form.selection.zip || !candidate || !channel.ok} onClick={() => void install()}>{form.busy ? "正在准备…" : "继续安装"}</Action>
        {form.probe && form.probe.plugins.length > 1 && <Action kind="changePlugin" disabled={form.busy} onClick={back}>更换插件</Action>}
        {!channel.ok && <Status tone="error" title="安装通道不可用">{channel.reason}</Status>}
      </>}
      {phase !== "input" && <Action kind="changeRepo" disabled={form.busy} onClick={() => sourceChanged({})}>更换仓库</Action>}
      {form.error && <Status tone="error" title="操作失败">{form.error}</Status>}
    </PanelSection>
  </Focusable>;
}
