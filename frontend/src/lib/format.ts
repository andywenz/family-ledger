import { currentLang } from "./i18n";

// 金额只处理服务端返回的十进制字符串，不做浮点运算（需求 §7）。

export function money(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const negative = value.startsWith("-");
  const [int = "0", frac] = (negative ? value.slice(1) : value).split(".");
  const grouped = int.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return (negative ? "−" : "") + grouped + (frac !== undefined ? "." + frac : "");
}

export function currentMonth(tz = "Pacific/Auckland"): string {
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit" }).formatToParts(new Date());
  const y = parts.find((p) => p.type === "year")!.value;
  const m = parts.find((p) => p.type === "month")!.value;
  return `${y}-${m}`;
}

export function today(tz = "Pacific/Auckland"): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: tz }).format(new Date());
}

export function shiftMonth(month: string, delta: number): string {
  const [y = 2000, m = 1] = month.split("-").map(Number);
  const d = new Date(Date.UTC(y, m - 1 + delta, 1));
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
}

const MONTHS_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const MONTHS_EN_FULL = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

/** 2026-10 → “2026 年 10 月”／“October 2026” */
export function monthLabel(month: string): string {
  const [y, m] = month.split("-");
  return currentLang() === "en" ? `${MONTHS_EN_FULL[Number(m) - 1]} ${y}` : `${y} 年 ${Number(m)} 月`;
}

/** 2026-10-05 → “10 / 05”／“5 Oct”（英文按新西兰习惯日在前，避免月日混淆） */
export function shortDate(iso: string): string {
  if (currentLang() === "en") return `${Number(iso.slice(8, 10))} ${MONTHS_EN[Number(iso.slice(5, 7)) - 1]}`;
  return iso.slice(5).replace("-", " / ");
}

export async function sha256Hex(data: ArrayBuffer): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", data);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** 整数分值 → 两位小数字符串（纯整数运算）。 */
export function minorToDecimal(minor: number): string {
  const sign = minor < 0 ? "-" : "";
  const abs = Math.abs(Math.trunc(minor));
  return `${sign}${Math.floor(abs / 100)}.${String(abs % 100).padStart(2, "0")}`;
}

// 币种符号（同为“$”的币种加前缀区分）；未列出的币种退回代码
const SYMBOLS: Record<string, string> = {
  AUD: "A$", CAD: "C$", CNY: "¥", EUR: "€", GBP: "£", HKD: "HK$", JPY: "JP¥",
  KRW: "₩", MOP: "MOP$", NZD: "NZ$", SGD: "S$", USD: "US$",
};

export function currencySymbol(code: string): string {
  return SYMBOLS[code] ?? code + " ";
}

/** 带符号的金额，如 NZ$1,234.50、−¥80.00 */
export function cash(code: string, value: string | null | undefined): string {
  const m = money(value);
  if (m === "—") return m;
  return m.startsWith("−") ? "−" + currencySymbol(code) + m.slice(1) : currencySymbol(code) + m;
}

/** 两位小数字符串 → 整数分值（纯字符串与整数运算，不经浮点）。 */
export function decimalToMinor(value: string): number {
  const negative = value.startsWith("-");
  const [int = "0", frac = ""] = (negative ? value.slice(1) : value).split(".");
  const minor = Number(int) * 100 + Number((frac + "00").slice(0, 2));
  return negative ? -minor : minor;
}

const WEEKDAYS = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];
const WEEKDAYS_EN = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

/** 2026-10-05 → “10月5日 周一”／“Mon 5 Oct” */
export function dayLabel(iso: string): string {
  const [y = 2000, m = 1, d = 1] = iso.split("-").map(Number);
  const wd = new Date(Date.UTC(y, m - 1, d)).getUTCDay();
  return currentLang() === "en" ? `${WEEKDAYS_EN[wd]} ${d} ${MONTHS_EN[m - 1]}` : `${m}月${d}日 ${WEEKDAYS[wd]}`;
}

/** 币种名：中文用服务端的 name_zh；英文用浏览器内置的 ISO 币种名（如 New Zealand Dollar），取不到则用代码 */
export function currencyName(code: string, nameZh?: string): string {
  if (currentLang() !== "en") return nameZh || code;
  try {
    return new Intl.DisplayNames(["en"], { type: "currency" }).of(code) ?? code;
  } catch {
    return code;
  }
}

/** 汇率显示为“币种:美元 = X:1”中的 X：1 ÷ 美元值，保留两位小数（BigInt 整数运算，不经浮点）。
 *  例：港币 0.128 → "7.81"；日元 0.0067 → "149.25"。 */
export function perUsd(usdValue: string, places = 2): string {
  const [int = "0", frac = ""] = usdValue.split(".");
  const den = BigInt(int + frac);
  if (den === 0n) return "—";
  // 1 ÷ (den / 10^frac.length) = 10^frac.length / den；多算一位后四舍五入
  const scaled = (10n ** BigInt(frac.length + places + 1)) / den;
  const s = ((scaled + 5n) / 10n).toString().padStart(places + 1, "0");
  return money(`${s.slice(0, -places)}.${s.slice(-places)}`);
}
