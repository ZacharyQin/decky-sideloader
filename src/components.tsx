// Semantic index:
// - participant: docs/upstream-conflict/sideload/plugin-management.md -- animate busy native actions and status decoration with reduced-motion support and no extra focus targets.
import { Children, type ReactNode } from "react";
import { DialogButton, Field, PanelSectionRow, findClassByName, updaterFieldClasses } from "@decky/ui";
import { FaCheckCircle, FaExclamationCircle, FaExclamationTriangle, FaSpinner } from "react-icons/fa";
import type { IconType } from "react-icons";
import type { CheckStatus } from "./types";
import { icons, type IconKey } from "./uiIcons";

const decorative = { "aria-hidden": true, focusable: "false" as const, size: "1em", style: { flexShrink: 0 } };
function BusyStyle() { return <style>{`@keyframes github-sideload-spin { to { transform: rotate(360deg); } }
      .github-sideload-spinning { transform-origin: center; animation: github-sideload-spin 1s linear infinite; }
      @media (prefers-reduced-motion: reduce) { .github-sideload-spinning { animation: none; } }`}</style>; }
export function Action({ children, onClick, kind, disabled = false, loading = false, secondary = false, iconOnly = false }: {
  children: ReactNode; onClick: () => void; kind: IconKey; disabled?: boolean; loading?: boolean; secondary?: boolean; iconOnly?: boolean;
}) {
  const Icon = loading ? FaSpinner : icons[kind];
  const label = Children.toArray(children).filter((part) => typeof part === "string" || typeof part === "number").join("");
  if (iconOnly) return <DialogButton data-action-key={kind} aria-label={label} aria-busy={loading}
    onOKActionDescription={label} disabled={disabled || loading} style={{ minWidth: 0, width: "auto", padding: "0.5em" }}
    onClick={() => { if (!disabled && !loading) onClick(); }}>
    {loading && <BusyStyle />}<Icon {...decorative} className={loading ? "github-sideload-spinning" : undefined} />
  </DialogButton>;
  if (secondary) {
    const activate = () => { if (!disabled && !loading) onClick(); };
    return <PanelSectionRow>{loading && <BusyStyle />}<Field data-action-key={kind} aria-label={label} aria-busy={loading}
      label={children} icon={<Icon {...decorative} className={loading ? "github-sideload-spinning" : undefined} />} bottomSeparator="none" disabled={disabled || loading}
      focusable={!disabled && !loading} highlightOnFocus childrenContainerWidth="min" inlineWrap="keep-inline"
      onOKActionDescription={label} onActivate={activate} onClick={activate} /></PanelSectionRow>;
  }
  return <PanelSectionRow><Field childrenLayout="below" spacingBetweenLabelAndChild="none" focusable={false}>
    {loading && <BusyStyle />}
    <DialogButton data-action-key={kind} aria-label={label} aria-busy={loading} disabled={disabled || loading}
      style={{ width: "100%" }} onClick={() => { if (!disabled && !loading) onClick(); }}>
      <span style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", gap: "0.5em" }}><Icon {...decorative} className={loading ? "github-sideload-spinning" : undefined} /><span style={{ whiteSpace: "normal", overflowWrap: "anywhere" }}>{children}</span></span>
    </DialogButton>
  </Field></PanelSectionRow>;
}

export type StatusTone = "loading" | "success" | "warning" | "error" | "muted";
export const checkTone = (status: CheckStatus): StatusTone => status === "update" || status === "current" || status === "ahead" ? "success" : status === "error" ? "error" : status === "unbound" ? "muted" : "warning";
export function StatusText({ tone, children }: { tone: StatusTone; children: ReactNode }) {
  const className = tone === "success" ? updaterFieldClasses.UpdaterFieldStatusSuccess
    : tone === "error" ? findClassByName("ErrorText") : tone === "warning" ? findClassByName("Yellow") : undefined;
  return <span data-status-tone={tone} className={className || undefined}>{children}</span>;
}

const statusIcons = { loading: FaSpinner, success: FaCheckCircle, warning: FaExclamationTriangle, error: FaExclamationCircle };
export function Status({ title, children, tone, icon: Icon, iconClassName, action }: { title: string; children?: ReactNode; tone?: keyof typeof statusIcons; icon?: IconType; iconClassName?: string; action?: ReactNode }) {
  const Glyph = Icon ?? (tone ? statusIcons[tone] : null);
  return <PanelSectionRow>{tone === "loading" && <BusyStyle />}<Field aria-busy={tone === "loading"} label={tone ? <StatusText tone={tone}>{title}</StatusText> : title} description={children} icon={Glyph ? <Glyph {...decorative} className={tone === "loading" ? "github-sideload-spinning" : iconClassName} /> : undefined} focusable={false} childrenContainerWidth={action ? "min" : undefined}>{action}</Field></PanelSectionRow>;
}

export function errorText(error: unknown): string {
  if (error && typeof error === "object") {
    const remote = error as { message?: unknown; pythonTraceback?: unknown };
    if (typeof remote.message === "string" && remote.message.trim()) return remote.message;
    // Loader's Python exceptions have an empty message and carry the cause here.
    if (typeof remote.pythonTraceback === "string") {
      const lastLine = remote.pythonTraceback.trim().split("\n").pop()?.replace(/^[\w.]+:\s*/, "");
      if (lastLine) return lastLine;
    }
    return "操作失败，请重试";
  }
  return String(error ?? "") || "操作失败，请重试";
}

export function Explanation({ children }: { children: ReactNode }) {
  return <PanelSectionRow><Field description={children} focusable={false} bottomSeparator="none" /></PanelSectionRow>;
}
