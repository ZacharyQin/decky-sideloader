import { t } from "./i18n";
import { DropdownItem, Focusable, NavEntryPositionPreferences, PanelSectionRow } from "@decky/ui";
import { listReleases, listSideloadPlugins, probeRepository, searchRepositories } from "./api";
import { FaPuzzlePiece } from "react-icons/fa";
import { Action, Explanation, Heading, PanelSection, Status } from "./components";
import { errorText } from "./errors";
import { ReleasePicker } from "./ReleasePicker";
import { RepositorySearch } from "./RepositorySearch";
import { probeLoaderChannel } from "./loaderBridge";
import { isActive, startInstallation, useInstallation } from "./installController";
import { pluginReadOnly, pluginEligible } from "./loaderManagement";
import { initialSelection } from "./viewModel";
import { getViewState, returnToInstallSearch, updateViewState, useViewState, type InstallDraft } from "./viewState";
import type { PluginRow, RepositoryPlugin, RepositoryProbe, RepositoryRow } from "./types";

const emptySelection = () => ({ tag: null, zip: null });

function isRepositoryAddress(value: string): boolean {
  if (/^(github\.com\/|git@github\.com:)/i.test(value) || /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(value)) return true;
  try { return Boolean(new URL(value).hostname); } catch { return false; }
}

