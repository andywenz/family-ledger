import { useState } from "react";
import { api, type Schemas } from "../api/client";
import { ErrorNotice, PageHeading, useAction, useLoad } from "../components/common";
import { PlainShell } from "../components/Layout";

type User = Schemas["AdminUser"];

export default function AdminUsers() {
  const users = useLoad(() => api.get<{ items: User[] }>("/admin/users"), []);
  const action = useAction<Schemas["TemporaryPasswordResult"]>();
  const [login, setLogin] = useState("");
  const [display, setDisplay] = useState("");
  const [secret, setSecret] = useState<{ login: string; password: string } | null>(null);

  async function issue(fn: (key: string) => Promise<Schemas["TemporaryPasswordResult"]>) {
    const r = await action.run(fn);
    if (r?.temporary_password) setSecret({ login: r.user.login_name, password: r.temporary_password });
    await users.reload();
  }

  return (
    <PlainShell title="系统账号">
      <PageHeading eyebrow="SYSTEM ADMIN" title="系统账号" subtitle="没有公开注册。系统管理员创建账号并私下交付临时密码；首次登录必须修改。" />
      <ErrorNotice error={users.error ?? action.error} />
      {secret && (
        <div className="notice secret-notice" role="alert">
          <div>
            <strong>{secret.login} 的临时密码（只显示这一次）：</strong>
            <code>{secret.password}</code>
            <p>请通过可信的私人渠道交付，不要截图或发到群聊。</p>
            <button className="button secondary small" onClick={() => setSecret(null)}>我已记下</button>
          </div>
        </div>
      )}
      <div className="settings-grid">
        <section className="card">
          <div className="section-head"><h2>创建账号</h2></div>
          <form onSubmit={(e) => { e.preventDefault(); void issue((key) => api.post("/admin/users", { login_name: login, display_name: display }, { key })).then(() => { setLogin(""); setDisplay(""); }); }}>
            <div className="field-grid">
              <label className="field">登录名<input value={login} onChange={(e) => setLogin(e.target.value)} required /></label>
              <label className="field">显示名<input value={display} maxLength={24} onChange={(e) => setDisplay(e.target.value)} required /></label>
            </div>
            <button className="button primary" disabled={action.busy}>创建</button>
          </form>
        </section>
        <section className="card">
          <div className="section-head"><h2>全部账号</h2></div>
          {users.data?.items.map((u) => (
            <div key={u.user_id} className="member-row">
              <span className="avatar">{u.display_name.slice(0, 1)}</span>
              <div><strong>{u.display_name}</strong><small>{u.login_name}{u.is_system_admin && " · 系统管理员"}{u.must_change_password && " · 待首次改密"}{u.status === "disabled" && " · 已停用"}</small></div>
              <div className="row-actions">
                <button className="button secondary small" onClick={() => { if (confirm(`重置 ${u.login_name} 的密码？其现有登录会立即失效。`)) void issue((key) => api.post(`/admin/users/${u.user_id}/reset-password`, undefined, { key })); }}>重置密码</button>
                <button className="button secondary small" onClick={() => void action.run((key) => api.patch(`/admin/users/${u.user_id}`, { status: u.status === "active" ? "disabled" : "active", expected_version: u.version }, { key })).then(() => users.reload())}>
                  {u.status === "active" ? "停用" : "启用"}
                </button>
              </div>
            </div>
          ))}
        </section>
      </div>
    </PlainShell>
  );
}
