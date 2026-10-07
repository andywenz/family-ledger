/** 界面风格：每个浏览器各自记住（个人偏好，不存服务器）。存储不可用时回到默认风格。 */
export const THEMES = [
  { id: "juicy", label: "果汁色块", labelEn: "Juicy", icon: "🍑" },
  { id: "night", label: "夜光", labelEn: "Night", icon: "🌙" },
  { id: "pop", label: "手账波普", labelEn: "Pop", icon: "🎨" },
] as const;
export type ThemeId = (typeof THEMES)[number]["id"];
const KEY = "ledger:theme";
const DEFAULT: ThemeId = "juicy";

export function getTheme(): ThemeId {
  try {
    const v = localStorage.getItem(KEY);
    if (THEMES.some((t) => t.id === v)) return v as ThemeId;
  } catch { /* 隐私模式或禁用存储 */ }
  return DEFAULT;
}

export function applyTheme(id: ThemeId): void {
  document.documentElement.dataset.theme = id;
  document.querySelector('meta[name="color-scheme"]')?.setAttribute("content", id === "night" ? "dark" : "light");
}

export function saveTheme(id: ThemeId): void {
  applyTheme(id);
  try { localStorage.setItem(KEY, id); } catch { /* 仅本次生效 */ }
}
