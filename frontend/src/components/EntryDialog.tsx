import { useState } from "react";
import { plural, tr } from "../lib/i18n";
import { api, type Entry } from "../api/client";
import { money } from "../lib/format";
import { methodLabel, typeLabel } from "../lib/labels";
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
    if (ok !== undefined) onChanged(hasRefunds ? tr("已将该消费及关联退款移入回收站（30 天内可恢复）", "Moved the expense and its refunds to Trash (restorable for 30 days)") : tr("已移入回收站（30 天内可恢复）", "Moved to Trash (restorable for 30 days)"));
  }

  if (mode === "edit" || mode === "refund") {
    return (
      <Modal title={mode === "edit" ? tr("修改记录", "Edit entry") : tr("录入退款", "Add refund")} onClose={onClose}>
        <EntryForm mode={mode} entry={entry} onCancel={() => setMode("view")} onSaved={(saved) =>
          onChanged(mode === "edit" ? tr("已更新原记录与月度统计", "Entry and monthly totals updated") : tr("已保存退款", "Refund saved"), saved.business_date.slice(0, 7))} />
      </Modal>
    );
  }

  const snap = entry.fx_snapshot;
  return (
    <Modal title={tr("账目详情", "Entry details")} onClose={onClose}>
      <dl className="detail-list">
        <dt>{tr("日期", "Date")}</dt><dd>{entry.business_date}</dd>
        <dt>{tr("类型", "Type")}</dt><dd>{typeLabel(entry.type)}{!entry.counts_in_stats && tr("（不计收支）", " (not in totals)")}</dd>
        <dt>{tr("分类", "Category")}</dt><dd>{entry.category.parent_name}／{entry.category.leaf_name}{entry.category.leaf_status !== "active" && tr("（已停用）", " (disabled)")}</dd>
        <dt>{tr("原始金额", "Amount")}</dt><dd>{entry.currency} {money(entry.amount)}</dd>
        <dt>{tr("折算", "Converted")}</dt><dd>NZD {money(entry.display_amounts.nzd)} · CNY {money(entry.display_amounts.cny)}</dd>
        <dt>{tr("汇率", "Rate")}</dt><dd>{snap.effective_date}{snap.effective_date !== snap.requested_date && tr(`（请求 ${snap.requested_date}，取最近有效日）`, ` (requested ${snap.requested_date}; nearest available date used)`)}{snap.manual && tr(" · 含家庭修正", " · includes family override")}</dd>
        <dt>{tr("方式", "Method")}</dt><dd>{entry.payment_method ? methodLabel(entry.payment_method) : "—"}</dd>
        <dt>{tr("备注", "Note")}</dt><dd className="pre">{entry.note || "—"}</dd>
        <dt>{tr("录入人", "Entered by")}</dt><dd>{entry.created_by_display}{entry.created_by_left && tr("（已离开）", " (left)")}</dd>
        {entry.type === "expense" && (
          <>
            <dt>{tr("已退款", "Refunded")}</dt><dd>{entry.currency} {money(entry.refunded_amount ?? "0")}{tr(`（尚可退 ${money(entry.refundable_amount ?? "0")}）`, ` (${money(entry.refundable_amount ?? "0")} still refundable)`)}</dd>
          </>
        )}
      </dl>
      {entry.attachments.length > 0 && (
        <div className="photo-row">
          {entry.attachments.map((a, i) => (
            <button key={a.attachment_id} className="button secondary small" onClick={() => void viewPhoto(a.attachment_id)}>
              <Icon name="upload" />{tr(`查看照片 ${i + 1}`, `View photo ${i + 1}`)}
            </button>
          ))}
          <small className="tiny-note">{tr("查看链接 60 秒内有效。", "Photo links expire after 60 seconds.")}</small>
        </div>
      )}
      {mode === "delete" && (
        <div className="notice" role="alert">
          <Icon name="info" />
          <div>
            {hasRefunds ? (
              <strong>{tr(`该消费有 ${entry.refund_ids!.length} 笔关联退款，将一并移入回收站。恢复消费时不会自动恢复退款。`, `This expense has ${plural(entry.refund_ids!.length, "linked refund", "linked refunds")}, which will also move to Trash. Restoring the expense won’t restore the refunds.`)}</strong>
            ) : (
              <strong>{tr("确认将这笔记录移入回收站？30 天内可恢复。", "Move this entry to Trash? You can restore it within 30 days.")}</strong>
            )}
          </div>
        </div>
      )}
      <ErrorNotice error={action.error} />
      <UnknownPanel action={action as never} onCommitted={() => onChanged(tr("已确认删除完成", "Deletion confirmed"))} onRetry={() => void remove()} />
      <div className="form-actions">
        <button className="button secondary" onClick={onClose}>{tr("关闭", "Close")}</button>
        {entry.type === "expense" && Number(entry.refundable_amount ?? 0) > 0 && (
          <button className="button secondary" onClick={() => setMode("refund")}><Icon name="undo" />{tr("退款", "Refund")}</button>
        )}
        {entry.can_edit && mode !== "delete" && (
          <button className="button secondary danger" onClick={() => setMode("delete")}><Icon name="trash" />{tr("删除", "Delete")}</button>
        )}
        {entry.can_edit && mode === "delete" && (
          <button className="button primary danger" disabled={action.busy} onClick={() => void remove()}>{tr("确认删除", "Delete")}</button>
        )}
        {entry.can_edit && <button className="button primary" onClick={() => setMode("edit")}><Icon name="edit" />{tr("编辑", "Edit")}</button>}
      </div>
    </Modal>
  );
}
