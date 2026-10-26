import { useSyncExternalStore } from "react";
import { messages, type Message } from "./locales";

export const locales = ["zh-cn", "en", "pt", "es", "ja", "ko"] as const;
export type Locale = typeof locales[number];
export type LanguageChoice = Locale | "auto";
const storageKey = "decky-sideloader.language";
const listeners = new Set<() => void>();
let storageAvailable = true;
function loadChoice(): LanguageChoice {
  try {
    const saved = globalThis.localStorage?.getItem(storageKey);
    return locales.includes(saved as Locale) ? saved as Locale : "auto";
  } catch { storageAvailable = false; return "auto"; }
}
let choice = loadChoice();
export function detectLocale(): Locale {
  const hints: string[] = [];
  if (typeof window !== "undefined") {
    const manager = (window as Window & { LocalizationManager?: { m_strLocale?: string; m_code?: string } }).LocalizationManager;
    hints.push(new URLSearchParams(window.location?.search ?? "").get("LANGUAGE") ?? "", manager?.m_strLocale ?? "", manager?.m_code ?? "");
  }
  if (typeof document !== "undefined") hints.push(document.documentElement.lang);
  if (typeof navigator !== "undefined") hints.push(navigator.language, ...(navigator.languages ?? []));
  for (const hint of hints) {
    if (typeof hint !== "string" || !hint) continue;
    const language = hint.toLowerCase();
    if (/^(schinese|tchinese|zh)(-|_|$)/.test(language)) return "zh-cn";
    if (/^(english|en)(-|_|$)/.test(language)) return "en";
    if (/^(portuguese|brazilian|pt)(-|_|$)/.test(language)) return "pt";
    if (/^(spanish|latam|es)(-|_|$)/.test(language)) return "es";
    if (/^(japanese|ja)(-|_|$)/.test(language)) return "ja";
    if (/^(koreana|korean|ko)(-|_|$)/.test(language)) return "ko";
  }
  return "en";
}
export const getLocale = (): Locale => choice === "auto" ? detectLocale() : choice;
export const getLanguageChoice = () => choice;
export const canStoreLanguage = () => storageAvailable;
export function setLocale(next: LanguageChoice) {
  choice = next;
  try {
    if (!globalThis.localStorage) throw new Error("Storage unavailable");
    if (next === "auto") localStorage.removeItem(storageKey); else localStorage.setItem(storageKey, next);
    storageAvailable = true;
  } catch { storageAvailable = false; }
  for (const listener of listeners) listener();
}
function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
const snapshot = () => `${choice}:${getLocale()}:${storageAvailable}`;
export function useLocale() { useSyncExternalStore(subscribe, snapshot, snapshot); return getLocale(); }
export function t(source: Message, values: readonly unknown[] = []): string {
  const locale = getLocale();
  const text = locale === "zh-cn" ? source : messages[source][locales.indexOf(locale) - 1];
  return text.replace(/\{(\d+)\}/g, (match, index: string) => Number(index) < values.length ? String(values[Number(index)]) : match);
}
