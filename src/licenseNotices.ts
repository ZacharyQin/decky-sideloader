import type { LicenseEntry } from "./types";

export function decodeLicenseNotices(value: unknown): { entries: LicenseEntry[] } {
  if (!value || typeof value !== "object" || !Array.isArray((value as { entries?: unknown }).entries)) throw new Error("Invalid license materials");
  const { entries, texts } = value as { entries: LicenseEntry[]; texts?: Record<string, unknown> };
  return { entries: entries.map(entry => {
    if (!entry || !Array.isArray(entry.documents)) throw new Error("Invalid license documents");
    return { ...entry, documents: entry.documents.map(doc => {
      if (!doc || typeof doc.title !== "string") throw new Error("Invalid license document");
      if (typeof doc.text === "string" && doc.text.trim()) return doc;
      const ref = (doc as { ref?: unknown }).ref;
      const text = typeof ref === "string" && texts && Object.prototype.hasOwnProperty.call(texts, ref) ? texts[ref] : undefined;
      if (typeof text !== "string" || !text.trim()) throw new Error("Missing license text");
      return { title: doc.title, text };
    }) };
  }) };
}
