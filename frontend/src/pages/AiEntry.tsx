import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { ApiError, api, type Schemas } from "../api/client";
import { ErrorNotice, Modal, PageHeading, UnknownPanel, useAction, useLoad, useToast } from "../components/common";
import { Icon } from "../components/Icon";
import { useFamily } from "../components/Layout";
import { money } from "../lib/format";
import { KIND_FOR_TYPE, METHODS } from "../lib/labels";
import { uploadPhoto } from "../lib/upload";

type Batch = Schemas["Batch"];
type Candidate = Schemas["Candidate"];
type Job = Schemas["RecognitionJob"];

const CAND_TYPES = [
  ["expense", "支出"], ["income", "收入"], ["internal_transfer", "内部转账"],
  ["exchange", "换汇"], ["card_repayment", "信用卡还款"], ["receivable", "往来款"],
] as const;

const SOURCE_TAG: Record<string, string> = { family_default: "默认值", request_date: "接收日", inferred: "请核对" };
const PROBLEM: Record<string, string> = {
  refund_not_supported: "看起来是退款：请在月度总览中打开原消费，点击“退款”录入",
  category_inactive: "分类已停用，请重新选择",
  category_kind_mismatch: "分类与类型不符，请重新选择",
  rate_pending: "缺少该日期的汇率，请管理员补录",
  amount_invalid: "金额无效",
};
const ISSUE: Record<string, string> = {
  image_unreadable: "照片看不清，请补充或手工录入",
  total_ambiguous: "没能确定小票总额，请手工补充金额",
  not_a_transaction: "内容看起来不是一笔收支",
  embedded_instructions: "输入中含有指令性文字，已忽略",
  too_many_items: "内容超过 10 笔，只识别了前 10 笔",
  text_image_conflict: "文字与照片内容不一致，请核对",
};
const MISSING: Record<string, string> = { amount: "金额", currency: "币种", business_date: "日期", category: "分类", payment_method: "消费方式", type: "类型" };

