import { useState } from "react";
import { tr } from "../lib/i18n";
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
    <PlainShell title={tr("我的账号", "My account")}>
      <PageHeading title={tr("我的账号", "My account")} subtitle={tr("登录名用于登录；显示名出现在账目的“录入人”。", "Your username is for signing in; your display name appears as “Entered by” on entries.")} />
      <div className="settings-grid">
        <section className="card">
          <div className="section-head"><h2>{tr("资料", "Profile")}</h2></div>
          <form onSubmit={async (e) => {
            e.preventDefault();
            const r = await profile.run((key) => api.patch("/me/profile", { display_name: display, expected_version: me.version }, { key }));
            if (r !== undefined) { await reloadMe(); toast.show(tr("显示名已更新", "Display name updated")); }
          }}>
            <label className="field">{tr("显示名", "Display name")}<input value={display} maxLength={24} onChange={(e) => setDisplay(e.target.value)} /></label>
            <ErrorNotice error={profile.error} />
            <button className="button primary" disabled={profile.busy}>{tr("保存显示名", "Save display name")}</button>
          </form>
          <form className="space-top" onSubmit={async (e) => {
            e.preventDefault();
            const r = await rename.run((key) => api.patch("/me/login-name", { new_login_name: login, expected_version: me.version }, { key }));
            if (r !== undefined) { await reloadMe(); toast.show(tr("登录名已更新，下次请用新登录名登录", "Username updated. Use the new username next time you sign in")); }
          }}>
            <label className="field">{tr("登录名", "Username")}<input value={login} onChange={(e) => setLogin(e.target.value)} /></label>
            <ErrorNotice error={rename.error} />
            <button className="button secondary" disabled={rename.busy || login === me.login_name}>{tr("修改登录名", "Change username")}</button>
          </form>
        </section>
        <section className="card">
          <div className="section-head"><h2>{tr("修改密码", "Change password")}</h2></div>
          <form onSubmit={async (e) => {
            e.preventDefault();
            setPwError(null);
            try {
              await api.post("/me/password", { current_password: cur, new_password: pw });
              await refreshSession();
              setCur(""); setPw("");
              toast.show(tr("密码已修改，其他设备需要重新登录", "Password changed. Other devices will need to sign in again"));
            } catch (err) { setPwError(err instanceof ApiError ? err : new ApiError(0, "internal", tr("修改失败", "Could not save"))); }
          }}>
            <label className="field">{tr("当前密码", "Current password")}<input type="password" autoComplete="current-password" value={cur} onChange={(e) => setCur(e.target.value)} required /></label>
            <label className="field">{tr("新密码", "New password")}<input type="password" autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)} required minLength={10} /></label>
            <ErrorNotice error={pwError} />
            <button className="button primary">{tr("修改密码", "Change password")}</button>
            <p className="tiny-note">{tr("修改后其他设备的登录会失效，飞书记账需要重新验证。", "After changing it, other devices are signed out and Feishu entry needs to be verified again.")}</p>
          </form>
        </section>
        <FeishuBinding />
      </div>
      {toast.node}
    </PlainShell>
  );
}
