import { useState } from "react";
import { api, type Member, type Schemas } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { ErrorNotice, PageHeading, useAction, useLoad, useToast } from "../components/common";
import { useFamily } from "../components/Layout";
import { METHODS } from "../lib/labels";

export default function FamilySettings() {
  const fam = useFamily();
  const { me, reloadMe } = useAuth();
  const toast = useToast();
  const isAdmin = fam.family.my_role === "admin";
  const action = useAction<unknown>(fam.fid);
  const [login, setLogin] = useState("");
  const [role, setRole] = useState("member");
  const [cur, setCur] = useState(fam.config.default_currency);
  const [method, setMethod] = useState(fam.config.default_payment_method);
  const [secondary, setSecondary] = useState(fam.config.secondary_currency ?? "");
  const invites = useLoad(() => (isAdmin ? api.get<{ items: Schemas["Invitation"][] }>(`/families/${fam.fid}/invitations`) : Promise.resolve({ items: [] })), [fam.fid, isAdmin]);

  async function call(fn: (key: string) => Promise<unknown>, msg: string) {
    const r = await action.run(fn);
    if (r !== undefined) { toast.show(msg); await fam.reload(); await invites.reload(); await reloadMe(); }
  }

  const memberAction = (m: Member) => (
    <div className="row-actions">
      <button className="button secondary small" onClick={() => void call((key) => api.patch(`/families/${fam.fid}/members/${m.user_id}`, { role: m.role === "admin" ? "member" : "admin", expected_version: m.version }, { key }), "角色已更新")}>
        {m.role === "admin" ? "撤销管理员" : "设为管理员"}
      </button>
      <button className="button secondary small danger" onClick={() => {
        if (confirm(`移除 ${m.display_name}？其历史账目保留在家庭中，但将立即无法访问。`)) void call((key) => api.del(`/families/${fam.fid}/members/${m.user_id}`, { key, query: { expected_version: m.version } }), "已移除成员");
      }}>移除</button>
    </div>
  );

  return (
    <>
      <PageHeading title="家庭与设置" subtitle="管理成员、邀请新成员，设定默认币种和付款方式。" />
      <ErrorNotice error={action.error} />
      <div className="settings-grid">
        <div>
          <section className="card">
            <div className="section-head"><h2>{fam.family.name}</h2><span className="badge">{fam.members.filter((m) => m.status === "active").length} 位成员</span></div>
            {fam.members.filter((m) => m.status === "active").map((m) => (
              <div key={m.user_id} className="member-row">
                <span className="avatar">{m.display_name.slice(0, 1)}</span>
                <div><strong>{m.display_name}{m.user_id === me?.user_id && "（我）"}</strong><small>{m.login_name}</small></div>
                <span className="badge">{m.role === "admin" ? "管理员" : "成员"}</span>
                {isAdmin && memberAction(m)}
              </div>
            ))}
            {fam.members.some((m) => m.status !== "active") && (
              <p className="tiny-note">已离开：{fam.members.filter((m) => m.status !== "active").map((m) => m.display_name).join("、")}（历史账目保留）</p>
            )}
          </section>
          {isAdmin && (
            <section className="card space-top">
              <div className="section-head"><h2>邀请成员</h2><small>对方需已有账号</small></div>
              <form onSubmit={(e) => { e.preventDefault(); void call((key) => api.post(`/families/${fam.fid}/invitations`, { login_name: login, role }, { key }), "已发出邀请（7 天内有效）").then(() => setLogin("")); }}>
                <div className="field-grid">
                  <label className="field">登录名<input value={login} onChange={(e) => setLogin(e.target.value)} required /></label>
                  <label className="field">角色<select value={role} onChange={(e) => setRole(e.target.value)}><option value="member">成员</option><option value="admin">管理员</option></select></label>
                </div>
                <button className="button primary">发送邀请</button>
              </form>
              {invites.data?.items.filter((i) => i.status === "pending").map((i) => (
                <div key={i.invitation_id} className="member-row">
                  <div><strong>待接受</strong><small>{i.expires_at.slice(0, 10)} 前有效</small></div>
                  <button className="button secondary small" onClick={() => void call((key) => api.del(`/families/${fam.fid}/invitations/${i.invitation_id}`, { key }), "已撤销邀请")}>撤销</button>
                </div>
              ))}
            </section>
          )}
        </div>
        <section className="card">
          <div className="section-head"><h2>记账默认值</h2><small>AI 使用时会标注</small></div>
          <div className="field-grid">
            <label className="field">默认币种<select value={cur} disabled={!isAdmin} onChange={(e) => setCur(e.target.value)}>{fam.currencies.map((c) => <option key={c.code}>{c.code}</option>)}</select></label>
            <label className="field">默认消费方式<select value={method} disabled={!isAdmin} onChange={(e) => setMethod(e.target.value as typeof method)}>{METHODS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
            <label className="field">辅助币种（可选）
              <select value={secondary} disabled={!isAdmin} onChange={(e) => setSecondary(e.target.value)}>
                <option value="">不显示</option>
                {fam.currencies.filter((c) => c.code !== "NZD").map((c) => <option key={c.code} value={c.code}>{c.name_zh}（{c.code}）</option>)}
              </select>
            </label>
          </div>
          <p className="tiny-note">辅助币种决定月度总览里“合 xxx”的显示：净支出、消费方式和明细列表。选“不显示”则只显示纽币；不影响已入账的金额。</p>
          <p className="tiny-note">家庭时区 · {fam.config.timezone}<br />原始照片随有效账目长期保存。</p>
          {isAdmin && <button className="button primary" onClick={() => void call((key) => api.patch(`/families/${fam.fid}/config`, { default_currency: cur, default_payment_method: method, secondary_currency: secondary || null, expected_version: fam.config.version }, { key }), "设置已保存")}>保存设置</button>}
        </section>
      </div>
      {toast.node}
    </>
  );
}
