import { DialogButton, Field, Navigation } from "@decky/ui";
import { createElement, useMemo, type ReactNode } from "react";
import Markdown, { defaultUrlTransform } from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeRaw from "rehype-raw";
import rehypeSanitize from "rehype-sanitize";
import { Action, Heading, PanelSection } from "./components";
import { t } from "./i18n";
import type { ReleaseRow } from "./types";
import { updateViewState, useViewState } from "./viewState";

const remarkPlugins = [remarkGfm];
const rehypePlugins = [rehypeRaw, rehypeSanitize];

function ReleaseMarkdown({ release, preview = false }: { release: ReleaseRow; preview?: boolean }) {
  const openLinkLabel = t("打开链接");
  return useMemo(() => {
    const source = `https://github.com/${release.repo}/blob/${encodeURIComponent(release.tag)}/`;
    const raw = `https://raw.githubusercontent.com/${release.repo}/${encodeURIComponent(release.tag)}/`;
    const readable = (children: ReactNode) => preview ? children : <Field className="gss-markdown-block" label={children} focusable highlightOnFocus={false} bottomSeparator="none" />;
    const block = (tag: "p" | "h1" | "h2" | "h3" | "h4" | "h5" | "h6" | "pre" | "table") =>
      ({ children }: { children?: ReactNode }) => readable(createElement(tag, null, children));
    return <Markdown remarkPlugins={remarkPlugins} rehypePlugins={rehypePlugins}
      urlTransform={(value, key) => {
        const safe = defaultUrlTransform(value);
        if (!safe) return "";
        try {
          const url = new URL(safe, key === "src" ? raw : source);
          return ["https:", "http:"].includes(url.protocol) || key === "href" && url.protocol === "mailto:" ? url.href : "";
        } catch { return ""; }
      }}
      components={{
        p: block("p"), h1: block("h1"), h2: block("h2"), h3: block("h3"), h4: block("h4"), h5: block("h5"), h6: block("h6"), pre: block("pre"), table: block("table"),
        li: ({ children }) => <li>{readable(children)}</li>,
        a: ({ href, children }) => preview || !href ? <span className="gss-markdown-link">{children}</span> :
          <DialogButton className="gss-markdown-link" onOKActionDescription={openLinkLabel} onClick={() => Navigation.NavigateToExternalWeb(href)}>{children}</DialogButton>,
        img: ({ src, alt, title, width, height }) => src ? <img src={src} alt={alt ?? ""} title={title} width={width} height={height} loading="lazy" referrerPolicy="no-referrer" /> : null,
        details: ({ children }) => <div>{children}</div>,
        summary: ({ children }) => <strong>{children}</strong>,
      }}>{release.body}</Markdown>;
  }, [release.body, release.repo, release.tag, preview, openLinkLabel]);
}

export function ReleaseNotesPreview({ release }: { release: ReleaseRow }) {
  const { releaseNotesFocus } = useViewState();
  const hasNotes = Boolean(release.body.trim());
  return <>
    <div className="gss-release-notes gss-release-notes-preview" aria-label={t("发布说明")}>
      {hasNotes ? <ReleaseMarkdown release={release} preview /> : t("作者未提供发布说明")}
    </div>
    {hasNotes && <Action secondary kind="releaseNotes" preferredFocus={releaseNotesFocus} onClick={() => updateViewState(current => ({
      ...current, page: "releaseNotes", releaseNotes: { release, origin: current.page === "install" ? "install" : "details" }, releaseNotesFocus: false,
    }))}>{t("查看完整发布说明")}</Action>}
  </>;
}

export function ReleaseNotesPage({ release }: { release: ReleaseRow }) {
  return <PanelSection>
    <Heading title={t("发布说明")}>{release.repo} · {release.tag}</Heading>
    <div className="gss-release-notes gss-release-notes-full" aria-label={t("发布说明")}><ReleaseMarkdown release={release} /></div>
  </PanelSection>;
}
