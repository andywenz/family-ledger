import { useT, type Lang } from "../lib/i18n";

// 右上角语言切换（位于风格切换左侧）。按钮各用本语言书写，任一界面语言下都认得出
const LANGS: { id: Lang; label: string; name: string; htmlLang: string }[] = [
  { id: "zh", label: "中", name: "中文", htmlLang: "zh-CN" },
  { id: "en", label: "EN", name: "English", htmlLang: "en" },
];

export function LangSwitcher() {
  const { t, lang, setLang } = useT();
  return (
    <div className="theme-switch lang-switch" role="group" aria-label={t("界面语言", "Language")}>
      {LANGS.map((l) => (
        <button key={l.id} type="button" lang={l.htmlLang} aria-pressed={lang === l.id} aria-label={l.name} title={l.name} onClick={() => setLang(l.id)}>
          {l.label}
        </button>
      ))}
    </div>
  );
}
