import { createContext, useContext, useState, type ReactNode } from "react";
import { NavLink, Navigate, Outlet, useNavigate, useParams } from "react-router";
import { api, type CategoryTree, type Currency, type Member, type Schemas } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { ErrorNotice, Modal, useLoad } from "./common";
import { ThemeSwitcher } from "./ThemeSwitcher";
import { Icon } from "./Icon";

type FamilyData = {
  fid: string;
  family: Schemas["Family"];
  config: Schemas["FamilyConfig"];
  categories: CategoryTree;
  currencies: Currency[];
  members: Member[];
  reload: () => Promise<void>;
};

const FamilyCtx = createContext<FamilyData | null>(null);

export function useFamily(): FamilyData {
  const ctx = useContext(FamilyCtx);
  if (!ctx) throw new Error("不在家庭页面内");
  return ctx;
}

export function RequireAuth({ children }: { children: ReactNode }) {
  const { me, ready } = useAuth();
  if (!ready) return <div className="center-page">加载中…</div>;
  if (!me) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

function Shell({ breadcrumb, familyName, children, fid }: { breadcrumb: string; familyName?: string; children: ReactNode; fid?: string }) {
  const { me, env, logout } = useAuth();
  const navigate = useNavigate();
  const families = me?.families ?? [];
  const [more, setMore] = useState(false);
  const link = (to: string, icon: string, label: string, end = false) => (
    <NavLink to={to} end={end} className={({ isActive }) => (isActive ? "active" : "")}>
      <span><Icon name={icon} /></span>
      {label}
    </NavLink>
  );
  return (
    <>
      <aside className="sidebar">
        <a className="brand" href="/">
          <span className="brand-mark">家</span>
          <span>小家账本<small>OUR DAILY LEDGER</small></span>
        </a>
        <label className="family-switch">
          <span className="family-avatar">⌂</span>
          <div className="family-select">
            <select aria-label="切换家庭" value={fid ?? ""} onChange={(e) => {
              if (!e.target.value) return;
              if (e.target.value === "__manage") navigate("/families");
              else navigate(`/f/${e.target.value}`);
            }}>
              {!fid && <option value="">选择家庭</option>}
              {families.map((f) => <option key={f.family_id} value={f.family_id}>{f.name}</option>)}
              <option value="__manage">管理／创建家庭…</option>
            </select>
            <small>{familyName ? "家庭共享账本" : "未选择家庭"}</small>
          </div>
        </label>
        <p className="nav-label">日常记账</p>
        <nav aria-label="主要导航">
          {fid && (
            <>
              {link(`/f/${fid}`, "grid", "月度总览", true)}
              {link(`/f/${fid}/new`, "plus", "记一笔")}
              {link(`/f/${fid}/ai`, "spark", "AI 帮我记")}
              <p className="nav-label">账本管理</p>
              <span className="nav-secondary">{link(`/f/${fid}/trash`, "trash", "回收站")}</span>
              <span className="nav-secondary">{link(`/f/${fid}/categories`, "folder", "分类管理")}</span>
              <span className="nav-secondary">{link(`/f/${fid}/rates`, "rate", "币种与汇率")}</span>
              <span className="nav-secondary">{link(`/f/${fid}/settings`, "settings", "家庭与设置")}</span>
            </>
          )}
          <p className="nav-label">账号</p>
          <span className={fid ? "nav-secondary" : ""}>{link("/account", "user", "我的账号")}</span>
          {me?.is_system_admin && <span className={fid ? "nav-secondary" : ""}>{link("/admin/users", "settings", "系统账号")}</span>}
          {me?.is_system_admin && <span className={fid ? "nav-secondary" : ""}>{link("/admin/costs", "wallet", "费用与告警")}</span>}
          <button type="button" className="nav-more" onClick={() => setMore(true)}>
            <span><Icon name="menu" /></span>更多
          </button>
        </nav>
        <div className="sidebar-bottom">
          <div className="profile">
            <span className="avatar">{me?.display_name.slice(0, 1)}</span>
            <div>
              <strong>{me?.display_name}</strong>
              <small>{me?.login_name}</small>
            </div>
            <button type="button" className="icon-button" aria-label="退出登录" onClick={async () => { await logout(); navigate("/login"); }}>
              <Icon name="logout" />
            </button>
          </div>
        </div>
      </aside>
      <div className="shell">
        <header className="topbar">
          <div className="breadcrumb">
            {familyName ?? "小家账本"} <span>/</span> <strong>{breadcrumb}</strong>
          </div>
          <div className="topbar-right">
            {env === "local" && <span className="preview-tag local-tag"><i></i>本地开发模式 · 非生产数据</span>}
            <ThemeSwitcher />
          </div>
        </header>
        <main>{children}</main>
        <footer className="site-footer">
          <span>小家账本 · 好好生活，慢慢记录</span>
          <span>金额以记账日期汇率快照折算</span>
        </footer>
      </div>
      {more && (
        <Modal title="更多" onClose={() => setMore(false)}>
          <div className="more-sheet" onClick={(e) => { if ((e.target as HTMLElement).closest("a")) setMore(false); }}>
            <label className="field">当前家庭
              <select aria-label="手机切换家庭" value={fid ?? ""} onChange={(e) => {
                setMore(false);
                if (e.target.value === "__manage") navigate("/families");
                else if (e.target.value) navigate(`/f/${e.target.value}`);
              }}>
                {!fid && <option value="">选择家庭</option>}
                {families.map((f) => <option key={f.family_id} value={f.family_id}>{f.name}</option>)}
                <option value="__manage">管理／创建家庭…</option>
              </select>
            </label>
            {fid && (
              <>
                <NavLink to={`/f/${fid}/trash`}><Icon name="trash" />回收站</NavLink>
                <NavLink to={`/f/${fid}/categories`}><Icon name="folder" />分类管理</NavLink>
                <NavLink to={`/f/${fid}/rates`}><Icon name="rate" />币种与汇率</NavLink>
                <NavLink to={`/f/${fid}/settings`}><Icon name="settings" />家庭与设置</NavLink>
              </>
            )}
            <NavLink to="/account"><Icon name="user" />我的账号（{me?.display_name}）</NavLink>
            {me?.is_system_admin && <NavLink to="/admin/users"><Icon name="settings" />系统账号</NavLink>}
            {me?.is_system_admin && <NavLink to="/admin/costs"><Icon name="wallet" />费用与告警</NavLink>}
            <button type="button" className="button secondary" onClick={async () => { setMore(false); await logout(); navigate("/login"); }}>
              <Icon name="logout" />退出登录
            </button>
          </div>
        </Modal>
      )}
    </>
  );
}

export function PlainShell({ title, children }: { title: string; children: ReactNode }) {
  return <Shell breadcrumb={title}>{children}</Shell>;
}

const TITLES: Record<string, string> = {
  "": "月度总览", new: "记一笔", ai: "AI 帮我记", trash: "回收站", categories: "分类管理",
  rates: "币种与汇率", settings: "家庭与设置",
};

export function FamilyLayout() {
  const { fid = "" } = useParams();
  const { data, error, reload } = useLoad(async () => {
    const [family, config, categories, currencies, members] = await Promise.all([
      api.get<Schemas["Family"]>(`/families/${fid}`),
      api.get<Schemas["FamilyConfig"]>(`/families/${fid}/config`),
      api.get<CategoryTree>(`/families/${fid}/categories`, { include_inactive: true }),
      api.get<{ items: Currency[] }>(`/families/${fid}/currencies`),
      api.get<{ items: Member[] }>(`/families/${fid}/members`),
    ]);
    try { localStorage.setItem("ledger:last-family", fid); } catch { /* 隐私模式 */ }
    return { family, config, categories, currencies: currencies.items, members: members.items };
  }, [fid]);
  const segment = location.pathname.split("/")[3] ?? "";
  if (error) return <PlainShell title="家庭"><ErrorNotice error={error} onRetry={reload} /></PlainShell>;
  // 加载中与加载后保持同一层级结构，避免 Shell 重新挂载（否则手机端“更多”菜单会在加载完成时消失）
  return (
    <FamilyCtx.Provider value={data ? { fid, ...data, reload } : null}>
      <Shell breadcrumb={data ? (TITLES[segment] ?? "") : "加载中"} familyName={data?.family.name} fid={fid}>
        {data ? <Outlet /> : <p className="muted">加载中…</p>}
      </Shell>
    </FamilyCtx.Provider>
  );
}
