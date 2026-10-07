import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { ApiError, api, type Schemas } from "../api/client";
import { ErrorNotice, Modal, PageHeading, UnknownPanel, useAction, useLoad, useToast } from "../components/common";
import { Icon } from "../components/Icon";
import { useFamily } from "../components/Layout";
import { money } from "../lib/format";
import { entryTypes, KIND_FOR_TYPE, methods } from "../lib/labels";
import { currentLang, plural, tr } from "../lib/i18n";
import { uploadPhoto } from "../lib/upload";

type Batch = Schemas["Batch"];
type Candidate = Schemas["Candidate"];
type Job = Schemas["RecognitionJob"];

// 候选可选类型：不含退款（退款须从原消费录入）
const candTypes = () => entryTypes().filter(([v]) => v !== "refund");

const sourceTag = (): Record<string, string> => ({
  family_default: tr("默认值", "Default"), request_date: tr("接收日", "Date received"), inferred: tr("请核对", "Check"),
});
const problems = (): Record<string, string> => ({
  refund_not_supported: tr("看起来是退款：请在月度总览中打开原消费，点击“退款”录入", "Looks like a refund: open the original expense in the Overview and choose “Refund”"),
  category_inactive: tr("分类已停用，请重新选择", "This category is disabled. Choose another"),
  category_kind_mismatch: tr("分类与类型不符，请重新选择", "The category doesn’t match the type. Choose another"),
  rate_pending: tr("缺少该日期的汇率，请管理员补录", "No exchange rate for this date. Ask an admin to add one"),
  amount_invalid: tr("金额无效", "Invalid amount"),
});
const issues = (): Record<string, string> => ({
  image_unreadable: tr("照片看不清，请补充或手工录入", "The photo is hard to read. Add details or enter it manually"),
  total_ambiguous: tr("没能确定小票总额，请手工补充金额", "Couldn’t find the receipt total. Enter the amount manually"),
  not_a_transaction: tr("内容看起来不是一笔收支", "This doesn’t look like a transaction"),
  embedded_instructions: tr("输入中含有指令性文字，已忽略", "Instructions in the input were ignored"),
  too_many_items: tr("内容超过 10 笔，只识别了前 10 笔", "More than 10 items; only the first 10 were read"),
  text_image_conflict: tr("文字与照片内容不一致，请核对", "The text and photo disagree. Please check"),
});
// 识别失败原因：按类别显示当前语言；未知类别退回服务端说明
const failureText = (f?: { class?: string; message?: string }): string => {
  if (!f) return "";
  const en: Record<string, string> = {
    input_rejected: "The input wasn’t accepted",
    permission_revoked: "You no longer have access to this family",
    protocol_error: "The result came back malformed. Try again or enter it manually",
    schema_invalid: "The result came back malformed. Try again or enter it manually",
    semantic_invalid: "The result couldn’t be used. Enter it manually",
    rate_limited: "The reading service is busy. Try again shortly",
    transport_error: "The reading service is unavailable",
    timeout: "Reading timed out. Try again shortly",
    auth_error: "The reading service is unavailable",
    attempts_exhausted: "Reading failed after several tries. Try again or enter it manually",
  };
  return currentLang() === "en" ? en[f.class ?? ""] ?? "Reading failed" : f.message ?? "";
};
const missingNames = (): Record<string, string> => ({
  amount: tr("金额", "amount"), currency: tr("币种", "currency"), business_date: tr("日期", "date"),
  category: tr("分类", "category"), payment_method: tr("消费方式", "payment method"), type: tr("类型", "type"),
});

function Tag({ c, field }: { c: Candidate; field: string }) {
  const s = c.field_sources[field];
  if (!s || s === "evidence" || s === "user") return c.needs_review.includes(field) ? <span className="field-tag review">{tr("请核对", "Check")}</span> : null;
  return <span className={`field-tag ${s}`}>{sourceTag()[s] ?? s}</span>;
}