export function InstallTab({ onExisting, onOperation }: { onExisting: (plugin: PluginRow) => void; onOperation: () => void }) {
  const { operation, preparing } = useInstallation();
  const form = useViewState().install;
  const current = () => getViewState().install;
  const valid = (request: number) => current().request === request;
  const change = (patch: Partial<InstallDraft>, request?: number) => updateViewState((state) => request !== undefined && state.install.request !== request ? state
    : { ...state, install: { ...state.install, ...patch } });
  const sourceChanged = (patch: Partial<InstallDraft>) => change({ probe: null, pluginPath: null, releases: null,
    selection: emptySelection(), busy: false, searching: false, error: null, request: current().request + 1, ...patch });
  const loadVersions = async (request: number, probe: RepositoryProbe, candidate: RepositoryPlugin) => {
    const listing = await listSideloadPlugins();
    if (!valid(request)) return;
    const existing = listing.plugins.find((p) => !p.is_self && p.name === candidate.name && (pluginReadOnly(p) || !pluginEligible(p) || p.repo?.toLowerCase() === probe.repo.toLowerCase()));
    if (existing) { sourceChanged({}); onExisting(existing); return; }
    const rows = await listReleases(probe.repo, null);
    if (!valid(request)) return;
    change({ releases: rows, selection: initialSelection(rows) }, request);
  };
  const read = async (source: string) => {
    const state = current();
    if (state.busy || state.searching || !source) return;
    const request = state.request + 1;
    change({ request, busy: true, repository: source, error: null });
    try {
      const probe = await probeRepository(source);
      if (!valid(request)) return;
      const candidate = probe.plugins.length === 1 ? probe.plugins[0] : null;
      change({ probe, pluginPath: candidate?.path ?? null }, request);
      if (candidate) await loadVersions(request, probe, candidate);
    } catch (err) { change({ error: errorText(err) }, request); }
    finally { change({ busy: false }, request); }
  };
  const selectRepository = (repo: RepositoryRow) => {
    const state = current();
    if (state.repositories?.some((row) => row.repo === repo.repo)) void read(repo.repo);
  };
  const submit = async () => {
    const state = current(), value = state.draft.trim();
    if (state.busy || state.searching || !value) return;
    if (isRepositoryAddress(value)) { await read(value); return; }
    const request = state.request + 1;
    change({ request, searching: true, repositories: null, repository: null, error: null });
    try { change({ repositories: await searchRepositories(value), searching: false }, request); }
    catch (err) { change({ error: errorText(err), searching: false }, request); }
  };
  const readVersions = async () => {
    const state = current(), candidate = state.probe?.plugins.find((p) => p.path === state.pluginPath);
    if (state.busy || !state.probe || !candidate) return;
    const request = state.request + 1;
    change({ request, busy: true, error: null });
    try { await loadVersions(request, state.probe, candidate); }
    catch (err) { change({ error: errorText(err) }, request); }
    finally { change({ busy: false }, request); }
  };
  const install = async () => {
    const state = current(), release = state.releases?.find((r) => r.tag === state.selection.tag);
    if (state.busy || preparing || isActive(operation) || !release || !state.selection.zip || !state.probe || state.pluginPath === null) return;
    const request = state.request + 1;
    change({ request, busy: true, error: null });
    try {
      await startInstallation(release, state.selection.zip, null, state.pluginPath, "install", onOperation);
    } catch (err) { change({ error: errorText(err) }, request); }
    finally { change({ busy: false }, request); }
  };
  const channel = probeLoaderChannel();
  const candidate = form.probe?.plugins.find((p) => p.path === form.pluginPath);
  const phase = form.releases ? "preview" : form.probe ? "projects" : "input";
  const changePlugin = () => {
    if (!form.busy) change({ releases: null, selection: emptySelection(), error: null, request: current().request + 1 });
  };
  return <Focusable key={phase} autoFocus flow-children="column" navEntryPreferPosition={NavEntryPositionPreferences.PREFERRED_CHILD}>
    <PanelSection><Heading title={phase === "preview" ? t("安装预览") : phase === "projects" ? t("仓库插件探查") : t("从 GitHub 安装")} />
      {phase === "input" && <>
        <RepositorySearch query={form.draft} results={form.repositories} selected={form.repository} busy={form.searching} disabled={form.busy}
          label={t("仓库地址或搜索关键词")}
          actionLabel={form.busy ? t("正在探查…") : isRepositoryAddress(form.draft.trim()) ? t("读取仓库") : t("搜索仓库")}
          onQuery={(draft) => sourceChanged({ draft, repositories: null, repository: null })}
          onSearch={() => void submit()} onSelect={selectRepository} />
        <Explanation>{t("插件会静态读取仓库信息，识别可安装的 Decky 插件。")}</Explanation>
      </>}
      {form.probe && <>
        <Status title={form.probe.repo} />
        {form.probe.plugins.length === 0 && <Status tone="warning" title={t("未确认 Decky 插件")}>{t("未找到同目录的有效 plugin.json、package.json 和 Decky 实现特征，不能继续安装。")}</Status>}
        {phase === "projects" && form.probe.plugins.length > 1 && <PanelSectionRow><DropdownItem label={t("仓库内的插件")} selectedOption={form.pluginPath} disabled={form.busy}
          strDefaultLabel={t("请选择要安装的插件")} rgOptions={form.probe.plugins.map((p) => ({ data: p.path, label: `${p.name} · ${p.path || t("根目录")}` }))}
          onChange={(option) => {
            const state = current();
            if (state.probe?.repo === form.probe?.repo && state.probe?.tree_sha === form.probe?.tree_sha && state.probe?.plugins.some((p) => p.path === option.data)) {
              change({ pluginPath: option.data, releases: null, selection: emptySelection(), error: null, request: current().request + 1 });
            }
          }} /></PanelSectionRow>}
        {candidate && <Status icon={FaPuzzlePiece} title={t("已识别：{0}", [candidate.name])}>{candidate.path || t("仓库根目录")}<br />{candidate.evidence.join("，")}</Status>}
        {(form.probe.plugins.length > 1 || !!candidate?.path) && <Status tone="warning" title={t("请核对插件对应的发布与 ZIP")}>{t("monorepo 的发布可能属于其他插件。准备安装时会校验插件名和包名，不能只凭 ZIP 文件名判断。")}</Status>}
      </>}
      {phase === "projects" && !!form.probe?.plugins.length && <Action kind="readReleases" disabled={form.busy || !candidate} onClick={() => void readVersions()}>{form.busy ? t("正在读取版本…") : t("读取版本")}</Action>}
      {phase === "preview" && form.releases && <>
        <ReleasePicker releases={form.releases} tag={form.selection.tag} zip={form.selection.zip} disabled={form.busy}
          onChange={(tag, zip) => {
            const state = current(), release = state.releases?.find((r) => r.tag === tag);
            if (valid(form.request) && release && (zip === null || release.zips.some((a) => a.url === zip))) change({ selection: { tag, zip } });
          }} />
        <Status tone="warning" title={t("安装前请核对")}>{t("第三方插件可能会有恶意代码，请甄别插件来源之后再决定安装。")}</Status>
        {candidate?.requires_root && <Status tone="warning" title={t("此插件可以完全访问你的 Steam Deck。")} />}
        <Action kind="installPackage" disabled={form.busy || preparing || isActive(operation) || !form.selection.zip || !candidate || !channel.ok} onClick={() => void install()}>{form.busy ? t("正在准备…") : t("继续安装")}</Action>
        {form.probe && form.probe.plugins.length > 1 && <Action kind="changePlugin" disabled={form.busy} onClick={changePlugin}>{t("更换插件")}</Action>}
        {!channel.ok && <Status tone="error" title={t("安装通道不可用")}>{channel.reason}</Status>}
      </>}
      {phase !== "input" && <Action kind="changeRepo" disabled={form.busy} onClick={returnToInstallSearch}>{t("更换仓库")}</Action>}
      {form.error && <Status tone="error" title={t("操作失败")}>{form.error}</Status>}
    </PanelSection>
  </Focusable>;
}
