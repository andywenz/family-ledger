import { useRef } from "react";
import { plural, tr } from "../lib/i18n";
import { api, type Entry } from "../api/client";
import { ErrorNotice, PageHeading, UnknownPanel, useAction, useLoad, useToast } from "../components/common";
import { useFamily } from "../components/Layout";
import { money } from "../lib/format";
import { typeLabel } from "../lib/labels";

export default function Trash() {
  const fam = useFamily();
  const toast = useToast();
  const { data, error, reload } = useLoad(() => api.get<{ items: Entry[] }>(`/families/${fam.fid}/trash`), [fam.fid]);
  const action = useAction<Entry>(fam.fid);
  const pending = useRef<Entry | null>(null);

  async function restore(e: Entry) {
    pending.current = e;
    const r = await action.run((key) => api.post<Entry>(`/families/${fam.fid}/entries/${e.entry_id}/restore`, { expected_version: e.version }, { key }));
    if (r) { toast.show(tr("已恢复", "Restored")); await reload(); }
  }

  const daysLeft = (iso?: string) => (iso ? Math.max(0, Math.ceil((Date.parse(iso) - Date.now()) / 86400000)) : 0);

  return (
    <>
      <PageHeading title={tr("回收站", "Trash")} subtitle={tr("删除的账目保留 30 天，期间可以恢复；之后会被清理，照片无其他引用时一并删除。", "Deleted entries are kept for 30 days and can be restored. After that they’re removed, along with any photos nothing else uses.")} />
      <ErrorNotice error={error ?? action.error} onRetry={reload} />
      <UnknownPanel action={action as never} onCommitted={() => void reload()} onRetry={() => { if (pending.current) void restore(pending.current); }} />
      <section className="card ledger-card">
        <div className="table-scroll">
          <table>
            <thead><tr><th>{tr("日期", "Date")}</th><th>{tr("类型", "Type")}</th><th>{tr("分类", "Category")}</th><th className="numeric">{tr("金额", "Amount")}</th><th>{tr("备注", "Note")}</th><th>{tr("录入人", "Entered by")}</th><th>{tr("剩余天数", "Days left")}</th><th>{tr("操作", "Actions")}</th></tr></thead>
            <tbody>
              {data?.items.map((e) => (
                <tr key={e.entry_id}>
                  <td>{e.business_date}</td><td>{typeLabel(e.type)}</td>
                  <td>{e.category.parent_name}／{e.category.leaf_name}</td>
                  <td className="numeric">{e.currency} {money(e.amount)}</td>
                  <td>{e.note}</td><td>{e.created_by_display}</td>
                  <td>{tr(`${daysLeft(e.purge_after)} 天`, plural(daysLeft(e.purge_after), "day", "days"))}</td>
                  <td>{e.can_edit ? <button className="edit-row" onClick={() => void restore(e)}>{tr("恢复", "Restore")}</button> : "—"}</td>
                </tr>
              ))}
              {data?.items.length === 0 && <tr><td colSpan={8} className="empty">{tr("回收站是空的。", "Trash is empty.")}</td></tr>}
            </tbody>
          </table>
        </div>
      </section>
      {toast.node}
    </>
  );
}
