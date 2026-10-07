import { useEffect, useMemo, useState } from "react";
import { tr } from "../lib/i18n";
import { api, type Entry, type FxPreview } from "../api/client";
import { currencyName, money, today } from "../lib/format";
import { uploadPhoto } from "../lib/upload";
import { entryTypes, KIND_FOR_TYPE, methodRule, methods } from "../lib/labels";
import { ErrorNotice, UnknownPanel, useAction } from "./common";
import { Icon } from "./Icon";
import { useFamily } from "./Layout";

export type EntryBody = {
  business_date: string;
  type: string;
  amount: string;
  currency?: string;
  leaf_category_id?: string;
  payment_method?: string | null;
  note?: string;
  attachment_ids?: string[];
  refund_of?: string;
};

type Props = {
  mode: "create" | "edit" | "refund";
  entry?: Entry; // edit：原账目；refund：原消费
  onSaved: (e: Entry) => void;
  onCancel: () => void;
};

const AMOUNT_RE = /^(0|[1-9]\d{0,11})(\.\d{1,3})?$/;

type Upload = { name: string; status: "uploading" | "ready" | "rejected"; id?: string; reason?: string };

export function EntryForm({ mode, entry, onSaved, onCancel }: Props) {
  const fam = useFamily();
  const editingRefund = mode === "edit" && entry?.type === "refund";
  const locked = mode === "refund" || editingRefund; // 分类／币种／方式随原消费
  const [type, setType] = useState<string>(mode === "refund" ? "refund" : entry?.type ?? "expense");
  const [date, setDate] = useState(mode === "edit" ? entry!.business_date : today(fam.config.timezone));
  const [currency, setCurrency] = useState(mode === "create" ? fam.config.default_currency : entry!.currency);
  const [amount, setAmount] = useState(mode === "edit" ? entry!.amount : "");
  const [parent, setParent] = useState(mode === "edit" ? entry!.category.parent_id : "");
  const [leaf, setLeaf] = useState(mode === "edit" ? entry!.category.leaf_id : "");
  const [method, setMethod] = useState<string>(
    mode === "edit" ? entry!.payment_method ?? "" : mode === "refund" ? entry!.payment_method ?? "" : fam.config.default_payment_method,
  );
  const [note, setNote] = useState(mode === "edit" ? entry!.note : "");
  const [uploads, setUploads] = useState<Upload[]>(
    mode === "edit" ? entry!.attachments.map((a) => ({ name: tr("已附照片", "Attached photo"), status: "ready", id: a.attachment_id })) : [],
  );
  const [preview, setPreview] = useState<FxPreview | null>(null);
  const action = useAction<Entry>(fam.fid);

  const kind = KIND_FOR_TYPE[type];
  const groups = useMemo(
    () => fam.categories.groups.filter((g) => g.kind === kind && (g.status === "active" || g.category_id === parent)),
    [fam.categories, kind, parent],
  );
  const leaves = useMemo(() => {
    const g = fam.categories.groups.find((x) => x.category_id === parent);
    return (g?.children ?? []).filter((c) => (c.status === "active" && !c.redirect_to) || c.category_id === leaf);
  }, [fam.categories, parent, leaf]);

  // 类型变化后分类目录不匹配时，切到该目录第一个可选项
  useEffect(() => {
    if (locked) return;
    if (!groups.some((g) => g.category_id === parent)) {
      const first = groups.find((g) => g.status === "active");
      setParent(first?.category_id ?? "");
    }
  }, [groups, parent, locked]);
  useEffect(() => {
    if (locked) return;
    if (!leaves.some((c) => c.category_id === leaf)) setLeaf(leaves.find((c) => c.status === "active")?.category_id ?? "");
  }, [leaves, leaf, locked]);

  const rule = methodRule(type);
  const amountValid = AMOUNT_RE.test(amount) && Number(amount) > 0;

  const body = (): EntryBody => {
    const attachment_ids = uploads.filter((u) => u.status === "ready" && u.id).map((u) => u.id!);
    if (mode === "refund") return { business_date: date, type: "refund", amount, note, refund_of: entry!.entry_id, attachment_ids };
    if (editingRefund) return { business_date: date, type: "refund", amount, note, attachment_ids };
    return {
      business_date: date, type, amount, currency, leaf_category_id: leaf, note, attachment_ids,
      payment_method: rule === "none" ? null : method || null,
    };
  };

  // 服务端折算预览（防抖）
  useEffect(() => {
    if (!amountValid) {
      setPreview(null);
      return;
    }
    const t = setTimeout(async () => {
      try {
        const b = body();
        const payload = mode === "edit" ? { entry_id: entry!.entry_id, patch: { ...diff(entry!, b), expected_version: entry!.version } } : b;
        setPreview(await api.post<FxPreview>(`/families/${fam.fid}/entries/preview`, payload));
      } catch {
        setPreview(null);
      }
    }, 350);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [amount, currency, date, type, leaf, method]);

  async function addPhoto(file: File) {
    if (uploads.length >= 3) return;
    const item: Upload = { name: file.name, status: "uploading" };
    setUploads((u) => [...u, item]);
    const r = await uploadPhoto(fam.fid, file);
    setUploads((u) => u.map((x) => (x === item ? Object.assign(item, r.ok ? { status: "ready" as const, id: r.id } : { status: "rejected" as const, reason: r.reason }) : x)));
  }

  async function save() {
    const b = body();
    const saved = await action.run((key) =>
      mode === "edit"
        ? api.patch<Entry>(`/families/${fam.fid}/entries/${entry!.entry_id}`, { ...diff(entry!, b), expected_version: entry!.version }, { key })
        : api.post<Entry>(`/families/${fam.fid}/entries`, b, { key }),
    );
    if (saved) onSaved(saved);
  }

  function submit(ev: React.FormEvent) {
    ev.preventDefault();
    void save();
  }

  const ok = preview && preview.status === "ok" ? preview : null;
  const pending = preview && preview.status === "rate_pending" ? preview : null;

  return (
    <form onSubmit={submit} className="entry-form" noValidate>
      {mode === "refund" && (
        <p className="tiny-note">
          {tr("关联原消费：", "Original expense: ")}{entry!.business_date} · {entry!.category.parent_name}／{entry!.category.leaf_name} · {entry!.currency} {money(entry!.amount)}
          <br />{tr(`尚可退款 ${entry!.currency} ${money(entry!.refundable_amount ?? "0")}；退款沿用原消费的分类、币种与方式。`, `Up to ${entry!.currency} ${money(entry!.refundable_amount ?? "0")} can be refunded. The refund uses the original expense’s category, currency and method.`)}
        </p>
      )}
      {mode === "edit" && <p className="tiny-note">{tr("修改同一笔记录，不会新增账目。原录入人：", "This edits the same entry; no new entry is created. Entered by: ")}{entry!.created_by_display}</p>}
      <div className="field-grid">
        <label className="field">
          {tr("记录类型", "Type")}
          <select value={type} disabled={locked} onChange={(e) => setType(e.target.value)}>
            {entryTypes().filter(([v]) => v !== "refund" || locked).map(([v, l]) => (
              <option key={v} value={v}>{l}</option>
            ))}
          </select>
        </label>
        <label className="field">
          {tr("记账日期", "Date")}
          <input type="date" value={date} required onChange={(e) => setDate(e.target.value)} />
        </label>
        <label className="field">
          {tr("币种", "Currency")}
          <select value={currency} disabled={locked} onChange={(e) => setCurrency(e.target.value)}>
            {fam.currencies.map((c) => <option key={c.code} value={c.code} title={currencyName(c.code, c.name_zh)}>{c.code}</option>)}
          </select>
        </label>
        <label className="field">
          {tr("原始金额", "Amount")}
          <input inputMode="decimal" value={amount} placeholder="0.00" required aria-invalid={amount !== "" && !amountValid}
            onChange={(e) => setAmount(e.target.value.trim())} />
        </label>
        <label className="field">
          {tr("一级分类", "Category")}
          <select value={parent} disabled={locked} onChange={(e) => setParent(e.target.value)}>
            {locked && <option value={parent}>{entry!.category.parent_name}</option>}
            {!locked && groups.map((g) => <option key={g.category_id} value={g.category_id}>{g.name}{g.status !== "active" ? tr("（已停用）", " (disabled)") : ""}</option>)}
          </select>
        </label>
        <label className="field">
          {tr("二级分类", "Subcategory")}
          <select value={leaf} disabled={locked} onChange={(e) => setLeaf(e.target.value)}>
            {locked && <option value={leaf}>{entry!.category.leaf_name}</option>}
            {!locked && leaves.map((c) => <option key={c.category_id} value={c.category_id}>{c.name}{c.status !== "active" ? tr("（已停用）", " (disabled)") : ""}</option>)}
          </select>
        </label>
        {rule !== "none" && (
          <label className="field">
            {type === "income" ? tr("收款方式（可选）", "Received via (optional)") : tr("消费方式", "Payment method")}
            <select value={method} disabled={locked} onChange={(e) => setMethod(e.target.value)}>
              {rule === "optional" && <option value="">{tr("未指定", "Not specified")}</option>}
              {methods().map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </label>
        )}
      </div>
      {rule === "none" && <p className="tiny-note">{tr("该类型不计入收入或支出统计，不需要消费方式。", "This type isn’t counted in income or spending, so no payment method is needed.")}</p>}
      <label className="field">
        {tr("备注", "Note")}
        <textarea value={note} maxLength={200} onChange={(e) => setNote(e.target.value)} placeholder={tr("例如：周末采购，买了水果和牛奶", "e.g. Weekend shop: fruit and milk")} />
      </label>
      <div className="field">
        {tr("照片凭证（可选，最多 3 张）", "Receipt photos (optional, up to 3)")}
        <div className="upload-list">
          {uploads.map((u, i) => (
            <span key={i} className={`upload-chip ${u.status}`}>
              {u.name} · {u.status === "ready" ? tr("已通过", "Ready") : u.status === "uploading" ? tr("上传中", "Uploading") : u.reason}
              <button type="button" aria-label={tr("移除照片", "Remove photo")} onClick={() => setUploads((x) => x.filter((y) => y !== u))}>×</button>
            </span>
          ))}
          {uploads.length < 3 && (
            <label className="upload-button">
              <Icon name="upload" /> {tr("添加照片", "Add photo")}
              <input type="file" accept="image/jpeg,image/png,image/webp" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) void addPhoto(f); e.target.value = ""; }} />
            </label>
          )}
        </div>
      </div>
      <div className="conversion-strip" aria-live="polite">
        <div>{tr("合 NZD", "≈ NZD")}<strong>{ok ? `$ ${money(ok.display_amounts.nzd)}` : "—"}</strong></div>
        <div>{tr("合 CNY", "≈ CNY")}<strong>{ok ? `¥ ${money(ok.display_amounts.cny)}` : "—"}</strong></div>
        <div>{tr("汇率日期", "Rate date")}<strong className="small-strong">{ok ? ok.snapshot.effective_date : pending ? tr("待补汇率", "Rate needed") : "—"}</strong></div>
      </div>
      {ok && "snapshot_changed" in ok && mode === "edit" && (
        <p className="tiny-note">{ok.snapshot_changed ? tr("日期或币种已改变：将使用新的汇率快照。", "Date or currency changed: a new exchange-rate snapshot will be used.") : tr("沿用原汇率快照。", "Keeps the original exchange-rate snapshot.")}</p>
      )}
      {pending && <p className="tiny-note warn">{tr(`缺少 ${pending.missing_currencies.join("、")} 在该日期附近的汇率，请家庭管理员在「币种与汇率」补录后再保存。`, `No ${pending.missing_currencies.join(", ")} rate near this date. Ask a family admin to add it under Currencies & rates, then save.`)}</p>}
      <ErrorNotice error={action.error} />
      <UnknownPanel action={action as never} onCommitted={onCancel} onRetry={() => void save()} />
      <div className="form-actions">
        <button type="button" className="button secondary" onClick={onCancel}>{tr("取消", "Cancel")}</button>
        <button className="button primary" type="submit" disabled={action.busy || !amountValid || (!locked && !leaf) || (rule === "required" && !method) || uploads.some((u) => u.status === "uploading")}>
          <Icon name="check" />
          {mode === "edit" ? tr("保存修改", "Save changes") : mode === "refund" ? tr("保存退款", "Save refund") : tr("保存这笔记录", "Save entry")}
        </button>
      </div>
    </form>
  );
}


/** 修改时只提交变化的字段。 */
function diff(entry: Entry, b: EntryBody): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  if (b.business_date !== entry.business_date) out.business_date = b.business_date;
  if (b.amount !== entry.amount) out.amount = b.amount;
  if ((b.note ?? "") !== entry.note) out.note = b.note ?? "";
  const att = b.attachment_ids ?? [];
  if (att.join() !== entry.attachments.map((a) => a.attachment_id).join()) out.attachment_ids = att;
  if (entry.type === "refund") return out;
  if (b.type !== entry.type) out.type = b.type;
  if (b.currency !== entry.currency) out.currency = b.currency;
  if (b.leaf_category_id !== entry.category.leaf_id) out.leaf_category_id = b.leaf_category_id;
  if ((b.payment_method ?? null) !== (entry.payment_method ?? null)) out.payment_method = b.payment_method ?? null;
  return out;
}
