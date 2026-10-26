import { Component, cloneElement, Fragment, isValidElement, type ReactElement } from "react";
import { FaPuzzlePiece } from "react-icons/fa";
import type { PluginRow } from "./types";
import { recordFrontendError } from "./diagnosticsController";

function loadedIcon(plugin: PluginRow): ReactElement | null {
  if (["ambiguous", "disabled", "unregistered"].includes(plugin.management?.state ?? "")) return null;
  try {
    const entries = (window as Window & { DeckyPluginLoader?: { plugins?: { name: string; icon?: unknown }[] } }).DeckyPluginLoader?.plugins;
    if (!Array.isArray(entries)) return null;
    const matches = entries.filter((entry) => entry.name === plugin.name);
    return matches.length === 1 && isValidElement(matches[0].icon) ? matches[0].icon : null;
  } catch { return null; }
}

class IconBoundary extends Component<{ icon: ReactElement | null }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(error: unknown) { recordFrontendError(error, "icon"); }
  componentDidUpdate(previous: Readonly<{ icon: ReactElement | null }>) {
    if (previous.icon !== this.props.icon && this.state.failed) this.setState({ failed: false });
  }
  render() {
    const icon = this.props.icon;
    if (this.state.failed || !icon) return <FaPuzzlePiece aria-hidden focusable="false" />;
    return icon.type === Fragment ? icon : cloneElement(icon, { "aria-hidden": true, focusable: "false", tabIndex: -1 } as Record<string, unknown>);
  }
}

export function PluginIcon({ plugin }: { plugin: PluginRow }) {
  return <span aria-hidden inert tabIndex={-1} data-plugin-icon={plugin.name} style={{ display: "inline-flex", pointerEvents: "none" }}>
    <IconBoundary icon={loadedIcon(plugin)} />
  </span>;
}
