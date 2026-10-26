import { t } from "./i18n";
import { Field, PanelSectionRow } from "@decky/ui";
import { useEffect, useRef, type ReactNode } from "react";
import { FaChevronRight } from "react-icons/fa";
import { PluginIcon } from "./pluginIcons";
import { refreshPlugins, runCheck, useChecks, usePluginReleases } from "./checkController";
import { Action, Heading, PanelSection, Status, StatusText, checkTone } from "./components";
import type { CheckReport, PluginRow } from "./types";
import { updateIcon as UpdateIcon, updateClassName } from "./uiIcons";
import { checkFor } from "./viewModel";
import { managementReason, pluginKey, pluginReadOnly, pluginEligible, pluginCanTakeOver } from "./loaderManagement";
import { LanguagePicker } from "./LanguagePicker";

function isStoreEntry(p: PluginRow) {
  return pluginCanTakeOver(p) && !pluginEligible(p) && !pluginReadOnly(p) && p.ownership?.state !== "pending" && !p.memory_changed && !p.management?.versionMismatch && !["ambiguous", "unregistered", "disabled"].includes(p.management?.state ?? "");
}
function groupFor(p: PluginRow, report: CheckReport | null) {
  const result = checkFor(p, report);
  if (result?.status === "update") return "updates";
  if (!pluginEligible(p) || !p.repo && p.repo_source !== "disabled" || managementReason(p) || p.management?.versionMismatch || result && ["error", "uncomparable", "no_asset", "no_release", "unbound"].includes(result.status)) return "attention";
  return "quiet";
}
function PluginDescription({ plugin, report }: { plugin: PluginRow; report: CheckReport | null }) {
  const { snapshot } = usePluginReleases(plugin);
  if (pluginReadOnly(plugin)) return managementReason(plugin);
  if (!pluginEligible(plugin)) return plugin.memory_changed ? t("身份已变化，需重新确认") : plugin.ownership?.state === "pending" ? t("接管结果未确认") : pluginCanTakeOver(plugin) ? t("由商店负责") : t("归属待确认");
  const result = checkFor(plugin, report);
  const latest = snapshot?.releases.find(release => release.has_zip) ?? snapshot?.releases[0];
  const skipped = (result?.status === "current" || result?.status === "ahead") && Boolean(plugin.skipped_tags?.length) &&
    (!snapshot || Boolean(latest && plugin.skipped_tags?.includes(latest.tag)));
  const status = !plugin.repo ? plugin.repo_source === "disabled" ? t("已停止更新追踪") : t("需补充 GitHub")
    : !result ? t("尚未检查更新") : skipped ? t(snapshot ? "已跳过最新版本" : "已跳过版本")
    : { update: t("可更新"), current: t("已是最新"), ahead: t("本机版本较新"), uncomparable: t("版本无法比较"), unbound: t("需补充 GitHub"), no_release: t("仓库没有发布版本"), no_asset: t("没有安装包"), error: t("暂时无法检查更新") }[result.status];
  return <>{plugin.version ?? t("未知版本")}{result?.status === "update" ? <> → {result.latest} · <StatusText tone="success"><UpdateIcon aria-hidden focusable="false" className={updateClassName} /> {t("可更新")}</StatusText></> : <> · <StatusText tone={skipped ? "warning" : !result ? "muted" : checkTone(result.status)}>{status}</StatusText></>}{plugin.management?.versionMismatch ? t(" · 版本记录不一致") : ""}</>;
}
export function Home({ onOpen, selected, notice, actions }: { onOpen: (plugin: PluginRow) => void; selected: string | null; notice?: ReactNode; actions?: ReactNode }) {
  const { list, checking: busy, refreshing, listError, checkError, error, revision } = useChecks();
  const grouping = useRef(new Map<string, string>());
  const groupedRevision = useRef(revision);
  const report = list?.last_check ?? null;
  useEffect(() => { void refreshPlugins(); }, []);
  if (groupedRevision.current !== revision) { grouping.current.clear(); groupedRevision.current = revision; }
  const plugins = list?.plugins.filter(p => !p.is_self && !isStoreEntry(p)) ?? [];
  const updates = plugins.filter(p => checkFor(p, report)?.status === "update");
  const unchecked = plugins.some(p => pluginEligible(p) && p.repo && !checkFor(p, report));
  const attention = plugins.filter(p => groupFor(p, report) === "attention");
  const groups = [{ id: "updates", title: t("可更新") }, { id: "attention", title: t("需要处理") }, { id: "quiet", title: t("无需操作") }];
  for (const p of plugins) if (!grouping.current.has(pluginKey(p))) grouping.current.set(pluginKey(p), groupFor(p, report));
  return <>
    <PanelSection>
      <Heading title={report?.error ? t("检查失败") : updates.length ? t("{0} 个插件可更新", [updates.length]) : attention.length ? t("有插件需要处理") : report && !unchecked ? t("没有待处理的更新") : t("尚未检查更新")}>
        {report?.error ?? (attention.length ? t("先处理需要关注的插件，其余无需操作。") : updates.length ? t("选择插件查看更新内容，安装由你确认。") : t("在这里管理 GitHub 插件的版本与更新。"))}
      </Heading>
      <div className="gss-check-line"><span>{report ? t("上次检查：{0}", [new Date(report.checkedAt).toLocaleString()]) : t("关联 GitHub 后可检查版本")}</span>
        <Action appearance="inline" kind="checkUpdates" loading={busy} onClick={() => void runCheck()} disabled={busy || refreshing}>{busy ? t("正在检查…") : t("检查更新")}</Action>
      </div>
      {notice}
      {checkError && <Status tone="error" title={t("检查失败")}>{checkError}</Status>}
      {listError && <Status tone="error" title={t("刷新失败")}>{listError}</Status>}
      {list?.loader && !list.loader.ok && <Status tone="warning" title={t("插件列表读取失败")}>{list.loader.error}</Status>}
      {!list && !error && <Status tone="loading" title={t("正在读取插件…")} />}
      {actions}
    </PanelSection>
    {groups.map(group => {
      const items = plugins.filter(p => grouping.current.get(pluginKey(p)) === group.id);
      // Never move the focused row on background checks, or retain an obsolete group claim.
      const heading = items.some(p => groupFor(p, report) !== group.id) ? t("检查结果") : group.title;
      return items.length > 0 && <div className="gss-plugin-group" key={group.id}><PanelSection title={heading}>
        <span className="gss-section-count">{items.length}</span>{items.map(plugin => <PanelSectionRow key={pluginKey(plugin)}>
        <Field className="gss-row" label={plugin.name} icon={<PluginIcon plugin={plugin} />} description={<PluginDescription plugin={plugin} report={report} />} focusable highlightOnFocus preferredFocus={pluginKey(plugin) === selected}
          bottomSeparator="none" childrenContainerWidth="min" inlineWrap="keep-inline"
          onOKActionDescription={t("查看详情")} onActivate={() => onOpen(plugin)} onClick={() => onOpen(plugin)}><FaChevronRight className="gss-chevron" aria-hidden focusable="false" /></Field>
      </PanelSectionRow>)}</PanelSection></div>;
    })}
    {list?.store_ok && plugins.length === 0 && <PanelSection><Status title={t("尚无可管理的插件")}>{t("可从 GitHub 安装，或在设置中接管商店插件。")}</Status></PanelSection>}
    <LanguagePicker />
  </>;
}
export function StorePlugins({ onOpen, selected }: { onOpen: (plugin: PluginRow) => void; selected: string | null }) {
  const { list, error } = useChecks();
  useEffect(() => { void refreshPlugins(); }, []);
  const plugins = list?.plugins.filter(p => !p.is_self && isStoreEntry(p)) ?? [];
  return <PanelSection>
    <Heading title={t("商店插件")}>{t("默认仍由商店负责更新")}</Heading>
    <Status tone="warning" title={t("接管商店插件")}>{t("只有需要改用 GitHub 版本时才接管。接管后暂停此插件的商店更新检查，之后仍可交还商店。")}</Status>
    {!list && !error && <Status tone="loading" title={t("正在读取插件…")} />}
    {error && <Status tone="error" title={t("读取失败")}>{error}</Status>}
    {list && !plugins.length && <Status title={t("没有可接管的商店插件")} />}
    {plugins.map(plugin => <PanelSectionRow key={pluginKey(plugin)}><Field className="gss-row" label={plugin.name} description={t("由商店负责")} icon={<PluginIcon plugin={plugin} />} focusable highlightOnFocus preferredFocus={pluginKey(plugin) === selected}
      bottomSeparator="none" childrenContainerWidth="min" inlineWrap="keep-inline"
      onOKActionDescription={t("查看接管")} onActivate={() => onOpen(plugin)} onClick={() => onOpen(plugin)}><FaChevronRight className="gss-chevron" aria-hidden focusable="false" /></Field></PanelSectionRow>)}
  </PanelSection>;
}
