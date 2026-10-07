import { useState } from "react";
import { plural, tr } from "../lib/i18n";
import { api, type Category } from "../api/client";
import { ErrorNotice, Modal, PageHeading, useAction, useToast } from "../components/common";
import { Icon } from "../components/Icon";
import { useFamily } from "../components/Layout";
import { kindLabel, kindLabels } from "../lib/labels";

type Edit = { cat: Category; mode: "rename" | "move" | "merge" | "add" };

export default function Categories() {
  const fam = useFamily();
  const toast = useToast();
  const [kind, setKind] = useState("expense");
  const [edit, setEdit] = useState<Edit | null>(null);
  const [value, setValue] = useState("");
  const [showInactive, setShowInactive] = useState(false);
  const action = useAction<unknown>(fam.fid);
  const isAdmin = fam.family.my_role === "admin";
  const groups = fam.categories.groups.filter((g) => g.kind === kind && (showInactive || g.status === "active"));

  async function call(fn: (key: string) => Promise<unknown>, msg: string) {
    const r = await action.run(fn);
    if (r !== undefined) { setEdit(null); toast.show(msg); await fam.reload(); }
  }

  const base = `/families/${fam.fid}/categories`;
  return (
    <>
      <PageHeading title={tr("分类管理", "Categories")} subtitle={tr("改名或移动会同步更新历史账目的显示，金额与汇率不变。", "Renaming or moving updates how past entries display; amounts and rates don’t change.")}
        actions={isAdmin ? <button className="button primary" onClick={() => { setValue(""); setEdit({ cat: { category_id: "", kind: kind as Category["kind"], name: "", status: "active", sort: 0, version: 1 }, mode: "add" }); }}><Icon name="plus" />{tr("新增一级分类", "New top-level category")}</button> : undefined} />
      <div className="notice"><Icon name="info" /><div>{tr("已被账目使用的分类不能直接删除：请先合并到其他分类，或停用（历史仍可查看，新账目不能选择）。", "Categories in use can’t be deleted. Merge them into another category, or disable them (past entries still show them; new entries can’t use them).")}{!isAdmin && tr(" 只有家庭管理员可以修改分类。", " Only family admins can change categories.")}</div></div>
      <div className="tabs kind-tabs">
        {kindLabels().map(([k, l]) => <button key={k} className={k === kind ? "selected" : ""} onClick={() => setKind(k)}>{l}</button>)}
      </div>
      <label className="inline-check"><input type="checkbox" checked={showInactive} onChange={(e) => setShowInactive(e.target.checked)} />{tr("显示已停用", "Show disabled")}</label>
      <ErrorNotice error={action.error} />
      <div className="category-grid">
        {groups.map((g) => (
          <article key={g.category_id} className="card category-card">
            <div className="section-head">
              <div className="category-title"><h2>{g.name}{g.status !== "active" && <small>{tr("（已停用）", " (disabled)")}</small>}</h2></div>
              {isAdmin && <button className="icon-button" aria-label={tr(`修改${g.name}`, `Rename ${g.name}`)} onClick={() => { setValue(g.name); setEdit({ cat: g, mode: "rename" }); }}><Icon name="edit" /></button>}
            </div>
            {g.children.filter((c) => showInactive || c.status === "active").map((c) => (
              <div key={c.category_id} className="category-row">
                <span>{c.name}{c.status !== "active" && <small>{c.redirect_to ? tr("（已合并）", " (merged)") : tr("（已停用）", " (disabled)")}</small>}</span>
                <small>{tr(`${c.usage_count ?? 0} 笔`, plural(c.usage_count ?? 0, "entry", "entries"))}</small>
                {isAdmin && c.status === "active" && (
                  <div className="row-actions">
                    <button className="icon-button" aria-label={tr(`改名${c.name}`, `Rename ${c.name}`)} title={tr("改名", "Rename")} onClick={() => { setValue(c.name); setEdit({ cat: c, mode: "rename" }); }}><Icon name="edit" /></button>
                    <button className="icon-button" aria-label={tr(`移动或合并${c.name}`, `Move or merge ${c.name}`)} title={tr("移动／合并", "Move / merge")} onClick={() => { setValue(""); setEdit({ cat: c, mode: "move" }); }}><Icon name="arrow" /></button>
                    <button className="icon-button" aria-label={tr(`停用${c.name}`, `Disable ${c.name}`)} title={tr("停用", "Disable")} onClick={() => void call((key) => api.patch(`${base}/${c.category_id}`, { status: "disabled", expected_version: c.version }, { key }), tr("已停用", "Disabled"))}>⊘</button>
                    {(c.usage_count ?? 0) === 0 && <button className="icon-button" aria-label={tr(`删除${c.name}`, `Delete ${c.name}`)} title={tr("删除", "Delete")} onClick={() => void call((key) => api.del(`${base}/${c.category_id}`, { key, query: { expected_version: c.version } }), tr("已删除", "Deleted"))}><Icon name="trash" /></button>}
                  </div>
                )}
                {isAdmin && c.status === "disabled" && !c.redirect_to && (
                  <button className="text-button" onClick={() => void call((key) => api.patch(`${base}/${c.category_id}`, { status: "active", expected_version: c.version }, { key }), tr("已重新启用", "Re-enabled"))}>{tr("启用", "Enable")}</button>
                )}
              </div>
            ))}
            {isAdmin && g.status === "active" && <button className="new-category" onClick={() => { setValue(""); setEdit({ cat: g, mode: "add" }); }}>{tr("＋ 添加二级分类", "＋ Add subcategory")}</button>}
          </article>
        ))}
      </div>
      {edit && (
        <Modal title={edit.mode === "rename" ? tr("修改名称", "Rename") : edit.mode === "add" ? tr("新增分类", "New category") : tr("移动或合并", "Move or merge")} onClose={() => setEdit(null)}>
          {(edit.mode === "rename" || edit.mode === "add") && (
            <form onSubmit={(e) => {
              e.preventDefault();
              if (edit.mode === "rename") void call((key) => api.patch(`${base}/${edit.cat.category_id}`, { name: value, expected_version: edit.cat.version }, { key }), tr("已改名，历史账目同步显示新名称", "Renamed; past entries now show the new name"));
              else void call((key) => api.post(base, { kind, name: value, ...(edit.cat.category_id ? { parent_id: edit.cat.category_id } : {}) }, { key }), tr("已新增", "Added"));
            }}>
              <label className="field">{tr("名称", "Name")}<input value={value} maxLength={24} onChange={(e) => setValue(e.target.value)} required autoFocus /></label>
              <ErrorNotice error={action.error} />
              <div className="form-actions"><button type="button" className="button secondary" onClick={() => setEdit(null)}>{tr("取消", "Cancel")}</button><button className="button primary">{tr("保存", "Save")}</button></div>
            </form>
          )}
          {edit.mode === "move" && (
            <>
              <p className="tiny-note">{tr(`移动：把「${edit.cat.name}」放到另一个一级分类下（只能在${kindLabel(kind)}内移动）。`, `Move: put “${edit.cat.name}” under another top-level category (within ${kindLabel(kind)} only).`)}<br />{tr("合并：把它的所有历史账目归到另一个二级分类，原分类停用。", "Merge: move all its past entries to another subcategory and disable this one.")}</p>
              <label className="field">{tr("目标", "Target")}
                <select value={value} onChange={(e) => setValue(e.target.value)}>
                  <option value="">{tr("请选择", "Choose")}</option>
                  <optgroup label={tr("移动到一级分类", "Move to top-level category")}>
                    {fam.categories.groups.filter((g) => g.kind === kind && g.status === "active" && g.category_id !== edit.cat.parent_id).map((g) => <option key={g.category_id} value={`move:${g.category_id}`}>{g.name}</option>)}
                  </optgroup>
                  <optgroup label={tr("合并到二级分类", "Merge into subcategory")}>
                    {fam.categories.groups.filter((g) => g.kind === kind).flatMap((g) => g.children.filter((c) => c.status === "active" && c.category_id !== edit.cat.category_id).map((c) => <option key={c.category_id} value={`merge:${c.category_id}`}>{g.name}／{c.name}</option>))}
                  </optgroup>
                </select>
              </label>
              <ErrorNotice error={action.error} />
              <div className="form-actions">
                <button className="button secondary" onClick={() => setEdit(null)}>{tr("取消", "Cancel")}</button>
                <button className="button primary" disabled={!value} onClick={() => {
                  const [op, target] = value.split(":");
                  if (op === "move") void call((key) => api.patch(`${base}/${edit.cat.category_id}`, { parent_id: target, expected_version: edit.cat.version }, { key }), tr("已移动，分类汇总同步更新", "Moved; category totals updated"));
                  else void call((key) => api.post(`${base}/${edit.cat.category_id}/merge`, { target_id: target, expected_version: edit.cat.version }, { key }), tr("已合并", "Merged"));
                }}>{tr("确认", "Confirm")}</button>
              </div>
            </>
          )}
        </Modal>
      )}
      {toast.node}
    </>
  );
}
