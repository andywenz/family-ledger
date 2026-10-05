// 小家账本标志：出檐的屋顶 + 账本横格 + 一枚硬币（家 · 记账 · 钱）。
// 颜色取自当前界面风格（theme.css 的 --logo-*）；浏览器标签图标见 public/favicon.svg。
export function Logo({ className = "brand-logo" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 32 32" aria-hidden="true" focusable="false">
      <rect width="32" height="32" rx="8" fill="var(--logo-tile)" />
      <path d="M9 14.6V24a1.6 1.6 0 0 0 1.6 1.6h10.8A1.6 1.6 0 0 0 23 24v-9.4" fill="none" stroke="var(--logo-ink)" strokeWidth="2.4" strokeLinejoin="round" />
      <path d="M5.6 15.4 16 6.6l10.4 8.8" fill="none" stroke="var(--logo-ink)" strokeWidth="3.2" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M12.4 16.6h7.2M12.4 20h3.6" stroke="var(--logo-ink)" strokeWidth="2" strokeLinecap="round" />
      <circle cx="19.4" cy="21.3" r="2.5" fill="var(--logo-coin)" />
    </svg>
  );
}
