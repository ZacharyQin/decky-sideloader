import { t } from "./i18n";

export function errorText(error: unknown): string {
  if (error && typeof error === "object") {
    const remote = error as { message?: unknown; pythonTraceback?: unknown };
    if (typeof remote.message === "string" && remote.message.trim()) return remote.message;
    if (typeof remote.pythonTraceback === "string") {
      const lastLine = remote.pythonTraceback.trim().split("\n").pop()?.replace(/^[\w.]+:\s*/, "");
      if (lastLine) return lastLine;
    }
    return t("操作失败，请重试");
  }
  return String(error ?? "") || t("操作失败，请重试");
}
