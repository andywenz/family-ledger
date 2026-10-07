import { Fragment, createContext, useCallback, useContext, useState, type ReactNode } from "react";

/** 界面语言：每个浏览器各自记住（与界面风格一样，个人偏好，不存服务器）。
 *  文案在调用处中英并列 t("中文", "English")，不维护键表，避免漏译。
 *  用户录入的数据（分类名、备注、家庭名等）不翻译。 */
export type Lang = "zh" | "en";
const KEY = "ledger:lang";

function stored(): Lang {
  try {
    return localStorage.getItem(KEY) === "en" ? "en" : "zh";
  } catch {
    return "zh"; // 隐私模式或禁用存储
  }
}

// 非组件代码（格式化、标签、API 请求头）读取当前语言；由 LangProvider 渲染时同步更新
let current: Lang = stored();
export const currentLang = (): Lang => current;
export const tr = (zh: string, en: string): string => (current === "en" ? en : zh);
/** 英文单复数：plural(3, "entry", "entries") → "3 entries" */
export const plural = (n: number, one: string, many: string): string => `${n} ${n === 1 ? one : many}`;

export function applyLang(lang: Lang): void {
  current = lang;
  document.documentElement.lang = lang === "en" ? "en" : "zh-CN";
  document.title = lang === "en" ? "Family Ledger" : "小家账本";
}

const Ctx = createContext<{ lang: Lang; setLang: (l: Lang) => void }>({ lang: current, setLang: () => undefined });

export function LangProvider({ children }: { children: ReactNode }) {
  const [lang, setState] = useState<Lang>(current);
  current = lang; // 先于子组件渲染，使格式化函数与组件一致
  const setLang = useCallback((l: Lang) => {
    applyLang(l);
    try { localStorage.setItem(KEY, l); } catch { /* 仅本次生效 */ }
    setState(l);
  }, []);
  return <Ctx.Provider value={{ lang, setLang }}>{children}</Ctx.Provider>;
}

/** 切换语言时让页面整体重新渲染（不随语言变化的组件也会更新）；登录状态在外层，不受影响 */
export function LangKeyed({ children }: { children: ReactNode }) {
  const { lang } = useContext(Ctx);
  return <Fragment key={lang}>{children}</Fragment>;
}

/** 组件内取文案：const { t } = useT(); t("月度总览", "Overview") */
export function useT() {
  const { lang, setLang } = useContext(Ctx);
  const t = useCallback((zh: string, en: string) => (lang === "en" ? en : zh), [lang]);
  return { t, lang, setLang };
}
