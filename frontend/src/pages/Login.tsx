import { useState } from "react";
import { tr } from "../lib/i18n";
import { Logo } from "../components/Logo";
import { useLocation, useNavigate } from "react-router";
import { ApiError, api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { LangSwitcher } from "../components/LangSwitcher";
import { ThemeSwitcher } from "../components/ThemeSwitcher";

type LoginResult = { status: "ok"; access_token: string } | { status: "challenge"; challenge_session: string };

function nextPath(families: { family_id: string }[]): string {
  let last: string | null = null;
  try { last = localStorage.getItem("ledger:last-family"); } catch { /* 隐私模式 */ }
  if (last && families.some((f) => f.family_id === last)) return `/f/${last}`;
  const first = families[0];
  return first ? `/f/${first.family_id}` : "/families";
}

export function Login() {
  const { signedIn, env } = useAuth();
  const navigate = useNavigate();
  const [loginName, setLoginName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const r = await api.post<LoginResult>("/auth/login", { login_name: loginName, password }, { auth: false });
      if (r.status === "challenge") {
        navigate("/login/new-password", { state: { session: r.challenge_session } });
        return;
      }
      await signedIn(r.access_token);
      const me = await api.get<{ families: { family_id: string }[] }>("/me");
      navigate(nextPath(me.families));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : tr("登录失败", "Sign-in failed"));
    } finally {
      setBusy(false);
      setPassword("");
    }
  }

  return (
    <div className="auth-page">
      <div className="auth-theme"><LangSwitcher /><ThemeSwitcher /></div>
      <form className="card auth-card" onSubmit={submit}>
        <div className="brand"><Logo /><span>{tr("小家账本", "Family Ledger")}</span></div>
        <h1>{tr("登录", "Sign in")}</h1>
        {env === "local" && <p className="preview-tag local-tag"><i></i>{tr("本地开发模式", "Local dev")}</p>}
        <label className="field">{tr("登录名", "Username")}<input autoComplete="username" value={loginName} onChange={(e) => setLoginName(e.target.value)} required /></label>
        <label className="field">{tr("密码", "Password")}<input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required /></label>
        {error && <p className="form-error" role="alert">{error}</p>}
        <button className="button primary" type="submit" disabled={busy}>{tr("登录", "Sign in")}</button>
        <p className="tiny-note">{tr("没有公开注册。忘记密码请联系系统管理员重置。", "There is no public sign-up. If you forget your password, ask the system admin to reset it.")}</p>
      </form>
    </div>
  );
}

export function NewPassword() {
  const { signedIn } = useAuth();
  const navigate = useNavigate();
  const session = (useLocation().state as { session?: string } | null)?.session;
  const [pw, setPw] = useState("");
  const [pw2, setPw2] = useState("");
  const [error, setError] = useState("");

  if (!session) {
    return <div className="auth-page"><div className="card auth-card"><p>{tr("改密会话已失效，请重新登录。", "This password session has expired. Please sign in again.")}</p><a className="button primary" href="/login">{tr("返回登录", "Back to sign in")}</a></div></div>;
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (pw !== pw2) return setError(tr("两次输入的密码不一致", "The passwords do not match"));
    try {
      const r = await api.post<{ access_token: string }>("/auth/challenge", { challenge_session: session, new_password: pw }, { auth: false });
      await signedIn(r.access_token);
      const me = await api.get<{ families: { family_id: string }[] }>("/me");
      navigate(nextPath(me.families));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : tr("修改失败", "Could not save"));
    }
  }

  return (
    <div className="auth-page">
      <div className="auth-theme"><LangSwitcher /><ThemeSwitcher /></div>
      <form className="card auth-card" onSubmit={submit}>
        <h1>{tr("设置新密码", "Set a new password")}</h1>
        <p className="tiny-note">{tr("首次登录或管理员重置后，需要设置自己的密码：至少 10 位，同时包含字母和数字。", "After your first sign-in or an admin reset, set your own password: at least 10 characters, with both letters and numbers.")}</p>
        <label className="field">{tr("新密码", "New password")}<input type="password" autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)} required minLength={10} /></label>
        <label className="field">{tr("再次输入", "Confirm password")}<input type="password" autoComplete="new-password" value={pw2} onChange={(e) => setPw2(e.target.value)} required /></label>
        {error && <p className="form-error" role="alert">{error}</p>}
        <button className="button primary" type="submit">{tr("保存并进入", "Save and continue")}</button>
      </form>
    </div>
  );
}

export function Home() {
  const { me } = useAuth();
  const navigate = useNavigate();
  if (me) queueMicrotask(() => navigate(nextPath(me.families), { replace: true }));
  return null;
}
