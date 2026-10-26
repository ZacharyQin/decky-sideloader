import { Component, type ReactNode } from "react";
import { recordFrontendError } from "./diagnosticsController";

// Report only our subtree, then preserve Decky's existing error handling.
export class DiagnosticBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(error: unknown) {
    recordFrontendError(error, "render");
    throw error;
  }
  render() { return this.state.failed ? null : this.props.children; }
}
