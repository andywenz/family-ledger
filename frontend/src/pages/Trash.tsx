import { useRef } from "react";
import { api, type Entry } from "../api/client";
import { ErrorNotice, PageHeading, UnknownPanel, useAction, useLoad, useToast } from "../components/common";
import { useFamily } from "../components/Layout";
import { money } from "../lib/format";
import { TYPE_LABEL } from "../lib/labels";

export default function Trash() {
  const fam = useFamily();
  const toast = useToast();
  const { data, error, reload } = useLoad(() => api.get<{ items: Entry[] }>(`/families/${fam.fid}/trash`), [fam.fid]);
  const action = useAction<Entry>(fam.fid);
  const pending = useRef<Entry | null>(null);

  async function restore(e: Entry) {
    pending.current = e;
    const r = await action.run((key) => api.post<Entry>(`/families/${fam.fid}/entries/${e.entry_id}/restore`, { expected_version: e.version }, { key }));
    if (r) { toast.show("已恢复"); await reload(); }
  }

  const daysLeft = (iso?: string) => (iso ? Math.max(0, Math.ceil((Date.parse(iso) - Date.now()) / 86400000)) : 0);

  return (
    <>
      <PageHeading title="回收站" subtitle="删除的账目保留 30 天，期间可以恢复；之后会被清理，照片无其他引用时一并删除。" />
      <ErrorNotice error={error ?? action.error} onRetry={reload} />
      <UnknownPanel action={action as never} onCommitted={() => void reload()} onRetry={() => { if (pending.current) void restore(pending.current); }} />
      <section className="card ledger-card">
        <div className="table-scroll">
          <table>
            <thead><tr><th>日期</th><th>类型</th><th>分类</th><th className="numeric">金额</th><th>备注</th><th>录入人</th><th>剩余天数</th><th>操作</th></tr></thead>
            <tbody>
              {data?.items.map((e) => (
                <tr key={e.entry_id}>
                  <td>{e.business_date}</td><td>{TYPE_LABEL[e.type]}</td>
                  <td>{e.category.parent_name}／{e.category.leaf_name}</td>
                  <td className="numeric">{e.currency} {money(e.amount)}</td>
                  <td>{e.note}</td><td>{e.created_by_display}</td>
                  <td>{daysLeft(e.purge_after)} 天</td>
                  <td>{e.can_edit ? <button className="edit-row" onClick={() => void restore(e)}>恢复</button> : "—"}</td>
                </tr>
              ))}
              {data?.items.length === 0 && <tr><td colSpan={8} className="empty">回收站是空的。</td></tr>}
            </tbody>
          </table>
        </div>
      </section>
      {toast.node}
    </>
  );
}
