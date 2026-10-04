import { useState } from "react";
import { api, type Entry } from "../api/client";
import { money } from "../lib/format";
import { METHOD_LABEL, TYPE_LABEL } from "../lib/labels";
import { ErrorNotice, Modal, UnknownPanel, useAction } from "./common";
import { EntryForm } from "./EntryForm";
import { Icon } from "./Icon";
import { useFamily } from "./Layout";

type Props = { entry: Entry; onClose: () => void; onChanged: (message: string, month?: string) => void };

export function EntryDialog({ entry, onClose, onChanged }: Props) {
  const fam = useFamily();
  const [mode, setMode] = useState<"view" | "edit" | "refund" | "delete">("view");
  const action = useAction<unknown>(fam.fid);
  const hasRefunds = (entry.refund_ids ?? []).length > 0;

  async function viewPhoto(id: string) {
    const link = await api.get<{ url: string }>(`/families/${fam.fid}/attachments/${id}/download`);
    window.open(link.url, "_blank", "noopener");
  }

  async function remove() {
    const ok = await action.run((key) =>
      api.del(`/families/${fam.fid}/entries/${entry.entry_id}`, {
        key, query: { expected_version: entry.version, with_refunds: hasRefunds },
      }),
    );
    if (ok !== undefined) onChanged(hasRefunds ? "已将该消费及关联退款移入回收站（30 天内可恢复）" : "已移入回收站（30 天内可恢复）");
  }

  if (mode === "edit" || mode === "refund") {
    return (
      <Modal title={mode === "edit" ? "修改记录" : "录入退款"} onClose={onClose}>
        <EntryForm mode={mode} entry={entry} onCancel={() => setMode("view")} onSaved={(saved) =>
          onChanged(mode === "edit" ? "已更新原记录与月度统计" : "已保存退款", saved.business_date.slice(0, 7))} />
      </Modal>
    );
  }

  const snap = entry.fx_snapshot;
  return (
    <Modal title="账目详情" onClose={onClose}>
      <dl className="detail-list">
        <dt>日期</dt><dd>{entry.business_date}</dd>
        <dt>类型</dt><dd>{TYPE_LABEL[entry.type]}{!entry.counts_in_stats && "（不计收支）"}</dd>
        <dt>分类</dt><dd>{entry.category.parent_name}／{entry.category.leaf_name}{entry.category.leaf_status !== "active" && "（已停用）"}</dd>
        <dt>原始金额</dt><dd>{entry.currency} {money(entry.amount)}</dd>
        <dt>折算</dt><dd>NZD {money(entry.display_amounts.nzd)} · CNY {money(entry.display_amounts.cny)}</dd>
        <dt>汇率</dt><dd>{snap.effective_date}{snap.effective_date !== snap.requested_date && `（请求 ${snap.requested_date}，取最近有效日）`}{snap.manual && " · 含家庭修正"}</dd>
        <dt>方式</dt><dd>{entry.payment_method ? METHOD_LABEL[entry.payment_method] : "—"}</dd>
        <dt>备注</dt><dd className="pre">{entry.note || "—"}</dd>
        <dt>录入人</dt><dd>{entry.created_by_display}{entry.created_by_left && "（已离开）"}</dd>
        {entry.type === "expense" && (
          <>
            <dt>已退款</dt><dd>{entry.currency} {money(entry.refunded_amount ?? "0")}（尚可退 {money(entry.refundable_amount ?? "0")}）</dd>
          </>
        )}
      </dl>
      {entry.attachments.length > 0 && (
        <div className="photo-row">
          {entry.attachments.map((a, i) => (
            <button key={a.attachment_id} className="button secondary small" onClick={() => void viewPhoto(a.attachment_id)}>
              <Icon name="upload" />查看照片 {i + 1}
            </button>
          ))}
          <small className="tiny-note">查看链接 60 秒内有效。</small>
        </div>
      )}
      {mode === "delete" && (
        <div className="notice" role="alert">
          <Icon name="info" />
          <div>
            {hasRefunds ? (
              <strong>该消费有 {entry.refund_ids!.length} 笔关联退款，将一并移入回收站。恢复消费时不会自动恢复退款。</strong>
            ) : (
              <strong>确认将这笔记录移入回收站？30 天内可恢复。</strong>
            )}
          </div>
        </div>
      )}
      <ErrorNotice error={action.error} />
      <UnknownPanel action={action as never} onCommitted={() => onChanged("已确认删除完成")} onRetry={() => void remove()} />
      <div className="form-actions">
        <button className="button secondary" onClick={onClose}>关闭</button>
        {entry.type === "expense" && Number(entry.refundable_amount ?? 0) > 0 && (
          <button className="button secondary" onClick={() => setMode("refund")}><Icon name="undo" />退款</button>
        )}
        {entry.can_edit && mode !== "delete" && (
          <button className="button secondary danger" onClick={() => setMode("delete")}><Icon name="trash" />删除</button>
        )}
        {entry.can_edit && mode === "delete" && (
          <button className="button primary danger" disabled={action.busy} onClick={() => void remove()}>确认删除</button>
        )}
        {entry.can_edit && <button className="button primary" onClick={() => setMode("edit")}><Icon name="edit" />编辑</button>}
      </div>
    </Modal>
  );
}
