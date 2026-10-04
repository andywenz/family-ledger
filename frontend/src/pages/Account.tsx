import { useState } from "react";
import { ApiError, api, refreshSession } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { ErrorNotice, PageHeading, useAction, useToast } from "../components/common";
import { FeishuBinding } from "../components/FeishuBinding";
import { PlainShell } from "../components/Layout";

export default function Account() {
  const { me, reloadMe } = useAuth();
  const toast = useToast();
  const [display, setDisplay] = useState(me?.display_name ?? "");
  const [login, setLogin] = useState(me?.login_name ?? "");
  const [cur, setCur] = useState("");
  const [pw, setPw] = useState("");
  const profile = useAction<unknown>();
  const rename = useAction<unknown>();
  const [pwError, setPwError] = useState<unknown>(null);
  if (!me) return null;

  return (
    <PlainShell title="我的账号">
      <PageHeading eyebrow="MY ACCOUNT" title="我的账号" subtitle="登录名用于登录；显示名出现在账目的“录入人”。" />
      <div className="settings-grid">
        <section className="card">
          <div className="section-head"><h2>资料</h2></div>
          <form onSubmit={async (e) => {
            e.preventDefault();
            const r = await profile.run((key) => api.patch("/me/profile", { display_name: display, expected_version: me.version }, { key }));
            if (r !== undefined) { await reloadMe(); toast.show("显示名已更新"); }
          }}>
            <label className="field">显示名<input value={display} maxLength={24} onChange={(e) => setDisplay(e.target.value)} /></label>
            <ErrorNotice error={profile.error} />
            <button className="button primary" disabled={profile.busy}>保存显示名</button>
          </form>
          <form className="space-top" onSubmit={async (e) => {
            e.preventDefault();
            const r = await rename.run((key) => api.patch("/me/login-name", { new_login_name: login, expected_version: me.version }, { key }));
            if (r !== undefined) { await reloadMe(); toast.show("登录名已更新，下次请用新登录名登录"); }
          }}>
            <label className="field">登录名<input value={login} onChange={(e) => setLogin(e.target.value)} /></label>
            <ErrorNotice error={rename.error} />
            <button className="button secondary" disabled={rename.busy || login === me.login_name}>修改登录名</button>
          </form>
        </section>
        <section className="card">
          <div className="section-head"><h2>修改密码</h2></div>
          <form onSubmit={async (e) => {
            e.preventDefault();
            setPwError(null);
            try {
              await api.post("/me/password", { current_password: cur, new_password: pw });
              await refreshSession();
              setCur(""); setPw("");
              toast.show("密码已修改，其他设备需要重新登录");
            } catch (err) { setPwError(err instanceof ApiError ? err : new ApiError(0, "internal", "修改失败")); }
          }}>
            <label className="field">当前密码<input type="password" autoComplete="current-password" value={cur} onChange={(e) => setCur(e.target.value)} required /></label>
            <label className="field">新密码<input type="password" autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)} required minLength={10} /></label>
            <ErrorNotice error={pwError} />
            <button className="button primary">修改密码</button>
            <p className="tiny-note">修改后其他设备的登录会失效，飞书记账需要重新验证。</p>
          </form>
        </section>
        <FeishuBinding />
      </div>
      {toast.node}
    </PlainShell>
  );
}
