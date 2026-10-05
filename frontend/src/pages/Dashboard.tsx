import type React from "react";
import { useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { ApiError, api, type Dashboard as DashboardData, type Entry } from "../api/client";
import { EntryDialog } from "../components/EntryDialog";
import { EntryList } from "../components/EntryList";
import { ErrorNotice, PageHeading, useLoad, useToast } from "../components/common";
import { Icon } from "../components/Icon";
import { useFamily } from "../components/Layout";
import { cash, currencySymbol, currentMonth, money, monthLabel, shiftMonth, shortDate } from "../lib/format";
import { PHONE_QUERY, useMediaQuery } from "../lib/media";
import { ENTRY_TYPES, METHODS, METHOD_LABEL, TYPE_LABEL } from "../lib/labels";

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
      toast.show(e instanceof ApiError ? e.message : "导出失败");
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
  const currencyName = (code: string) => fam.currencies.find((c) => c.code === code)?.name_zh ?? code;
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
        title="月度总览"
        subtitle="这个月花在哪里、怎么付的；点任意一笔可以查看或修改。"
        actions={
          <>
            <div className="month-picker">
              <button className="icon-button" aria-label="上个月" onClick={() => setParam("month", shiftMonth(month, -1))}><Icon name="left" /></button>
              <strong>{monthLabel(month)}</strong>
              <button className="icon-button" aria-label="下个月" onClick={() => setParam("month", shiftMonth(month, 1))}><Icon name="right" /></button>
            </div>
            <button className="button primary" onClick={() => navigate(`/f/${fam.fid}/new`)}><Icon name="plus" />记一笔</button>
          </>
        }
      />
      <ErrorNotice error={error} onRetry={reload} />
      {s && (
        <>
          {/* 净支出、分类饼图、消费方式同一排（桌面）；窄屏自动换行（app.css .dashboard-top） */}
          <section className="dashboard-top" aria-label="本月概览">
            <article className="card spending-hero">
              <div className="card-label">本月净支出 <Icon name="wallet" /></div>
              <div className="currency-kicker">{currencyName(prim!.currency)}（{prim!.currency}）</div>
              <div className="number"><small>{currencySymbol(prim!.currency)}</small>{money(prim!.net_expense)}</div>
              {prim!.missing_count > 0 && <div className="secondary-amount"><small>{prim!.missing_count} 笔缺少汇率，未计入</small></div>}
              {sec && (
                <div className="secondary-amount">
                  合{currencyName(sec.currency)}（{sec.currency}） {cash(sec.currency, sec.net_expense)}
                  {sec.missing_count > 0 && <small>（{sec.missing_count} 笔缺少汇率，未计入）</small>}
                </div>
              )}
              <div className="card-footnote"><span className="dot"></span><span className="nowrap">消费 {cash(prim!.currency, prim!.expense_gross)}</span> · <span className="nowrap">退款 {cash(prim!.currency, prim!.refund)}</span></div>
            </article>
            <article className="card">
              <div className="section-head"><h2>钱花在了哪里</h2><small>一级分类 · 合 {prim!.currency}</small></div>
              <div className="chart-body">
                <div className="donut" role="img" aria-label="各分类净支出占比" style={{ background: gradient ? `conic-gradient(${gradient})` : "#ecf0e5" }}>
                  <div className="donut-center">正净支出合计<DonutTotal code={prim!.currency} value={pie!.denominator} />{pie!.slices.length} 个分类</div>
                </div>
                <div className="legend">
                  {pie!.empty && <p className="muted">这个月没有正净支出的分类。</p>}
                  {pie!.slices.map((sl, i) => (
                    <div key={sl.category_id} className="legend-row">
                      <i className="legend-dot" style={{ background: PIE_COLORS[i % PIE_COLORS.length] }}></i>
                      <span>{sl.name}</span><b>{sl.share_percent}%</b>
                    </div>
                  ))}
                  {pie!.negatives.length > 0 && (
                    <div className="negatives">
                      <small>净额为负（退款多于消费），不计入饼图：</small>
                      {pie!.negatives.map((n) => <div key={n.category_id} className="legend-row"><span>{n.name}</span><b>{cash(prim!.currency, n.net)}</b></div>)}
                    </div>
                  )}
                  {!pie!.empty && <p className="tiny-note">饼图分母为所有正净支出分类之和，可能不同于净支出总额。</p>}
                </div>
              </div>
            </article>
            <article className="card">
              <div className="section-head"><h2>消费方式</h2><small>扣除对应退款 · 不含收入</small></div>
              {prim!.by_payment_method.map((m) => (
                <div key={m.payment_method} className="payment-row">
                  <div className="payment-icon"><Icon name={m.payment_method === "cash" ? "cash" : m.payment_method === "credit_card" ? "card" : "wallet"} /></div>
                  <div className="payment-label">{METHOD_LABEL[m.payment_method]}<small>{m.expense_count} 笔消费 · {m.refund_count} 笔退款</small></div>
                  <div className="payment-amount"><strong>{cash(prim!.currency, m.net)}</strong>{sec && <small>{cash(sec.currency, sec.by_payment_method.find((x) => x.payment_method === m.payment_method)?.net ?? "0")}</small>}</div>
                </div>
              ))}
            </article>
          </section>
          <section className="card ledger-card">
            <div className="ledger-top">
              <h2>本月明细<small>{data!.items.length} 笔记录</small></h2>
              <div className="filters">
                <select aria-label="筛选类型" value={filters.type ?? ""} onChange={(e) => setParam("type", e.target.value)}>
                  <option value="">全部类型</option>
                  {ENTRY_TYPES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                </select>
                <select aria-label="筛选成员" value={filters.created_by ?? ""} onChange={(e) => setParam("created_by", e.target.value)}>
                  <option value="">所有成员</option>
                  {fam.members.map((m) => <option key={m.user_id} value={m.user_id}>{m.display_name}{m.status !== "active" ? "（已离开）" : ""}</option>)}
                </select>
                <select aria-label="筛选分类" value={filters.category_id ?? ""} onChange={(e) => setParam("category_id", e.target.value)}>
                  <option value="">所有分类</option>
                  {fam.categories.groups.map((g) => (
                    <optgroup key={g.category_id} label={g.name}>
                      <option value={g.category_id}>{g.name}（全部）</option>
                      {g.children.map((c) => <option key={c.category_id} value={c.category_id}>{c.name}</option>)}
                    </optgroup>
                  ))}
                </select>
                <select aria-label="筛选方式" value={filters.payment_method ?? ""} onChange={(e) => setParam("payment_method", e.target.value)}>
                  <option value="">所有方式</option>
                  {METHODS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                </select>
                {/* 合并为一个“导出”按钮，展开选择格式（原下载图标与 Excel 按钮看起来重复） */}
                <details className="export-menu" ref={exportMenu}>
                  <summary className="button secondary small"><Icon name="download" />导出</summary>
                  <div className="export-options" role="menu">
                    <button type="button" role="menuitem" onClick={() => void exportFile("xlsx")}>Excel（.xlsx）<small>按当前月份与筛选</small></button>
                    <button type="button" role="menuitem" onClick={() => void exportFile("csv")}>CSV<small>通用表格格式</small></button>
                  </div>
                </details>
              </div>
            </div>
            {activeFilters.length > 0 && (
              <p className="filter-note">当前筛选：{activeFilters.map(([k, v]) => `${FILTER_LABEL[k]}=${labelFor(k, v!, fam)}`).join("，")}。汇总、图表与导出使用同一范围。<button className="text-button" onClick={() => setParams(new URLSearchParams({ month }))}>清除</button></p>
            )}
            {phone ? (
              <EntryList items={data!.items} primary={prim!.currency} secondary={sec?.currency ?? null} colorOf={colorOf} onOpen={setOpenEntry} />
            ) : (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>日期</th><th>类型</th><th>一级分类</th><th>二级分类</th><th>币种</th>
                    <th className="numeric">原始金额</th><th className="numeric" data-currency-role="primary">合 {prim!.currency}</th>{sec && <th className="numeric" data-currency-role="secondary">合 {sec.currency}</th>}
                    <th>备注</th><th>方式</th><th>录入人</th><th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {data!.items.map((e) => (
                    <tr key={e.entry_id} data-entry data-type={e.type}>
                      <td>{shortDate(e.business_date)}</td>
                      <td><span className="badge">{TYPE_LABEL[e.type]}</span>{!e.counts_in_stats && <small className="no-stat">不计收支</small>}</td>
                      <td>{e.category.parent_name}</td>
                      <td>{e.category.leaf_name}</td>
                      <td>{e.currency}</td>
                      <td className="numeric">{money(e.amount)}</td>
                      <td className="numeric">{e.primary ? money(e.primary.amount) : "—"}</td>
                      {sec && <td className="numeric">{e.secondary ? money(e.secondary.amount) : "—"}</td>}
                      <td className="note-cell">{e.note}{e.attachments.length > 0 && <span title="有照片"> 📎</span>}</td>
                      <td>{e.payment_method ? METHOD_LABEL[e.payment_method] : "—"}</td>
                      <td>{e.created_by_display}{e.created_by_left && <small>（已离开）</small>}{e.source === "import" && <small>（历史导入）</small>}</td>
                      <td>
                        <div className="row-actions">
                          <button className="edit-row" onClick={() => setOpenEntry(e)} aria-label={`查看或编辑 ${e.note || e.category.leaf_name}`}><Icon name="edit" />{e.can_edit ? "编辑" : "查看"}</button>
                        </div>
                      </td>
                    </tr>
                  ))}
                  {data!.items.length === 0 && <tr><td colSpan={12} className="empty">还没有符合条件的记录。</td></tr>}
                </tbody>
              </table>
            </div>
            )}
            <div className="ledger-bottom"><span>按业务日期从新到旧 · 金额为记账时的汇率快照</span><span>共 {data!.items.length} 笔</span></div>
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

const FILTER_LABEL: Record<string, string> = { created_by: "录入人", type: "类型", category_id: "分类", payment_method: "方式" };

function labelFor(k: string, v: string, fam: ReturnType<typeof useFamily>): string {
  if (k === "type") return TYPE_LABEL[v] ?? v;
  if (k === "payment_method") return METHOD_LABEL[v] ?? v;
  if (k === "created_by") return fam.members.find((m) => m.user_id === v)?.display_name ?? v;
  for (const g of fam.categories.groups) {
    if (g.category_id === v) return g.name;
    const c = g.children.find((x) => x.category_id === v);
    if (c) return `${g.name}／${c.name}`;
  }
  return v;
}