function Tag({ c, field }: { c: Candidate; field: string }) {
  const s = c.field_sources[field];
  if (!s || s === "evidence" || s === "user") return c.needs_review.includes(field) ? <span className="field-tag review">请核对</span> : null;
  return <span className={`field-tag ${s}`}>{SOURCE_TAG[s] ?? s}</span>;
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
          <input type="checkbox" disabled={c.status !== "ready"} checked={selected && c.status === "ready"} onChange={(e) => onSelect(e.target.checked)} aria-label={`选择候选 ${index + 1}`} />
          <h3>{String(index + 1).padStart(2, "0")} · {c.note || "候选"}</h3>
        </label>
        <strong>{c.currency ?? ""} {c.fx_preview?.status === "ok" ? money(c.fx_preview.display_amounts.amount) : c.amount ? money(c.amount) : "金额待补"}</strong>
      </div>
      {c.problems.map((p) => <p key={p} className="tiny-note warn">{PROBLEM[p] ?? p}</p>)}
      {c.missing_fields.length > 0 && <p className="tiny-note warn">待补充：{c.missing_fields.map((m) => MISSING[m] ?? m).join("、")}</p>}
      <div className="field-grid">
        <label className="field">类型 <Tag c={c} field="type" />
          <select value={c.type ?? ""} disabled={!editable} onChange={(e) => void patch({ type: e.target.value })}>
            {!c.type && <option value="">请选择</option>}
            {CAND_TYPES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </label>
        <label className="field">日期 <Tag c={c} field="business_date" />
          <input type="date" defaultValue={c.business_date} disabled={!editable} onBlur={(e) => e.target.value !== c.business_date && void patch({ business_date: e.target.value })} />
        </label>
        <label className="field">金额 <Tag c={c} field="amount" />
          <input inputMode="decimal" defaultValue={c.amount ?? ""} disabled={!editable} placeholder="请填写" onBlur={(e) => { const v = e.target.value.trim(); if (v && v !== c.amount) void patch({ amount: v }); }} />
        </label>
        <label className="field">币种 <Tag c={c} field="currency" />
          <select value={c.currency ?? ""} disabled={!editable} onChange={(e) => void patch({ currency: e.target.value })}>
            {fam.currencies.map((x) => <option key={x.code} value={x.code}>{x.code}</option>)}
          </select>
        </label>
        <label className="field">一级分类 <Tag c={c} field="category_id" />
          <select value={parentId} disabled={!editable || !c.type} onChange={(e) => {
            const g = fam.categories.groups.find((x) => x.category_id === e.target.value);
            const first = g?.children.find((x) => x.status === "active" && !x.redirect_to);
            if (first) void patch({ leaf_category_id: first.category_id });
          }}>
            {!parentId && <option value="">请选择</option>}
            {groups.map((g) => <option key={g.category_id} value={g.category_id}>{g.name}</option>)}
          </select>
        </label>
        <label className="field">二级分类
          <select value={c.category?.leaf_id ?? ""} disabled={!editable || !c.type} onChange={(e) => void patch({ leaf_category_id: e.target.value })}>
            {c.category && !leaves.some((l) => l.category_id === c.category!.leaf_id) && <option value={c.category.leaf_id}>{c.category.leaf_name}（不可用）</option>}
            {leaves.map((l) => <option key={l.category_id} value={l.category_id}>{l.name}</option>)}
          </select>
        </label>
        {(c.type === "expense" || c.type === "income") && (
          <label className="field">{c.type === "income" ? "收款方式" : "消费方式"} <Tag c={c} field="payment_method" />
            <select value={c.payment_method ?? ""} disabled={!editable} onChange={(e) => void patch({ payment_method: e.target.value || null })}>
              {c.type === "income" && <option value="">未指定</option>}
              {METHODS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </label>
        )}
      </div>
      <label className="field">备注
        <input defaultValue={c.note} maxLength={200} disabled={!editable} onBlur={(e) => e.target.value !== c.note && void patch({ note: e.target.value })} />
      </label>
      <div className="candidate-info">
        {c.fx_preview?.status === "ok" ? `合 NZD $${money(c.fx_preview.display_amounts.nzd)} · 合 CNY ¥${money(c.fx_preview.display_amounts.cny)} · 汇率日期 ${c.fx_preview.snapshot.effective_date}` : c.fx_preview?.status === "rate_pending" ? "待补汇率" : "补全金额后显示折算"}
        {c.status === "confirmed" && " · 已入账"}
      </div>
      <ErrorNotice error={action.error} />
      {editable && (
        <div className="row-actions">
          <button className="button secondary small" onClick={() => setSplit(true)} disabled={!c.amount}>拆分</button>
          <button className="button secondary small danger" onClick={async () => {
            const r = await action.run((key) => api.del(base, { key, query: { expected_version: c.version } }));
            if (r !== undefined) onChanged();
          }}>删除</button>
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
    <Modal title="拆分小票" onClose={onClose}>
      <p className="tiny-note">原金额 {c.currency} {money(c.amount ?? "")}；各部分合计必须等于原金额，确认后原总额只作为凭证组信息，不重复记账。</p>
      {parts.map((p, i) => (
        <div key={i} className="field-grid">
          <label className="field">第 {i + 1} 部分金额<input inputMode="decimal" value={p.amount} onChange={(e) => setParts(parts.map((x, j) => (j === i ? { ...x, amount: e.target.value.trim() } : x)))} /></label>
          <label className="field">备注<input value={p.note} onChange={(e) => setParts(parts.map((x, j) => (j === i ? { ...x, note: e.target.value } : x)))} /></label>
        </div>
      ))}
      {parts.length < 10 && <button className="text-button" onClick={() => setParts([...parts, { amount: "", note: "" }])}>＋ 再加一部分</button>}
      <ErrorNotice error={action.error} />
      <div className="form-actions">
        <button className="button secondary" onClick={onClose}>取消</button>
        <button className="button primary" disabled={action.busy || parts.some((p) => !p.amount)} onClick={async () => {
          const r = await action.run((key) => api.post<Batch>(`/families/${fam.fid}/batches/${batchId}/candidates/${c.candidate_id}/split`, {
            expected_version: c.version, parts: parts.map((p) => ({ amount: p.amount, ...(p.note ? { note: p.note } : {}) })),
          }, { key }));
          if (r) onDone(r);
        }}>确认拆分</button>
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
      toast.show(left > 0 ? `已入账 ${r.entries.length} 笔；另有 ${left} 笔待确认，可在右侧继续` : `已入账 ${r.entries.length} 笔`);
      setParams({}); setJob(null); setText(""); setPhoto(null);
      await batches.reload();
    } else if (confirm.error instanceof ApiError && confirm.error.code === "candidate_stale") {
      await batch.reload();
    }
  }

  const b = batch.data;
  return (
    <>
      <PageHeading title="AI 帮我记" subtitle="说一句话或拍一张小票，AI 整理成账目，你确认后才会入账。" />
      <div className="two-column">
        <section className="card">
          {!b && (
            <form onSubmit={async (e) => {
              e.preventDefault();
              const j = await submit.run((key) => api.post<Job>(`/families/${fam.fid}/recognition-jobs`, { ...(text.trim() ? { text: text.trim() } : {}), ...(photo?.id ? { attachment_id: photo.id } : {}) }, { key }));
              if (j) setJob(j);
            }}>
              <label className="field">今天有什么开销？
                <textarea value={text} maxLength={300} placeholder="例如：今天超市买东西45纽币，停车8纽币" onChange={(e) => setText(e.target.value)} />
              </label>
              <p className="tiny-note">未说明的币种与方式将使用家庭默认值，并在结果中标注。{text.length} / 300</p>
              <div className="field">
                <label className="upload-button">
                  <Icon name="upload" /> {photo ? `${photo.name}${photo.busy ? " · 上传中" : photo.error ? ` · ${photo.error}` : " · 已通过"}` : "添加一张账单照片"}
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
              <UnknownPanel action={submit as never} onCommitted={() => toast.show("已提交")} onRetry={() => undefined} />
              {job && job.status !== "candidate_ready" && (
                <div className={`notice ${job.status === "failed" ? "error-notice" : ""}`} role="status">
                  <Icon name={job.status === "failed" ? "info" : "spark"} />
                  <div>{job.status === "failed" ? <><strong>{job.failure?.message}</strong><p>可以重试，或直接“记一笔”。</p></> : "正在识别…"}</div>
                </div>
              )}
              <div className="form-actions">
                <button type="button" className="button secondary" onClick={() => navigate(`/f/${fam.fid}/new`)}>改为手工录入</button>
                <button className="button primary" disabled={submit.busy || (!text.trim() && !photo?.id) || photo?.busy || (!!job && !["failed", "candidate_ready"].includes(job.status))}><Icon name="spark" />开始识别</button>
              </div>
            </form>
          )}
          {b && (
            <>
              <div className="section-head">
                <h2>待你确认</h2>
                <span className="review-state">{b.candidates.filter((c) => c.status !== "confirmed").length} 笔待确认</span>
              </div>
              {b.input_issues?.map((i) => <p key={i} className="tiny-note warn">{ISSUE[i] ?? i}</p>)}
              {b.status !== "open" && <p className="tiny-note warn">该批次{b.status === "expired" ? "已过期（7 天）" : b.status === "cancelled" ? "已取消" : "已全部处理"}。</p>}
              {b.candidates.length === 0 && <p className="muted">没有识别出可记录的内容。</p>}
              <ErrorNotice error={batch.error ?? confirm.error} />
              <UnknownPanel action={confirm as never} onCommitted={() => void batch.reload()} onRetry={() => void doConfirm(readySelected)} />
              {b.candidates.map((c, i) => (
                <CandidateCard key={c.candidate_id + c.version} c={c} index={i} batchId={b.batch_id} selected={!!selected[c.candidate_id]}
                  onSelect={(v) => setSelected({ ...selected, [c.candidate_id]: v })}
                  onChanged={() => void batch.reload()} />
              ))}
              <div className="form-actions">
                <button className="button secondary" onClick={() => { setParams({}); setJob(null); setText(""); setPhoto(null); }}>再记一条</button>
                {b.status === "open" && (
                  <button className="button secondary danger" onClick={async () => {
                    await api.post(`/families/${fam.fid}/batches/${b.batch_id}/cancel`, { expected_version: b.version });
                    await batch.reload();
                  }}>取消整批</button>
                )}
                <button className="button primary" disabled={confirm.busy || readySelected.length === 0} onClick={() => void doConfirm(readySelected)}>
                  <Icon name="check" />确认并记账（{readySelected.length}）
                </button>
              </div>
              <p className="tiny-note">只有你本人可以确认自己发起的候选；未确认的候选 7 天后过期，不会进入账本。</p>
            </>
          )}
        </section>
        <aside>
          <section className="card aside-note">
            <h3>未处理的识别</h3>
            {batches.data?.items.length === 0 && <p className="muted">暂无。</p>}
            {batches.data?.items.map((x) => (
              <button key={x.batch_id} className="member-row as-button" onClick={() => setParams({ batch: x.batch_id })}>
                <div><strong>{x.candidate_count} 条候选</strong><small>{x.source === "feishu" ? "飞书" : "网站"} · {x.expires_at.slice(0, 10)} 前有效</small></div>
              </button>
            ))}
            <p className="tiny-note">识别结果只是建议，金额请以小票为准。照片与文字会发送给模型服务处理。</p>
          </section>
        </aside>
      </div>
      {toast.node}
    </>
  );
}
