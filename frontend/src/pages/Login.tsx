import { useState } from "react";
import { useLocation, useNavigate } from "react-router";
import { ApiError, api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
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
      setError(err instanceof ApiError ? err.message : "登录失败");
    } finally {
      setBusy(false);
      setPassword("");
    }
  }

  return (
    <div className="auth-page">
      <div className="auth-theme"><ThemeSwitcher /></div>
      <form className="card auth-card" onSubmit={submit}>
        <div className="brand"><span className="brand-mark">家</span><span>小家账本<small>OUR DAILY LEDGER</small></span></div>
        <h1>登录</h1>
        {env === "local" && <p className="preview-tag local-tag"><i></i>本地开发模式</p>}
        <label className="field">登录名<input autoComplete="username" value={loginName} onChange={(e) => setLoginName(e.target.value)} required /></label>
        <label className="field">密码<input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required /></label>
        {error && <p className="form-error" role="alert">{error}</p>}
        <button className="button primary" type="submit" disabled={busy}>登录</button>
        <p className="tiny-note">没有公开注册。忘记密码请联系系统管理员重置。</p>
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
    return <div className="auth-page"><div className="card auth-card"><p>改密会话已失效，请重新登录。</p><a className="button primary" href="/login">返回登录</a></div></div>;
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (pw !== pw2) return setError("两次输入的密码不一致");
    try {
      const r = await api.post<{ access_token: string }>("/auth/challenge", { challenge_session: session, new_password: pw }, { auth: false });
      await signedIn(r.access_token);
      const me = await api.get<{ families: { family_id: string }[] }>("/me");
      navigate(nextPath(me.families));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "修改失败");
    }
  }

  return (
    <div className="auth-page">
      <div className="auth-theme"><ThemeSwitcher /></div>
      <form className="card auth-card" onSubmit={submit}>
        <h1>设置新密码</h1>
        <p className="tiny-note">首次登录或管理员重置后，需要设置自己的密码：至少 10 位，同时包含字母和数字。</p>
        <label className="field">新密码<input type="password" autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)} required minLength={10} /></label>
        <label className="field">再次输入<input type="password" autoComplete="new-password" value={pw2} onChange={(e) => setPw2(e.target.value)} required /></label>
        {error && <p className="form-error" role="alert">{error}</p>}
        <button className="button primary" type="submit">保存并进入</button>
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
