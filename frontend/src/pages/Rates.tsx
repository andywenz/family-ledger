import { useState } from "react";
import { plural, tr } from "../lib/i18n";
import { api, type Currency, type Schemas } from "../api/client";
import { ErrorNotice, PageHeading, useAction, useLoad, useToast } from "../components/common";
import { useFamily } from "../components/Layout";
import { currencyName, minorToDecimal, money, perUsd, today } from "../lib/format";

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
      <PageHeading title={tr("币种与汇率", "Currencies & rates")} subtitle={tr("以美元为基准，显示为“币种:美元”，即多少单位该币种兑 1 美元。历史账目保留入账时的快照。", "Rates are based on the US dollar and shown as “currency : USD”, i.e. how many units equal 1 USD. Past entries keep the rate from when they were saved.")} />
      <ErrorNotice error={action.error} />
      <div className="settings-grid">
        <section className="card">
          <div className="section-head"><h2>{tr("查看汇率组", "Rate set")}</h2><input type="date" value={date} onChange={(e) => setDate(e.target.value)} aria-label={tr("汇率日期", "Rate date")} /></div>
          <ErrorNotice error={resolved.error} />
          {resolved.data?.status === "ok" && (
            <>
              <p className="tiny-note">{tr("实际汇率日期", "Rates from")} {resolved.data.effective_date}{resolved.data.effective_date !== date && tr("（该日无发布，取不晚于该日的最近完整组）", " (nothing published that day; using the latest complete set before it)")}</p>
              {Object.entries(resolved.data.rates).sort(([a], [b]) => a.localeCompare(b)).map(([code, r]) => (
                <div key={code} className="rate-row">
                  <span className="rate-symbol">{code}</span>
                  <div><strong>{currencyName(code, fam.currencies.find((c) => c.code === code)?.name_zh)}</strong><small>{r.source === "family_manual" ? tr(`家庭修正 · 第 ${r.revision} 版`, `Family override · version ${r.revision}`) : r.source === "base" ? tr("基准币种", "Base currency") : tr("ECB 数据源", "ECB")}</small></div>
                  <b className="rate-ratio" title={`1 ${code} = ${r.usd_value} USD`}><small>{currencyName(code, fam.currencies.find((c) => c.code === code)?.name_zh)}{tr(":美元 = ", " : USD = ")}</small>{perUsd(r.usd_value)}:1</b>
                </div>
              ))}
              {(resolved.data.missing_currencies ?? []).map((code) => (
                <div key={code} className="rate-row rate-missing">
                  <span className="rate-symbol">{code}</span>
                  <div><strong>{currencyName(code, fam.currencies.find((c) => c.code === code)?.name_zh)}</strong><small>{tr("该日期前 7 天内缺少汇率", "No rate in the 7 days before this date")}{isAdmin ? tr("，可在“补录或修正汇率”中补录", "; add one under “Add or correct a rate”") : tr("，请家庭管理员补录", "; ask a family admin to add one")}</small></div>
                  <b>—</b>
                </div>
              ))}
            </>
          )}
          {resolved.data?.status === "rate_pending" && <p className="tiny-note warn">{tr(`该日期前 7 天内缺少 ${resolved.data.missing_currencies.join("、")} 的汇率，需要补录。`, `No ${resolved.data.missing_currencies.join(", ")} rate in the 7 days before this date. One needs to be added.`)}</p>}
        </section>
        {isAdmin && (
          <section className="card">
            <div className="section-head"><h2>{tr("补录或修正汇率", "Add or correct a rate")}</h2><small>{tr("只影响本家庭；不改写已入账记录", "This family only; saved entries aren’t rewritten")}</small></div>
            <form onSubmit={async (e) => {
              e.preventDefault();
              const existing = resolved.data?.status === "ok" && resolved.data.effective_date === form.date ? resolved.data.rates[form.currency] : undefined;
              const body: Record<string, unknown> = { ...form };
              if (existing?.source === "family_manual") body.expected_revision = existing.revision;
              const r = await action.run((key) => api.post(`/families/${fam.fid}/rates/manual`, body, { key }));
              if (r !== undefined) { toast.show(tr("已保存；如需更新已入账记录，请使用下方的显式重算", "Saved. To update saved entries, use Recalculate below")); await resolved.reload(); }
            }}>
              <div className="field-grid">
                <label className="field">{tr("日期", "Date")}<input type="date" value={form.date} onChange={(e) => setForm({ ...form, date: e.target.value })} required /></label>
                <label className="field">{tr("币种", "Currency")}<select value={form.currency} onChange={(e) => setForm({ ...form, currency: e.target.value })} required>
                  <option value="">{tr("请选择", "Choose")}</option>{fam.currencies.filter((c) => c.code !== "USD").map((c) => <option key={c.code}>{c.code}</option>)}
                </select></label>
                <label className="field">{tr("1 单位 = ? USD", "1 unit = ? USD")}<input inputMode="decimal" value={form.usd_value} onChange={(e) => setForm({ ...form, usd_value: e.target.value.trim() })} required /></label>
                <label className="field">{tr("原因", "Reason")}<input value={form.reason} maxLength={200} onChange={(e) => setForm({ ...form, reason: e.target.value })} required /></label>
              </div>
              <button className="button primary">{tr("保存", "Save")}</button>
            </form>
            <div className="section-head space-top"><h2>{tr("启用币种", "Enable currencies")}</h2></div>
            <div className="chip-row">
              {all.data?.items.filter((c) => !enabled.has(c.code)).map((c) => (
                <button key={c.code} className="button secondary small" onClick={async () => {
                  const r = await action.run((key) => api.post(`/families/${fam.fid}/currencies`, { code: c.code }, { key }));
                  if (r !== undefined) { toast.show(tr(`已启用 ${c.code}`, `${c.code} enabled`)); await fam.reload(); }
                }}>＋ {c.code} {currencyName(c.code, c.name_zh)}{!c.provider_supported && tr("（需补录汇率）", " (rates entered manually)")}</button>
              ))}
              {all.data && all.data.items.every((c) => enabled.has(c.code)) && <p className="muted">{tr("全部币种已启用。新增币种请联系系统管理员。", "All currencies are enabled. Ask the system admin to add new ones.")}</p>}
            </div>
            <div className="section-head space-top"><h2>{tr("显式重算", "Recalculate")}</h2><small>{tr("先预览，再确认", "Preview first, then confirm")}</small></div>
            <div className="field-grid">
              <label className="field">{tr("起始日期", "From")}<input type="date" value={range.from} onChange={(e) => setRange({ ...range, from: e.target.value })} /></label>
              <label className="field">{tr("结束日期", "To")}<input type="date" value={range.to} onChange={(e) => setRange({ ...range, to: e.target.value })} /></label>
            </div>
            <button className="button secondary" onClick={async () => {
              const r = await action.run((key) => api.post<Schemas["RecomputeJob"]>(`/families/${fam.fid}/rates/recompute`, { date_from: range.from, date_to: range.to }, { key }));
              if (r) setJob(r as Schemas["RecomputeJob"]);
            }}>{tr("预览影响", "Preview")}</button>
            {job?.preview && (
              <div className="recompute-preview">
                <p>{tr(`范围内 ${job.preview.entry_count} 笔，其中 ${job.preview.changed_count} 笔折算会变化。`, `${plural(job.preview.entry_count ?? 0, "entry", "entries")} in range; ${job.preview.changed_count} would change.`)}</p>
                {job.preview.months?.map((m) => <p key={m.month} className="tiny-note">{m.month}：NZD {money(minorToDecimal(m.nzd_delta_minor))}，CNY {money(minorToDecimal(m.cny_delta_minor))}</p>)}
                {(job.preview.pending_currencies ?? []).length > 0 && <p className="tiny-note warn">{tr("缺少汇率、未纳入：", "Missing rates, not included: ")}{job.preview.pending_currencies!.join(tr("、", ", "))}</p>}
                {job.status === "preview_ready" && (job.preview.changed_count ?? 0) > 0 && (
                  <button className="button primary" onClick={async () => {
                    const r = await action.run((key) => api.post<Schemas["RecomputeJob"]>(`/families/${fam.fid}/rates/recompute/${job.job_id}/confirm`, { preview_digest: job.preview_digest }, { key }));
                    if (r) { setJob(r as Schemas["RecomputeJob"]); toast.show((r as Schemas["RecomputeJob"]).status === "done" ? tr("重算完成", "Recalculation done") : tr("部分账目在预览后被修改，请重新预览", "Some entries changed after the preview. Preview again")); }
                  }}>{tr("确认重算", "Confirm")}</button>
                )}
                {job.status === "done" && <p className="tiny-note">{tr(`已完成：更新 ${job.applied_count} 笔。`, `Done: ${plural(job.applied_count ?? 0, "entry", "entries")} updated.`)}</p>}
                {job.status === "superseded" && <p className="tiny-note warn">{tr(`预览后有账目被修改，已停止。已完成 ${job.applied_count} 笔，请重新预览。`, `Entries changed after the preview, so it stopped after ${plural(job.applied_count ?? 0, "entry", "entries")}. Preview again.`)}</p>}
              </div>
            )}
          </section>
        )}
      </div>
      {toast.node}
    </>
  );
}
