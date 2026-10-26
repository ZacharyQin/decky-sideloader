import { t } from "./i18n";
import { Children, useState, type ComponentProps, type ReactNode } from "react";
import { DialogButton, DialogButtonPrimary, Field, Focusable, PanelSection as NativePanelSection, PanelSectionRow, findClassByName, updaterFieldClasses } from "@decky/ui";
import { FaCheckCircle, FaChevronDown, FaChevronRight, FaExclamationCircle, FaExclamationTriangle, FaSpinner } from "react-icons/fa";
import type { IconType } from "react-icons";
import type { CheckStatus } from "./types";
import { icons, type IconKey } from "./uiIcons";
import { recordFrontendError, recordUIAction } from "./diagnosticsController";

// Decky's section classes are private, not Steam's similarly named QAM classes.
export function PanelSection(props: ComponentProps<typeof NativePanelSection>) {
  return <div className="gss-section" data-titled={Boolean(props.title)}><NativePanelSection {...props} /></div>;
}

const decorative = { "aria-hidden": true, focusable: "false" as const, size: "1em", style: { flexShrink: 0 } };
function BusyStyle() { return <style>{`@keyframes decky-sideloader-spin { to { transform: rotate(360deg); } }
      .decky-sideloader-spinning { transform-origin: center; animation: decky-sideloader-spin 1s linear infinite; }
      @media (prefers-reduced-motion: reduce) { .decky-sideloader-spinning { animation: none; } }`}</style>; }
export function Action({ children, onClick, kind, disabled = false, loading = false, secondary = false, iconOnly = false, appearance = "primary", description, preferredFocus = false }: {
  children: ReactNode; onClick: () => void; kind: IconKey; disabled?: boolean; loading?: boolean; secondary?: boolean; iconOnly?: boolean;
  appearance?: "primary" | "quiet" | "inline" | "danger"; description?: ReactNode; preferredFocus?: boolean;
}) {
  const Icon = loading ? FaSpinner : icons[kind];
  const label = Children.toArray(children).filter((part) => typeof part === "string" || typeof part === "number").join("");
  const activate = () => {
    if (disabled || loading) return;
    recordUIAction(kind);
    try { onClick(); } catch (error) { recordFrontendError(error, "action"); throw error; }
  };
  const glyph = <Icon {...decorative} className={loading ? "decky-sideloader-spinning" : undefined} />;
  if (secondary) return <PanelSectionRow>{loading && <BusyStyle />}<Field data-action-key={kind} aria-label={label} aria-busy={loading}
    className={`gss-row gss-action-row ${appearance === "danger" ? "gss-danger" : ""}`} label={children} description={description}
    icon={<span className="gss-row-icon">{glyph}</span>} bottomSeparator="none" disabled={disabled || loading}
    focusable={!disabled && !loading} preferredFocus={preferredFocus} highlightOnFocus childrenContainerWidth="min" inlineWrap="keep-inline"
    onOKActionDescription={label} onActivate={activate} onClick={activate}><FaChevronRight className="gss-chevron" {...decorative} /></Field></PanelSectionRow>;
  const Button = appearance === "primary" ? DialogButtonPrimary : DialogButton;
  const button = <Button data-action-key={kind} aria-label={label} aria-busy={loading} onOKActionDescription={label}
    className={`gss-button gss-button-${appearance}${iconOnly ? " gss-icon-button" : ""}`} disabled={disabled || loading} onClick={activate}>
    {loading && <BusyStyle />}{glyph}{!iconOnly && <span>{children}</span>}
  </Button>;
  return appearance === "inline" || iconOnly ? button : <PanelSectionRow>{button}</PanelSectionRow>;
}

export function Heading({ title, children, icon }: { title: string; children?: ReactNode; icon?: ReactNode }) {
  return <Field className="gss-heading" focusable={false} highlightOnFocus={false} bottomSeparator="none" label={<div className={`gss-hero${icon ? " gss-plugin-hero" : ""}`}>
    {icon && <span className="gss-hero-icon">{icon}</span>}
    <div><h2>{title}</h2>{children && <p>{children}</p>}</div>
  </div>} />;
}

export function Choice({ title, description, checked, disabled, value, onSelect }: {
  title: string; description: ReactNode; checked: boolean; disabled: boolean; value: string; onSelect: () => void;
}) {
  const activate = () => { if (!disabled) onSelect(); };
  return <Focusable className="gss-choice" role="radio" aria-label={title} aria-checked={checked} aria-disabled={disabled}
    data-choice={value} tabIndex={disabled ? -1 : 0} noFocusRing focusClassName="gss-focus" onOKActionDescription={t("选择")}
    onActivate={activate} onClick={activate} onKeyDown={event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); activate(); } }}>
    <span className="gss-radio" aria-hidden /><span><strong>{title}</strong><small>{description}</small></span>
  </Focusable>;
}

export function Disclosure({ title, children }: { title: string; children: ReactNode }) {
  const [expanded, setExpanded] = useState(false);
  return <div className="gss-disclosure"><DialogButton className="gss-disclosure-toggle" aria-expanded={expanded}
    onOKActionDescription={title} onClick={() => setExpanded(!expanded)}>{title}{expanded ? <FaChevronDown {...decorative} /> : <FaChevronRight {...decorative} />}</DialogButton>
    {expanded && <div className="gss-disclosure-body">{children}</div>}
  </div>;
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
  return <PanelSectionRow>{tone === "loading" && <BusyStyle />}<Field className={`gss-status${tone ? ` gss-status-${tone}` : ""}`} bottomSeparator="none" aria-busy={tone === "loading"} label={tone ? <StatusText tone={tone}>{title}</StatusText> : title} description={children} icon={Glyph ? <Glyph {...decorative} className={tone === "loading" ? "decky-sideloader-spinning" : iconClassName} /> : undefined} focusable={false} childrenContainerWidth={action ? "min" : undefined}>{action}</Field></PanelSectionRow>;
}


export function Explanation({ children }: { children: ReactNode }) {
  return <p className="gss-note">{children}</p>;
}