function CandidateCard({ c, index, batchId, selected, onSelect, onChanged }: {
  c: Candidate; index: number; batchId: string; selected: boolean; onSelect: (v: boolean) => void; onChanged: (b?: Batch) => void;
}) {
  const fam = useFamily();
  const action = useAction<Candidate>(fam.fid);
  const [split, setSplit] = useState(false);
  const base = `/families/${fam.fid}/batches/${batchId}/candidates/${c.candidate_id}`;
  const kind = c.type ? KIND_FOR_TYPE[c.type] : "expense";
  const groups = fam.categories.groups.filter((g) => g.kind === kind && g.status === "active");
  const parentId = c.category?.parent_id ?? "";
  const leaves = fam.categories.groups.find((g) => g.category_id === parentId)?.children.filter((x) => x.status === "active" && !x.redirect_to) ?? [];
  const editable = c.status !== "confirmed" && c.status !== "expired";

  async function patch(body: Record<string, unknown>) {
    const r = await action.run((key) => api.patch<Candidate>(base, { ...body, expected_version: c.version }, { key }));
    if (r) onChanged();
  }

  return (
    <article className={`candidate-card ${c.status}`}>
      <div className="candidate-header">
        <label className="inline-check">
          <input type="checkbox" disabled={c.status !== "ready"} checked={selected && c.status === "ready"} onChange={(e) => onSelect(e.target.checked)} aria-label={tr(`选择候选 ${index + 1}`, `Select candidate ${index + 1}`)} />
          <h3>{String(index + 1).padStart(2, "0")} · {c.note || tr("候选", "Candidate")}</h3>
        </label>
        <strong>{c.currency ?? ""} {c.fx_preview?.status === "ok" ? money(c.fx_preview.display_amounts.amount) : c.amount ? money(c.amount) : tr("金额待补", "Amount needed")}</strong>
      </div>
      {c.problems.map((p) => <p key={p} className="tiny-note warn">{problems()[p] ?? p}</p>)}
      {c.missing_fields.length > 0 && <p className="tiny-note warn">{tr("待补充：", "Still needed: ")}{c.missing_fields.map((m) => missingNames()[m] ?? m).join(tr("、", ", "))}</p>}
      <div className="field-grid">
        <label className="field">{tr("类型", "Type")} <Tag c={c} field="type" />
          <select value={c.type ?? ""} disabled={!editable} onChange={(e) => void patch({ type: e.target.value })}>
            {!c.type && <option value="">{tr("请选择", "Choose")}</option>}
            {candTypes().map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </label>
        <label className="field">{tr("日期", "Date")} <Tag c={c} field="business_date" />
          <input type="date" defaultValue={c.business_date} disabled={!editable} onBlur={(e) => e.target.value !== c.business_date && void patch({ business_date: e.target.value })} />
        </label>
        <label className="field">{tr("金额", "Amount")} <Tag c={c} field="amount" />
          <input inputMode="decimal" defaultValue={c.amount ?? ""} disabled={!editable} placeholder={tr("请填写", "Enter")} onBlur={(e) => { const v = e.target.value.trim(); if (v && v !== c.amount) void patch({ amount: v }); }} />
        </label>
        <label className="field">{tr("币种", "Currency")} <Tag c={c} field="currency" />
          <select value={c.currency ?? ""} disabled={!editable} onChange={(e) => void patch({ currency: e.target.value })}>
            {fam.currencies.map((x) => <option key={x.code} value={x.code}>{x.code}</option>)}
          </select>
        </label>
        <label className="field">{tr("一级分类", "Category")} <Tag c={c} field="category_id" />
          <select value={parentId} disabled={!editable || !c.type} onChange={(e) => {
            const g = fam.categories.groups.find((x) => x.category_id === e.target.value);
            const first = g?.children.find((x) => x.status === "active" && !x.redirect_to);
            if (first) void patch({ leaf_category_id: first.category_id });
          }}>
            {!parentId && <option value="">{tr("请选择", "Choose")}</option>}
            {groups.map((g) => <option key={g.category_id} value={g.category_id}>{g.name}</option>)}
          </select>
        </label>
        <label className="field">{tr("二级分类", "Subcategory")}
          <select value={c.category?.leaf_id ?? ""} disabled={!editable || !c.type} onChange={(e) => void patch({ leaf_category_id: e.target.value })}>
            {c.category && !leaves.some((l) => l.category_id === c.category!.leaf_id) && <option value={c.category.leaf_id}>{c.category.leaf_name}{tr("（不可用）", " (unavailable)")}</option>}
            {leaves.map((l) => <option key={l.category_id} value={l.category_id}>{l.name}</option>)}
          </select>
        </label>
        {(c.type === "expense" || c.type === "income") && (
          <label className="field">{c.type === "income" ? tr("收款方式", "Received via") : tr("消费方式", "Payment method")} <Tag c={c} field="payment_method" />
            <select value={c.payment_method ?? ""} disabled={!editable} onChange={(e) => void patch({ payment_method: e.target.value || null })}>
              {c.type === "income" && <option value="">{tr("未指定", "Not specified")}</option>}
              {methods().map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </label>
        )}
      </div>
      <label className="field">{tr("备注", "Note")}
        <input defaultValue={c.note} maxLength={200} disabled={!editable} onBlur={(e) => e.target.value !== c.note && void patch({ note: e.target.value })} />
      </label>
      <div className="candidate-info">
        {c.fx_preview?.status === "ok" ? tr(`合 NZD $${money(c.fx_preview.display_amounts.nzd)} · 合 CNY ¥${money(c.fx_preview.display_amounts.cny)} · 汇率日期 ${c.fx_preview.snapshot.effective_date}`, `≈ NZD $${money(c.fx_preview.display_amounts.nzd)} · ≈ CNY ¥${money(c.fx_preview.display_amounts.cny)} · rate date ${c.fx_preview.snapshot.effective_date}`) : c.fx_preview?.status === "rate_pending" ? tr("待补汇率", "Rate needed") : tr("补全金额后显示折算", "Conversion appears once the amount is filled in")}
        {c.status === "confirmed" && tr(" · 已入账", " · Saved")}
      </div>
      <ErrorNotice error={action.error} />
      {editable && (
        <div className="row-actions">
          <button className="button secondary small" onClick={() => setSplit(true)} disabled={!c.amount}>{tr("拆分", "Split")}</button>
          <button className="button secondary small danger" onClick={async () => {
            const r = await action.run((key) => api.del(base, { key, query: { expected_version: c.version } }));
            if (r !== undefined) onChanged();
          }}>{tr("删除", "Delete")}</button>
        </div>
      )}
      {split && <SplitDialog c={c} batchId={batchId} onClose={() => setSplit(false)} onDone={(b) => { setSplit(false); onChanged(b); }} />}
    </article>
  );
}

function SplitDialog({ c, batchId, onClose, onDone }: { c: Candidate; batchId: string; onClose: () => void; onDone: (b: Batch) => void }) {
  const fam = useFamily();
  const [parts, setParts] = useState([{ amount: "", note: "" }, { amount: "", note: "" }]);
  const action = useAction<Batch>(fam.fid);
  return (
    <Modal title={tr("拆分小票", "Split receipt")} onClose={onClose}>
      <p className="tiny-note">{tr(`原金额 ${c.currency} ${money(c.amount ?? "")}；各部分合计必须等于原金额，确认后原总额只作为凭证组信息，不重复记账。`, `Original amount ${c.currency} ${money(c.amount ?? "")}. The parts must add up to it; after confirming, the original total is kept only as receipt info and isn’t recorded twice.`)}</p>
      {parts.map((p, i) => (
        <div key={i} className="field-grid">
          <label className="field">{tr(`第 ${i + 1} 部分金额`, `Part ${i + 1} amount`)}<input inputMode="decimal" value={p.amount} onChange={(e) => setParts(parts.map((x, j) => (j === i ? { ...x, amount: e.target.value.trim() } : x)))} /></label>
          <label className="field">{tr("备注", "Note")}<input value={p.note} onChange={(e) => setParts(parts.map((x, j) => (j === i ? { ...x, note: e.target.value } : x)))} /></label>
        </div>
      ))}
      {parts.length < 10 && <button className="text-button" onClick={() => setParts([...parts, { amount: "", note: "" }])}>{tr("＋ 再加一部分", "＋ Add another part")}</button>}
      <ErrorNotice error={action.error} />
      <div className="form-actions">
        <button className="button secondary" onClick={onClose}>{tr("取消", "Cancel")}</button>
        <button className="button primary" disabled={action.busy || parts.some((p) => !p.amount)} onClick={async () => {
          const r = await action.run((key) => api.post<Batch>(`/families/${fam.fid}/batches/${batchId}/candidates/${c.candidate_id}/split`, {
            expected_version: c.version, parts: parts.map((p) => ({ amount: p.amount, ...(p.note ? { note: p.note } : {}) })),
          }, { key }));
          if (r) onDone(r);
        }}>{tr("确认拆分", "Confirm split")}</button>
      </div>
    </Modal>
  );
}

export default function AiEntry() {
  const fam = useFamily();
  const navigate = useNavigate();
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const batchId = params.get("batch");
  const [text, setText] = useState("");
  const [photo, setPhoto] = useState<{ name: string; id?: string; error?: string; busy?: boolean } | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const submit = useAction<Job>(fam.fid);
  const confirm = useAction<Schemas["ConfirmResult"]>(fam.fid);
  const batches = useLoad(() => api.get<{ items: Schemas["BatchSummary"][] }>(`/families/${fam.fid}/batches`, { status: "open" }), [fam.fid, batchId]);
  const batch = useLoad(() => (batchId ? api.get<Batch>(`/families/${fam.fid}/batches/${batchId}`) : Promise.resolve(null)), [fam.fid, batchId]);

  // 轮询识别任务（最多约 90 秒）
  useEffect(() => {
    if (!job || job.status === "candidate_ready" || job.status === "failed") return;
    let tries = 0;
    const t = setInterval(async () => {
      tries += 1;
      try {
        const j = await api.get<Job>(`/families/${fam.fid}/recognition-jobs/${job.job_id}`);
        setJob(j);
        if (j.status === "candidate_ready" && j.batch_id) setParams({ batch: j.batch_id });
      } catch { /* 下一轮重试 */ }
      if (tries > 90) clearInterval(t);
    }, 1000);
    return () => clearInterval(t);
  }, [job, fam.fid, setParams]);

  useEffect(() => {
    if (batch.data) setSelected(Object.fromEntries(batch.data.candidates.filter((c) => c.status === "ready").map((c) => [c.candidate_id, true])));
  }, [batch.data]);

  const readySelected = useMemo(() => (batch.data?.candidates ?? []).filter((c) => c.status === "ready" && selected[c.candidate_id]), [batch.data, selected]);

  async function doConfirm(items: Candidate[]) {
    const r = await confirm.run((key) => api.post<Schemas["ConfirmResult"]>(`/families/${fam.fid}/batches/${batchId}/confirm`, {
      items: items.map((c) => ({ candidate_id: c.candidate_id, version: c.version, content_digest: c.content_digest })),
    }, { key }));
    if (r) {
      // 确认后直接回到“AI 帮我记”输入页；未确认的候选仍在右侧“未处理的识别”里
      const left = (batch.data?.candidates ?? []).filter(
        (c) => c.status !== "confirmed" && !items.some((x) => x.candidate_id === c.candidate_id),
      ).length;
      toast.show(left > 0 ? tr(`已入账 ${r.entries.length} 笔；另有 ${left} 笔待确认，可在右侧继续`, `Saved ${plural(r.entries.length, "entry", "entries")}; ${left} still to confirm`) : tr(`已入账 ${r.entries.length} 笔`, `Saved ${plural(r.entries.length, "entry", "entries")}`));
      setParams({}); setJob(null); setText(""); setPhoto(null);
      await batches.reload();
    } else if (confirm.error instanceof ApiError && confirm.error.code === "candidate_stale") {
      await batch.reload();
    }
  }

  const b = batch.data;
  return (
    <>
      <PageHeading title={tr("AI 帮我记", "AI entry")} subtitle={tr("说一句话或拍一张小票，AI 整理成账目，你确认后才会入账。", "Describe it in a sentence or snap a receipt. AI drafts the entries; nothing is saved until you confirm.")} />
      <div className="two-column">
        <section className="card">
          {!b && (
            <form onSubmit={async (e) => {
              e.preventDefault();
              const j = await submit.run((key) => api.post<Job>(`/families/${fam.fid}/recognition-jobs`, { ...(text.trim() ? { text: text.trim() } : {}), ...(photo?.id ? { attachment_id: photo.id } : {}) }, { key }));
              if (j) setJob(j);
            }}>
              <label className="field">{tr("今天有什么开销？", "What did you spend today?")}
                <textarea value={text} maxLength={300} placeholder={tr("例如：今天超市买东西45纽币，停车8纽币", "e.g. Supermarket 45 NZD, parking 8 NZD")} onChange={(e) => setText(e.target.value)} />
              </label>
              <p className="tiny-note">{tr("未说明的币种与方式将使用家庭默认值，并在结果中标注。", "If you don’t mention a currency or method, the family default is used and marked in the result. ")}{text.length} / 300</p>
              <div className="field">
                <label className="upload-button">
                  <Icon name="upload" /> {photo ? `${photo.name}${photo.busy ? tr(" · 上传中", " · Uploading") : photo.error ? ` · ${photo.error}` : tr(" · 已通过", " · Ready")}` : tr("添加一张账单照片", "Add a receipt photo")}
                  <input type="file" accept="image/jpeg,image/png,image/webp" hidden onChange={async (e) => {
                    const f = e.target.files?.[0];
                    if (!f) return;
                    setPhoto({ name: f.name, busy: true });
                    const r = await uploadPhoto(fam.fid, f);
                    setPhoto(r.ok ? { name: f.name, id: r.id } : { name: f.name, error: r.reason });
                  }} />
                </label>
              </div>
              <ErrorNotice error={submit.error} />
              <UnknownPanel action={submit as never} onCommitted={() => toast.show(tr("已提交", "Submitted"))} onRetry={() => undefined} />
              {job && job.status !== "candidate_ready" && (
                <div className={`notice ${job.status === "failed" ? "error-notice" : ""}`} role="status">
                  <Icon name={job.status === "failed" ? "info" : "spark"} />
                  <div>{job.status === "failed" ? <><strong>{failureText(job.failure)}</strong><p>{tr("可以重试，或直接“记一笔”。", "Try again, or use “New entry”.")}</p></> : tr("正在识别…", "Reading…")}</div>
                </div>
              )}
              <div className="form-actions">
                <button type="button" className="button secondary" onClick={() => navigate(`/f/${fam.fid}/new`)}>{tr("改为手工录入", "Enter manually")}</button>
                <button className="button primary" disabled={submit.busy || (!text.trim() && !photo?.id) || photo?.busy || (!!job && !["failed", "candidate_ready"].includes(job.status))}><Icon name="spark" />{tr("开始识别", "Read it")}</button>
              </div>
            </form>
          )}
          {b && (
            <>
              <div className="section-head">
                <h2>{tr("待你确认", "Review")}</h2>
                <span className="review-state">{tr(`${b.candidates.filter((c) => c.status !== "confirmed").length} 笔待确认`, `${b.candidates.filter((c) => c.status !== "confirmed").length} to confirm`)}</span>
              </div>
              {b.input_issues?.map((i) => <p key={i} className="tiny-note warn">{issues()[i] ?? i}</p>)}
              {b.status !== "open" && <p className="tiny-note warn">{b.status === "expired" ? tr("该批次已过期（7 天）。", "This batch has expired (7 days).") : b.status === "cancelled" ? tr("该批次已取消。", "This batch was cancelled.") : tr("该批次已全部处理。", "Everything in this batch is done.")}</p>}
              {b.candidates.length === 0 && <p className="muted">{tr("没有识别出可记录的内容。", "Nothing to record was found.")}</p>}
              <ErrorNotice error={batch.error ?? confirm.error} />
              <UnknownPanel action={confirm as never} onCommitted={() => void batch.reload()} onRetry={() => void doConfirm(readySelected)} />
              {b.candidates.map((c, i) => (
                <CandidateCard key={c.candidate_id + c.version} c={c} index={i} batchId={b.batch_id} selected={!!selected[c.candidate_id]}
                  onSelect={(v) => setSelected({ ...selected, [c.candidate_id]: v })}
                  onChanged={() => void batch.reload()} />
              ))}
              <div className="form-actions">
                <button className="button secondary" onClick={() => { setParams({}); setJob(null); setText(""); setPhoto(null); }}>{tr("再记一条", "Add another")}</button>
                {b.status === "open" && (
                  <button className="button secondary danger" onClick={async () => {
                    await api.post(`/families/${fam.fid}/batches/${b.batch_id}/cancel`, { expected_version: b.version });
                    await batch.reload();
                  }}>{tr("取消整批", "Cancel batch")}</button>
                )}
                <button className="button primary" disabled={confirm.busy || readySelected.length === 0} onClick={() => void doConfirm(readySelected)}>
                  <Icon name="check" />{tr(`确认并记账（${readySelected.length}）`, `Confirm and save (${readySelected.length})`)}
                </button>
              </div>
              <p className="tiny-note">{tr("只有你本人可以确认自己发起的候选；未确认的候选 7 天后过期，不会进入账本。", "Only you can confirm candidates you started. Unconfirmed candidates expire after 7 days and never reach the ledger.")}</p>
            </>
          )}
        </section>
        <aside>
          <section className="card aside-note">
            <h3>{tr("未处理的识别", "Unfinished")}</h3>
            {batches.data?.items.length === 0 && <p className="muted">{tr("暂无。", "None.")}</p>}
            {batches.data?.items.map((x) => (
              <button key={x.batch_id} className="member-row as-button" onClick={() => setParams({ batch: x.batch_id })}>
                <div><strong>{tr(`${x.candidate_count} 条候选`, plural(x.candidate_count, "candidate", "candidates"))}</strong><small>{x.source === "feishu" ? tr("飞书", "Feishu") : tr("网站", "Website")} · {tr(`${x.expires_at.slice(0, 10)} 前有效`, `valid until ${x.expires_at.slice(0, 10)}`)}</small></div>
              </button>
            ))}
            <p className="tiny-note">{tr("识别结果只是建议，金额请以小票为准。照片与文字会发送给模型服务处理。", "Results are suggestions; trust the receipt for amounts. Photos and text are sent to the model service for processing.")}</p>
          </section>
        </aside>
      </div>
      {toast.node}
    </>
  );
}
