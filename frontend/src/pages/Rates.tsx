import { useState } from "react";
import { api, type Currency, type Schemas } from "../api/client";
import { ErrorNotice, PageHeading, useAction, useLoad, useToast } from "../components/common";
import { useFamily } from "../components/Layout";
import { minorToDecimal, money, today } from "../lib/format";

type Resolved = Schemas["ResolvedRates"];

export default function Rates() {
  const fam = useFamily();
  const toast = useToast();
  const isAdmin = fam.family.my_role === "admin";
  const [date, setDate] = useState(today(fam.config.timezone));
  const resolved = useLoad(() => api.get<Resolved>(`/families/${fam.fid}/rates`, { date }), [fam.fid, date]);
  const all = useLoad(() => api.get<{ items: Currency[] }>("/currencies"), []);
  const action = useAction<unknown>(fam.fid);
  const [form, setForm] = useState({ date: today(fam.config.timezone), currency: "", usd_value: "", reason: "" });
  const [job, setJob] = useState<Schemas["RecomputeJob"] | null>(null);
  const [range, setRange] = useState({ from: today(fam.config.timezone), to: today(fam.config.timezone) });
  const enabled = new Set(fam.currencies.map((c) => c.code));

  return (
    <>
      <PageHeading title="币种与汇率" subtitle="以美元为基准：一单位币种可兑换多少 USD。历史账目保留入账时的快照。" />
      <ErrorNotice error={action.error} />
      <div className="settings-grid">
        <section className="card">
          <div className="section-head"><h2>查看汇率组</h2><input type="date" value={date} onChange={(e) => setDate(e.target.value)} aria-label="汇率日期" /></div>
          <ErrorNotice error={resolved.error} />
          {resolved.data?.status === "ok" && (
            <>
              <p className="tiny-note">实际汇率日期 {resolved.data.effective_date}{resolved.data.effective_date !== date && "（该日无发布，取不晚于该日的最近完整组）"}</p>
              {Object.entries(resolved.data.rates).sort(([a], [b]) => a.localeCompare(b)).map(([code, r]) => (
                <div key={code} className="rate-row">
                  <span className="rate-symbol">{code}</span>
                  <div><strong>{fam.currencies.find((c) => c.code === code)?.name_zh ?? code}</strong><small>{r.source === "family_manual" ? `家庭修正 · 第 ${r.revision} 版` : r.source === "base" ? "基准币种" : "ECB 数据源"}</small></div>
                  <b>{money(r.usd_value)}</b>
                </div>
              ))}
              {(resolved.data.missing_currencies ?? []).map((code) => (
                <div key={code} className="rate-row rate-missing">
                  <span className="rate-symbol">{code}</span>
                  <div><strong>{fam.currencies.find((c) => c.code === code)?.name_zh ?? code}</strong><small>该日期前 7 天内缺少汇率{isAdmin ? "，可在“补录或修正汇率”中补录" : "，请家庭管理员补录"}</small></div>
                  <b>—</b>
                </div>
              ))}
            </>
          )}
          {resolved.data?.status === "rate_pending" && <p className="tiny-note warn">该日期前 7 天内缺少 {resolved.data.missing_currencies.join("、")} 的汇率，需要补录。</p>}
        </section>
        {isAdmin && (
          <section className="card">
            <div className="section-head"><h2>补录或修正汇率</h2><small>只影响本家庭；不改写已入账记录</small></div>
            <form onSubmit={async (e) => {
              e.preventDefault();
              const existing = resolved.data?.status === "ok" && resolved.data.effective_date === form.date ? resolved.data.rates[form.currency] : undefined;
              const body: Record<string, unknown> = { ...form };
              if (existing?.source === "family_manual") body.expected_revision = existing.revision;
              const r = await action.run((key) => api.post(`/families/${fam.fid}/rates/manual`, body, { key }));
              if (r !== undefined) { toast.show("已保存；如需更新已入账记录，请使用下方的显式重算"); await resolved.reload(); }
            }}>
              <div className="field-grid">
                <label className="field">日期<input type="date" value={form.date} onChange={(e) => setForm({ ...form, date: e.target.value })} required /></label>
                <label className="field">币种<select value={form.currency} onChange={(e) => setForm({ ...form, currency: e.target.value })} required>
                  <option value="">请选择</option>{fam.currencies.filter((c) => c.code !== "USD").map((c) => <option key={c.code}>{c.code}</option>)}
                </select></label>
                <label className="field">1 单位 = ? USD<input inputMode="decimal" value={form.usd_value} onChange={(e) => setForm({ ...form, usd_value: e.target.value.trim() })} required /></label>
                <label className="field">原因<input value={form.reason} maxLength={200} onChange={(e) => setForm({ ...form, reason: e.target.value })} required /></label>
              </div>
              <button className="button primary">保存</button>
            </form>
            <div className="section-head space-top"><h2>启用币种</h2></div>
            <div className="chip-row">
              {all.data?.items.filter((c) => !enabled.has(c.code)).map((c) => (
                <button key={c.code} className="button secondary small" onClick={async () => {
                  const r = await action.run((key) => api.post(`/families/${fam.fid}/currencies`, { code: c.code }, { key }));
                  if (r !== undefined) { toast.show(`已启用 ${c.code}`); await fam.reload(); }
                }}>＋ {c.code} {c.name_zh}{!c.provider_supported && "（需补录汇率）"}</button>
              ))}
              {all.data && all.data.items.every((c) => enabled.has(c.code)) && <p className="muted">全部币种已启用。新增币种请联系系统管理员。</p>}
            </div>
            <div className="section-head space-top"><h2>显式重算</h2><small>先预览，再确认</small></div>
            <div className="field-grid">
              <label className="field">起始日期<input type="date" value={range.from} onChange={(e) => setRange({ ...range, from: e.target.value })} /></label>
              <label className="field">结束日期<input type="date" value={range.to} onChange={(e) => setRange({ ...range, to: e.target.value })} /></label>
            </div>
            <button className="button secondary" onClick={async () => {
              const r = await action.run((key) => api.post<Schemas["RecomputeJob"]>(`/families/${fam.fid}/rates/recompute`, { date_from: range.from, date_to: range.to }, { key }));
              if (r) setJob(r as Schemas["RecomputeJob"]);
            }}>预览影响</button>
            {job?.preview && (
              <div className="recompute-preview">
                <p>范围内 {job.preview.entry_count} 笔，其中 {job.preview.changed_count} 笔折算会变化。</p>
                {job.preview.months?.map((m) => <p key={m.month} className="tiny-note">{m.month}：NZD {money(minorToDecimal(m.nzd_delta_minor))}，CNY {money(minorToDecimal(m.cny_delta_minor))}</p>)}
                {(job.preview.pending_currencies ?? []).length > 0 && <p className="tiny-note warn">缺少汇率、未纳入：{job.preview.pending_currencies!.join("、")}</p>}
                {job.status === "preview_ready" && (job.preview.changed_count ?? 0) > 0 && (
                  <button className="button primary" onClick={async () => {
                    const r = await action.run((key) => api.post<Schemas["RecomputeJob"]>(`/families/${fam.fid}/rates/recompute/${job.job_id}/confirm`, { preview_digest: job.preview_digest }, { key }));
                    if (r) { setJob(r as Schemas["RecomputeJob"]); toast.show((r as Schemas["RecomputeJob"]).status === "done" ? "重算完成" : "部分账目在预览后被修改，请重新预览"); }
                  }}>确认重算</button>
                )}
                {job.status === "done" && <p className="tiny-note">已完成：更新 {job.applied_count} 笔。</p>}
                {job.status === "superseded" && <p className="tiny-note warn">预览后有账目被修改，已停止。已完成 {job.applied_count} 笔，请重新预览。</p>}
              </div>
            )}
          </section>
        )}
      </div>
      {toast.node}
    </>
  );
}
