import { useState } from "react";
import { tr } from "../lib/i18n";
import { useNavigate } from "react-router";
import { api, type Schemas } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { ErrorNotice, PageHeading, useAction, useLoad } from "../components/common";
import { PlainShell } from "../components/Layout";

export default function Families() {
  const { me, reloadMe } = useAuth();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const create = useAction<Schemas["Family"]>();
  const respond = useAction<unknown>();
  const invites = useLoad(() => api.get<{ items: Schemas["Invitation"][] }>("/me/invitations"), []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const fam = await create.run((key) => api.post<Schemas["Family"]>("/families", { name }, { key }));
    if (fam) {
      await reloadMe();
      navigate(`/f/${fam.family_id}`);
    }
  }

  async function answer(inv: Schemas["Invitation"], accept: boolean) {
    const ok = await respond.run((key) => api.post(`/families/${inv.family_id}/invitations/${inv.invitation_id}/${accept ? "accept" : "decline"}`, undefined, { key }));
    if (ok !== undefined) {
      await reloadMe();
      await invites.reload();
      if (accept) navigate(`/f/${inv.family_id}`);
    }
  }

  return (
    <PlainShell title={tr("我的家庭", "My families")}>
      <PageHeading title={tr("选择家庭", "Choose a family")} subtitle={tr("一个账号可以加入多个家庭；每个家庭的账目彼此独立。", "One account can join several families; each family's ledger is separate.")} />
      <div className="settings-grid">
        <section className="card">
          <div className="section-head"><h2>{tr("我的家庭", "My families")}</h2></div>
          {me?.families.length === 0 && <p className="muted">{tr("还没有加入任何家庭。可以创建一个，或等待家庭管理员邀请。", "You haven't joined a family yet. Create one, or wait for a family admin to invite you.")}</p>}
          {me?.families.map((f) => (
            <button key={f.family_id} className="member-row as-button" onClick={() => navigate(`/f/${f.family_id}`)}>
              <span className="avatar">⌂</span><div><strong>{f.name}</strong><small>{f.role === "admin" ? tr("家庭管理员", "Family admin") : tr("成员", "Member")}</small></div>
            </button>
          ))}
          <form onSubmit={submit} className="space-top">
            <label className="field">{tr("新家庭名称", "New family name")}<input value={name} maxLength={40} onChange={(e) => setName(e.target.value)} required /></label>
            <ErrorNotice error={create.error} />
            <button className="button primary" disabled={create.busy || !name.trim()}>{tr("创建家庭", "Create family")}</button>
            <p className="tiny-note">{tr("创建者成为家庭管理员，并按初始方案生成分类目录。", "The creator becomes the family admin, and a starter set of categories is created.")}</p>
          </form>
        </section>
        <section className="card">
          <div className="section-head"><h2>{tr("收到的邀请", "Invitations")}</h2></div>
          <ErrorNotice error={invites.error ?? respond.error} />
          {invites.data?.items.length === 0 && <p className="muted">{tr("暂无待处理的邀请。", "No pending invitations.")}</p>}
          {invites.data?.items.map((inv) => (
            <div key={inv.invitation_id} className="member-row">
              <div><strong>{inv.family_name}</strong><small>{tr("角色", "Role")}：{inv.role === "admin" ? tr("管理员", "Admin") : tr("成员", "Member")} · {tr(`${inv.expires_at.slice(0, 10)} 前有效`, `Valid until ${inv.expires_at.slice(0, 10)}`)}</small></div>
              <div className="row-actions">
                <button className="button secondary small" onClick={() => void answer(inv, false)}>{tr("拒绝", "Decline")}</button>
                <button className="button primary small" onClick={() => void answer(inv, true)}>{tr("加入", "Join")}</button>
              </div>
            </div>
          ))}
        </section>
      </div>
    </PlainShell>
  );
}
