import { useState } from "react";
import { useT } from "../lib/i18n";
import { THEMES, getTheme, saveTheme, type ThemeId } from "../lib/theme";

/** 右上角风格切换：果汁色块／夜光／手账波普。 */
export function ThemeSwitcher() {
  const { t } = useT();
  const [current, setCurrent] = useState<ThemeId>(getTheme());
  const themeName = (th: (typeof THEMES)[number]) => t(th.label, th.labelEn);
  return (
    <div className="theme-switch" role="group" aria-label={t("界面风格", "Theme")}>
      {THEMES.map((th) => (
        <button key={th.id} type="button" aria-pressed={current === th.id} title={themeName(th)}
          onClick={() => { saveTheme(th.id); setCurrent(th.id); }}>
          <span aria-hidden="true">{th.icon}</span><span className="theme-label">{themeName(th)}</span>
        </button>
      ))}
    </div>
  );
}
