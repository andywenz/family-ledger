import type React from "react";
import { plural, tr } from "../lib/i18n";
import { useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { ApiError, api, type Dashboard as DashboardData, type Entry } from "../api/client";
import { EntryDialog } from "../components/EntryDialog";
import { EntryList } from "../components/EntryList";
import { ErrorNotice, PageHeading, useLoad, useToast } from "../components/common";
import { Icon } from "../components/Icon";
import { useFamily } from "../components/Layout";
import { cash, currencyName, currencySymbol, currentMonth, money, monthLabel, shiftMonth, shortDate } from "../lib/format";
import { PHONE_QUERY, useMediaQuery } from "../lib/media";
import { entryTypes, methodLabel, methods, typeLabel } from "../lib/labels";

// 饼图配色随界面风格变化（theme.css 中的 --c1…--c9）
const PIE_COLORS = ["var(--c1)", "var(--c2)", "var(--c3)", "var(--c4)", "var(--c5)", "var(--c6)", "var(--c7)", "var(--c8)", "var(--c9)"];

// 饼图中心金额：符号与小数用小字，整数部分按长度缩放，保证落在圆孔内（--len 供 CSS 计算字号）
function DonutTotal({ code, value }: { code: string; value: string }) {
  const [int = "", frac] = money(value).split(".");
  const sym = currencySymbol(code).trim();
  const len = int.length + 0.5 * (sym.length + (frac ? frac.length + 1 : 0));
  return (
    <strong className="donut-total" style={{ "--len": len } as React.CSSProperties} aria-label={cash(code, value)}>
      <small>{sym}</small>{int}{frac !== undefined && <small>.{frac}</small>}
    </strong>
  );
}

export default function Dashboard() {
  const fam = useFamily();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const month = params.get("month") ?? currentMonth(fam.config.timezone);
  const filters = {
    created_by: params.get("created_by") ?? undefined,
    type: params.get("type") ?? undefined,
    category_id: params.get("category_id") ?? undefined,
    payment_method: params.get("payment_method") ?? undefined,
  };
  const [openEntry, setOpenEntry] = useState<Entry | null>(null);
  const toast = useToast();
  const phone = useMediaQuery(PHONE_QUERY);

  const { data, error, reload } = useLoad(async () => {
    const [summary, first] = await Promise.all([
      api.get<DashboardData>(`/families/${fam.fid}/dashboard`, { month, ...filters }),
      api.get<{ items: Entry[]; next_cursor?: string; data_version: string }>(`/families/${fam.fid}/entries`, { month, page_size: 100, ...filters }),
    ]);
    let items = first.items;
    let cursor = first.next_cursor;
    while (cursor) {
      const next = await api.get<{ items: Entry[]; next_cursor?: string }>(`/families/${fam.fid}/entries`, { month, page_size: 100, cursor, ...filters });
      items = items.concat(next.items);
      cursor = next.next_cursor;
    }
    return { summary, items };
  }, [fam.fid, month, params.toString()]);

  const setParam = (k: string, v?: string) => {
    const next = new URLSearchParams(params);
    if (v) next.set(k, v);
    else next.delete(k);
    setParams(next);
  };

  const exportMenu = useRef<HTMLDetailsElement>(null);

  async function exportFile(format: "csv" | "xlsx") {
    exportMenu.current?.removeAttribute("open");
    try {
      const job = await api.post<{ download_url?: string }>(`/families/${fam.fid}/exports`, {
        format, month, filters: Object.fromEntries(Object.entries(filters).filter(([, v]) => v)),
      });
      if (job.download_url) window.location.href = job.download_url;
    } catch (e) {
      toast.show(e instanceof ApiError ? e.message : tr("导出失败", "Export failed"));
    }
  }

  const s = data?.summary;
  // 主币种 ＝ 家庭默认币种；辅助币种（家庭设置，可选）未设置时不显示
  const prim = s?.primary;
  const sec = s?.secondary ?? null;
  const pie = prim?.category_pie;
  const sliceIndex = new Map(pie?.slices.map((sl, i) => [sl.category_id, i]) ?? []);
  const colorOf = (parentId: string) => {
    const i = sliceIndex.get(parentId);
    return i === undefined ? undefined : PIE_COLORS[i % PIE_COLORS.length];
  };
  const nameOf = (code: string) => currencyName(code, fam.currencies.find((c) => c.code === code)?.name_zh);
  let at = 0;
  const gradient = pie?.slices.map((sl, i) => {
    const start = at;
    at += Number(sl.share_percent);
    return `${PIE_COLORS[i % PIE_COLORS.length]} ${start}% ${at}%`;
  }).join(",");
  const activeFilters = Object.entries(filters).filter(([, v]) => v);

  return (
    <>
      <PageHeading
        title={tr("月度总览", "Overview")}
        subtitle={tr("这个月花在哪里、怎么付的；点任意一笔可以查看或修改。", "Where this month’s money went and how it was paid. Select any entry to view or edit it.")}
        actions={
          <>
            <div className="month-picker">
              <button className="icon-button" aria-label={tr("上个月", "Previous month")} onClick={() => setParam("month", shiftMonth(month, -1))}><Icon name="left" /></button>
              <strong>{monthLabel(month)}</strong>
              <button className="icon-button" aria-label={tr("下个月", "Next month")} onClick={() => setParam("month", shiftMonth(month, 1))}><Icon name="right" /></button>
            </div>
            <button className="button primary" onClick={() => navigate(`/f/${fam.fid}/new`)}><Icon name="plus" />{tr("记一笔", "New entry")}</button>
          </>
        }
      />
      <ErrorNotice error={error} onRetry={reload} />
      {s && (
        <>
          {/* 净支出、分类饼图、消费方式同一排（桌面）；窄屏自动换行（app.css .dashboard-top） */}
          <section className="dashboard-top" aria-label={tr("本月概览", "This month at a glance")}>
            <article className="card spending-hero">
              <div className="card-label">{tr("本月净支出", "Net spending this month")} <Icon name="wallet" /></div>
              <div className="currency-kicker">{nameOf(prim!.currency)}{tr(`（${prim!.currency}）`, ` (${prim!.currency})`)}</div>
              <div className="number"><small>{currencySymbol(prim!.currency)}</small>{money(prim!.net_expense)}</div>
              {prim!.missing_count > 0 && <div className="secondary-amount"><small>{tr(`${prim!.missing_count} 笔缺少汇率，未计入`, `${plural(prim!.missing_count, "entry", "entries")} without a rate, not included`)}</small></div>}
              {sec && (
                <div className="secondary-amount">
                  {tr("合", "≈ ")}{nameOf(sec.currency)}{tr(`（${sec.currency}）`, ` (${sec.currency})`)} {cash(sec.currency, sec.net_expense)}
                  {sec.missing_count > 0 && <small>{tr(`（${sec.missing_count} 笔缺少汇率，未计入）`, ` (${plural(sec.missing_count, "entry", "entries")} without a rate, not included)`)}</small>}
                </div>
              )}
              <div className="card-footnote"><span className="dot"></span><span className="nowrap">{tr("消费", "Spent")} {cash(prim!.currency, prim!.expense_gross)}</span> · <span className="nowrap">{tr("退款", "Refunds")} {cash(prim!.currency, prim!.refund)}</span></div>
            </article>
            <article className="card">
              <div className="section-head"><h2>{tr("钱花在了哪里", "Where the money went")}</h2><small>{tr(`一级分类 · 合 ${prim!.currency}`, `Top-level categories · ≈ ${prim!.currency}`)}</small></div>
              <div className="chart-body">
                <div className="donut" role="img" aria-label={tr("各分类净支出占比", "Share of net spending by category")} style={{ background: gradient ? `conic-gradient(${gradient})` : "#ecf0e5" }}>
                  <div className="donut-center">{tr("正净支出合计", "Net spending")}<DonutTotal code={prim!.currency} value={pie!.denominator} />{tr(`${pie!.slices.length} 个分类`, plural(pie!.slices.length, "category", "categories"))}</div>
                </div>
                <div className="legend">
                  {pie!.empty && <p className="muted">{tr("这个月没有正净支出的分类。", "No category has net spending this month.")}</p>}
                  {pie!.slices.map((sl, i) => (
                    <div key={sl.category_id} className="legend-row">
                      <i className="legend-dot" style={{ background: PIE_COLORS[i % PIE_COLORS.length] }}></i>
                      <span>{sl.name}</span><span className="legend-amount">{cash(prim!.currency, sl.net)}</span><b>{sl.share_percent}%</b>
                    </div>
                  ))}
                  {pie!.negatives.length > 0 && (
                    <div className="negatives">
                      <small>{tr("净额为负（退款多于消费），不计入饼图：", "Negative net (refunds exceed spending), not in the chart:")}</small>
                      {pie!.negatives.map((n) => <div key={n.category_id} className="legend-row"><span>{n.name}</span><b>{cash(prim!.currency, n.net)}</b></div>)}
                    </div>
                  )}
                  {!pie!.empty && <p className="tiny-note">{tr("饼图分母为所有正净支出分类之和，可能不同于净支出总额。", "Percentages are of categories with net spending, so the total can differ from net spending.")}</p>}
                </div>
              </div>
            </article>
            <article className="card">
              <div className="section-head"><h2>{tr("消费方式", "Payment methods")}</h2><small>{tr("扣除对应退款 · 不含收入", "Net of refunds · excl. income")}</small></div>
              {prim!.by_payment_method.map((m) => (
                <div key={m.payment_method} className="payment-row">
                  <div className="payment-icon"><Icon name={m.payment_method === "cash" ? "cash" : m.payment_method === "credit_card" ? "card" : "wallet"} /></div>
                  <div className="payment-label">{methodLabel(m.payment_method)}<small>{tr(`${m.expense_count} 笔消费 · ${m.refund_count} 笔退款`, `${plural(m.expense_count, "expense", "expenses")} · ${plural(m.refund_count, "refund", "refunds")}`)}</small></div>
                  <div className="payment-amount"><strong>{cash(prim!.currency, m.net)}</strong>{sec && <small>{cash(sec.currency, sec.by_payment_method.find((x) => x.payment_method === m.payment_method)?.net ?? "0")}</small>}</div>
                </div>
              ))}
            </article>
          </section>
          <section className="card ledger-card dash-ledger">
            <div className="ledger-top">
              <h2>{tr("本月明细", "This month’s entries")}<small>{tr(`${data!.items.length} 笔记录`, plural(data!.items.length, "entry", "entries"))}</small></h2>
              <div className="filters">
                <select aria-label={tr("筛选类型", "Filter by type")} value={filters.type ?? ""} onChange={(e) => setParam("type", e.target.value)}>
                  <option value="">{tr("全部类型", "Any type")}</option>
                  {entryTypes().map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                </select>
                <select aria-label={tr("筛选成员", "Filter by member")} value={filters.created_by ?? ""} onChange={(e) => setParam("created_by", e.target.value)}>
                  <option value="">{tr("所有成员", "Any member")}</option>
                  {fam.members.map((m) => <option key={m.user_id} value={m.user_id}>{m.display_name}{m.status !== "active" ? tr("（已离开）", " (left)") : ""}</option>)}
                </select>
                <select aria-label={tr("筛选分类", "Filter by category")} value={filters.category_id ?? ""} onChange={(e) => setParam("category_id", e.target.value)}>
                  <option value="">{tr("所有分类", "Any category")}</option>
                  {fam.categories.groups.map((g) => (
                    <optgroup key={g.category_id} label={g.name}>
                      <option value={g.category_id}>{g.name}{tr("（全部）", " (all)")}</option>
                      {g.children.map((c) => <option key={c.category_id} value={c.category_id}>{c.name}</option>)}
                    </optgroup>
                  ))}
                </select>
                <select aria-label={tr("筛选方式", "Filter by method")} value={filters.payment_method ?? ""} onChange={(e) => setParam("payment_method", e.target.value)}>
                  <option value="">{tr("所有方式", "Any method")}</option>
                  {methods().map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                </select>
                {/* 合并为一个“导出”按钮，展开选择格式（原下载图标与 Excel 按钮看起来重复） */}
                <details className="export-menu" ref={exportMenu}>
                  <summary className="button secondary small"><Icon name="download" />{tr("导出", "Export")}</summary>
                  <div className="export-options" role="menu">
                    <button type="button" role="menuitem" onClick={() => void exportFile("xlsx")}>Excel (.xlsx)<small>{tr("按当前月份与筛选", "Current month and filters")}</small></button>
                    <button type="button" role="menuitem" onClick={() => void exportFile("csv")}>CSV<small>{tr("通用表格格式", "Plain spreadsheet format")}</small></button>
                  </div>
                </details>
              </div>
            </div>
            {activeFilters.length > 0 && (
              <p className="filter-note">{tr("当前筛选：", "Filtered by: ")}{activeFilters.map(([k, v]) => `${filterLabels()[k]}=${labelFor(k, v!, fam)}`).join(tr("，", ", "))}{tr("。汇总、图表与导出使用同一范围。", ". Totals, charts and exports use the same filter. ")}<button className="text-button" onClick={() => setParams(new URLSearchParams({ month }))}>{tr("清除", "Clear")}</button></p>
            )}
            {phone ? (
              <EntryList items={data!.items} primary={prim!.currency} secondary={sec?.currency ?? null} colorOf={colorOf} onOpen={setOpenEntry} />
            ) : (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>{tr("日期", "Date")}</th><th>{tr("类型", "Type")}</th><th>{tr("一级分类", "Category")}</th><th>{tr("二级分类", "Subcategory")}</th><th>{tr("币种", "Currency")}</th>
                    <th className="numeric">{tr("原始金额", "Amount")}</th><th className="numeric" data-currency-role="primary">{tr("合 ", "≈ ")}{prim!.currency}</th>{sec && <th className="numeric" data-currency-role="secondary">{tr("合 ", "≈ ")}{sec.currency}</th>}
                    <th>{tr("备注", "Note")}</th><th>{tr("方式", "Method")}</th><th>{tr("录入人", "Entered by")}</th><th>{tr("操作", "Actions")}</th>
                  </tr>
                </thead>
                <tbody>
                  {data!.items.map((e) => (
                    <tr key={e.entry_id} data-entry data-type={e.type}>
                      <td>{shortDate(e.business_date)}</td>
                      <td><span className={`badge type-${e.type}`}>{typeLabel(e.type)}</span>{!e.counts_in_stats && <small className="no-stat">{tr("不计收支", "Not in totals")}</small>}</td>
                      <td>{e.category.parent_name}</td>
                      <td>{e.category.leaf_name}</td>
                      <td>{e.currency}</td>
                      <td className="numeric">{money(e.amount)}</td>
                      <td className="numeric">{e.primary ? money(e.primary.amount) : "—"}</td>
                      {sec && <td className="numeric">{e.secondary ? money(e.secondary.amount) : "—"}</td>}
                      <td className="note-cell">{e.note}{e.attachments.length > 0 && <span title={tr("有照片", "Has photo")}> 📎</span>}</td>
                      <td>{e.payment_method ? methodLabel(e.payment_method) : "—"}</td>
                      <td>{e.created_by_display}{e.created_by_left && <small>{tr("（已离开）", " (left)")}</small>}{e.source === "import" && <small>{tr("（历史导入）", " (imported)")}</small>}</td>
                      <td>
                        <div className="row-actions">
                          <button className="edit-row" onClick={() => setOpenEntry(e)} aria-label={tr(`查看或编辑 ${e.note || e.category.leaf_name}`, `View or edit ${e.note || e.category.leaf_name}`)}><Icon name="edit" />{e.can_edit ? tr("编辑", "Edit") : tr("查看", "View")}</button>
                        </div>
                      </td>
                    </tr>
                  ))}
                  {data!.items.length === 0 && <tr><td colSpan={12} className="empty">{tr("还没有符合条件的记录。", "No matching entries yet.")}</td></tr>}
                </tbody>
              </table>
            </div>
            )}
            <div className="ledger-bottom"><span>{tr("按业务日期从新到旧 · 金额为记账时的汇率快照", "Newest first · amounts use the exchange rate at the time of entry")}</span><span>{tr(`共 ${data!.items.length} 笔`, `${data!.items.length} total`)}</span></div>
          </section>
        </>
      )}
      {openEntry && (
        <EntryDialog entry={openEntry} onClose={() => setOpenEntry(null)} onChanged={(msg, moved) => {
          setOpenEntry(null);
          toast.show(msg);
          if (moved && moved !== month) setParam("month", moved);
          else void reload();
        }} />
      )}
      {toast.node}
    </>
  );
}

const filterLabels = (): Record<string, string> => ({
  created_by: tr("录入人", "Entered by"), type: tr("类型", "Type"), category_id: tr("分类", "Category"), payment_method: tr("方式", "Method"),
});

function labelFor(k: string, v: string, fam: ReturnType<typeof useFamily>): string {
  if (k === "type") return typeLabel(v);
  if (k === "payment_method") return methodLabel(v);
  if (k === "created_by") return fam.members.find((m) => m.user_id === v)?.display_name ?? v;
  for (const g of fam.categories.groups) {
    if (g.category_id === v) return g.name;
    const c = g.children.find((x) => x.category_id === v);
    if (c) return `${g.name}／${c.name}`;
  }
  return v;
}
