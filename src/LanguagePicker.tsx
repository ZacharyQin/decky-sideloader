import { Dropdown } from "@decky/ui";
import { FaLanguage } from "react-icons/fa";
import { LocaleFlag } from "./LocaleFlag";
import { canStoreLanguage, getLanguageChoice, locales, setLocale, t, useLocale, type LanguageChoice, type Locale } from "./i18n";

const names: Record<Locale, string> = { "zh-cn": "简体中文", en: "English", pt: "Português", es: "Español", ja: "日本語", ko: "한국어" };
export function LanguagePicker() {
  useLocale();
  return <div className="gss-language">
    <Dropdown menuLabel="Language / 语言" selectedOption={getLanguageChoice()}
      renderButtonValue={() => <span className="gss-language-label"><FaLanguage aria-hidden focusable="false" /><span>Language / 语言</span></span>}
      rgOptions={[{ data: "auto", label: `Auto · ${t("跟随系统")}` }, ...locales.map(locale => ({ data: locale, label: <span style={{ display: "flex", alignItems: "center", gap: 8 }}><LocaleFlag locale={locale} />{names[locale]}</span> }))]}
      onChange={option => setLocale(option.data as LanguageChoice)} />
    {!canStoreLanguage() && <p className="gss-note">{t("无法保存语言，本次会话仍可切换。")}</p>}
  </div>;
}
