import { useState } from "react";
import { THEMES, getTheme, saveTheme, type ThemeId } from "../lib/theme";

/** 右上角风格切换：果汁色块／夜光／手账波普。 */
export function ThemeSwitcher() {
  const [current, setCurrent] = useState<ThemeId>(getTheme());
  return (
    <div className="theme-switch" role="group" aria-label="界面风格">
      {THEMES.map((t) => (
        <button key={t.id} type="button" aria-pressed={current === t.id} title={t.label}
          onClick={() => { saveTheme(t.id); setCurrent(t.id); }}>
          <span aria-hidden="true">{t.icon}</span><span className="theme-label">{t.label}</span>
        </button>
      ))}
    </div>
  );
}
