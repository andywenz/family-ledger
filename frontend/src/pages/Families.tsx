import { useState } from "react";
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
    <PlainShell title="我的家庭">
      <PageHeading eyebrow="OUR HOMES" title="选择一个家庭账本。" subtitle="一个账号可以加入多个家庭；每个家庭的账目彼此独立。" />
      <div className="settings-grid">
        <section className="card">
          <div className="section-head"><h2>我的家庭</h2></div>
          {me?.families.length === 0 && <p className="muted">还没有加入任何家庭。可以创建一个，或等待家庭管理员邀请。</p>}
          {me?.families.map((f) => (
            <button key={f.family_id} className="member-row as-button" onClick={() => navigate(`/f/${f.family_id}`)}>
              <span className="avatar">⌂</span><div><strong>{f.name}</strong><small>{f.role === "admin" ? "家庭管理员" : "成员"}</small></div>
            </button>
          ))}
          <form onSubmit={submit} className="space-top">
            <label className="field">新家庭名称<input value={name} maxLength={40} onChange={(e) => setName(e.target.value)} required /></label>
            <ErrorNotice error={create.error} />
            <button className="button primary" disabled={create.busy || !name.trim()}>创建家庭</button>
            <p className="tiny-note">创建者成为家庭管理员，并按初始方案生成分类目录。</p>
          </form>
        </section>
        <section className="card">
          <div className="section-head"><h2>收到的邀请</h2></div>
          <ErrorNotice error={invites.error ?? respond.error} />
          {invites.data?.items.length === 0 && <p className="muted">暂无待处理的邀请。</p>}
          {invites.data?.items.map((inv) => (
            <div key={inv.invitation_id} className="member-row">
              <div><strong>{inv.family_name}</strong><small>角色：{inv.role === "admin" ? "管理员" : "成员"} · {inv.expires_at.slice(0, 10)} 前有效</small></div>
              <div className="row-actions">
                <button className="button secondary small" onClick={() => void answer(inv, false)}>拒绝</button>
                <button className="button primary small" onClick={() => void answer(inv, true)}>加入</button>
              </div>
            </div>
          ))}
        </section>
      </div>
    </PlainShell>
  );
}
