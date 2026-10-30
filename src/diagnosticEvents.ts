import type { DiagnosticEvent } from "./types";

const own = (value: object, key: PropertyKey) => Object.prototype.hasOwnProperty.call(value, key);
export const diagnosticPages = ["home", "details", "install", "settings", "binding", "operation", "checks", "network", "diagnostics", "maintenance", "versions", "source", "releaseNotes", "licenses"] as const;
const actions = new Set(["back", "checkUpdates", "newInstall", "settings", "binding", "manageBinding", "saveBinding", "unlink", "replace", "reinstall", "uninstall", "search", "manualRepo", "returnSearch", "readReleases", "refreshReleases", "installPackage", "changePlugin", "changeRepo", "releaseNotes", "licenses", "reloadSettings", "dismissTask", "muteTaskNotice", "viewTask", "refreshTask", "refreshRecognition", "deletePackage", "confirmInstall", "shareDiagnostics", "copyDiagnostics", "stopDiagnostics", "diagnosticDetails", "exportDiagnostics", "takeover", "returnStore", "readOwnership", "cancelRequest", "finishTracking", "skipRelease", "continueInstall", "retryDownload", "maintenance", "network", "deleteBackups", "updateRepositoryMap"]);
for (const action of ["selfUpdateCheck", "selfUpdateDownload", "selfUpdateInstall", "selfUpdateCancel"]) actions.add(action);
const errorTypes = new Set(["Error", "TypeError", "RangeError", "ReferenceError", "SyntaxError", "URIError", "EvalError", "Exception"]);
const origins = new Set(["render", "action", "global", "unhandled", "toast", "icon"]);
const schemas: Record<string, { required: string[]; optional: string[] }> = {
  loader_read: { required: ["event", "ok", "count", "durationMs"], optional: ["clientId"] },
  frontend_build: { required: ["event", "fingerprint", "version", "clientId"], optional: [] },
  ui_page: { required: ["event", "page", "clientId"], optional: ["pluginId", "releaseId"] },
  ui_action: { required: ["event", "page", "action", "clientId"], optional: [] },
  frontend_error: { required: ["event", "page", "origin", "errorType", "clientId"], optional: ["line", "column"] },
};
const validField = (key: string, value: unknown): boolean => {
  if (["count", "durationMs", "line", "column"].includes(key)) return typeof value === "number" && Number.isInteger(value) && value >= 0 && value <= 2147483647;
  if (key === "ok") return typeof value === "boolean";
  if (typeof value !== "string") return false;
  if (["clientId", "pluginId", "releaseId"].includes(key)) return /^[a-f0-9]{32}$/.test(value);
  if (key === "fingerprint") return /^[a-f0-9]{64}$/.test(value);
  if (key === "version") return /^v?\d{1,4}(?:\.\d{1,4}){0,3}(?:-(?:pre|debug|beta|alpha|rc)(?:\.?\d{1,4})?)?$/.test(value);
  if (key === "page") return (diagnosticPages as readonly string[]).includes(value);
  if (key === "action") return actions.has(value);
  if (key === "origin") return origins.has(value);
  if (key === "errorType") return errorTypes.has(value);
  return key === "event" && own(schemas, value);
};

export function cleanDiagnosticEvent(value: unknown, strict = false): DiagnosticEvent | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const row = value as Record<string, unknown>;
  const schema = typeof row.event === "string" && own(schemas, row.event) ? schemas[row.event] : null;
  if (!schema || schema.required.some(key => !own(row, key))) return null;
  const keys = [...schema.required, ...schema.optional];
  if (strict && Object.keys(row).some(key => !keys.includes(key))) return null;
  const clean: Record<string, unknown> = {};
  for (const key of keys) {
    if (!own(row, key)) continue;
    if (!validField(key, row[key])) return null;
    clean[key] = row[key];
  }
  return clean as DiagnosticEvent;
}

export function frontendErrorType(error: unknown): Extract<DiagnosticEvent, { event: "frontend_error" }>["errorType"] {
  const name = error && typeof error === "object" ? (error as { name?: unknown }).name : null;
  return typeof name === "string" && errorTypes.has(name) ? name as ReturnType<typeof frontendErrorType> : "Exception";
}

export function frontendLocation(error: unknown): { line: number; column: number } | null {
  const stack = error && typeof error === "object" ? (error as { stack?: unknown }).stack : null;
  if (typeof stack !== "string") return null;
  for (const line of stack.slice(0, 16384).split("\n").slice(0, 16)) {
    const match = line.match(/((?:https?|file):\/\/[^\s)]+):(\d+):(\d+)\)?$/);
    if (!match) continue;
    try {
      const path = decodeURIComponent(new URL(match[1]).pathname);
      if (!/\/plugins\/Sideloader\/(?:dist\/)?index\.js$/.test(path)) continue;
      const location = { line: Number(match[2]), column: Number(match[3]) };
      if (validField("line", location.line) && validField("column", location.column)) return location;
    } catch { /* A malformed or unrelated URL is not a plugin stack frame. */ }
  }
  return null;
}
