import { createContext, useContext, useState, type ReactNode } from "react";
import { tr } from "../lib/i18n";
import { Logo } from "./Logo";
import { NavLink, Navigate, Outlet, useNavigate, useParams } from "react-router";
import { api, type CategoryTree, type Currency, type Member, type Schemas } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { ErrorNotice, Modal, useLoad } from "./common";
import { LangSwitcher } from "./LangSwitcher";
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
  if (!ctx) throw new Error(tr("不在家庭页面内", "Not inside a family page"));
  return ctx;
}

export function RequireAuth({ children }: { children: ReactNode }) {
  const { me, ready } = useAuth();
  if (!ready) return <div className="center-page">{tr("加载中…", "Loading…")}</div>;
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
          <Logo />
          <span>{tr("小家账本", "Family Ledger")}</span>
        </a>
        <label className="family-switch">
          <span className="family-avatar">⌂</span>
          <div className="family-select">
            <select aria-label={tr("切换家庭", "Switch family")} value={fid ?? ""} onChange={(e) => {
              if (!e.target.value) return;
              if (e.target.value === "__manage") navigate("/families");
              else navigate(`/f/${e.target.value}`);
            }}>
              {!fid && <option value="">{tr("选择家庭", "Choose a family")}</option>}
              {families.map((f) => <option key={f.family_id} value={f.family_id}>{f.name}</option>)}
              <option value="__manage">{tr("管理／创建家庭…", "Manage or create families…")}</option>
            </select>
            <small>{familyName ? tr("家庭共享账本", "Shared family ledger") : tr("未选择家庭", "No family selected")}</small>
          </div>
        </label>
        <p className="nav-label">{tr("日常记账", "Daily")}</p>
        <nav aria-label={tr("主要导航", "Main navigation")}>
          {fid && (
            <>
              {link(`/f/${fid}`, "grid", tr("月度总览", "Overview"), true)}
              {link(`/f/${fid}/new`, "plus", tr("记一笔", "New entry"))}
              {link(`/f/${fid}/ai`, "spark", tr("AI 帮我记", "AI entry"))}
              <p className="nav-label">{tr("账本管理", "Ledger")}</p>
              <span className="nav-secondary">{link(`/f/${fid}/trash`, "trash", tr("回收站", "Trash"))}</span>
              <span className="nav-secondary">{link(`/f/${fid}/categories`, "folder", tr("分类管理", "Categories"))}</span>
              <span className="nav-secondary">{link(`/f/${fid}/rates`, "rate", tr("币种与汇率", "Currencies & rates"))}</span>
              <span className="nav-secondary">{link(`/f/${fid}/settings`, "settings", tr("家庭与设置", "Family settings"))}</span>
            </>
          )}
          <p className="nav-label">{tr("账号", "Account")}</p>
          <span className={fid ? "nav-secondary" : ""}>{link("/account", "user", tr("我的账号", "My account"))}</span>
          {me?.is_system_admin && <span className={fid ? "nav-secondary" : ""}>{link("/admin/users", "settings", tr("系统账号", "User accounts"))}</span>}
          {me?.is_system_admin && <span className={fid ? "nav-secondary" : ""}>{link("/admin/costs", "wallet", tr("费用与告警", "Costs & alerts"))}</span>}
          <button type="button" className="nav-more" onClick={() => setMore(true)}>
            <span><Icon name="menu" /></span>{tr("更多", "More")}
          </button>
        </nav>
        <div className="sidebar-bottom">
          <div className="profile">
            <span className="avatar">{me?.display_name.slice(0, 1)}</span>
            <div>
              <strong>{me?.display_name}</strong>
              <small>{me?.login_name}</small>
            </div>
            <button type="button" className="icon-button" aria-label={tr("退出登录", "Sign out")} onClick={async () => { await logout(); navigate("/login"); }}>
              <Icon name="logout" />
            </button>
          </div>
        </div>
      </aside>
      <div className="shell">
        <header className="topbar">
          <div className="breadcrumb">
            {familyName ?? tr("小家账本", "Family Ledger")} <span>/</span> <strong>{breadcrumb}</strong>
          </div>
          <div className="topbar-right">
            {env === "local" && <span className="preview-tag local-tag" title={tr("本地开发模式 · 非生产数据", "Local dev · not production data")}><i></i><span className="local-tag-text">{tr("本地开发模式 · 非生产数据", "Local dev · not production data")}</span></span>}
            <LangSwitcher />
            <ThemeSwitcher />
          </div>
        </header>
        <main>{children}</main>
      </div>
      {more && (
        <Modal title={tr("更多", "More")} onClose={() => setMore(false)}>
          <div className="more-sheet" onClick={(e) => { if ((e.target as HTMLElement).closest("a")) setMore(false); }}>
            <label className="field">{tr("当前家庭", "Current family")}
              <select aria-label={tr("手机切换家庭", "Switch family (mobile)")} value={fid ?? ""} onChange={(e) => {
                setMore(false);
                if (e.target.value === "__manage") navigate("/families");
                else if (e.target.value) navigate(`/f/${e.target.value}`);
              }}>
                {!fid && <option value="">{tr("选择家庭", "Choose a family")}</option>}
                {families.map((f) => <option key={f.family_id} value={f.family_id}>{f.name}</option>)}
                <option value="__manage">{tr("管理／创建家庭…", "Manage or create families…")}</option>
              </select>
            </label>
            {fid && (
              <>
                <NavLink to={`/f/${fid}/trash`}><Icon name="trash" />{tr("回收站", "Trash")}</NavLink>
                <NavLink to={`/f/${fid}/categories`}><Icon name="folder" />{tr("分类管理", "Categories")}</NavLink>
                <NavLink to={`/f/${fid}/rates`}><Icon name="rate" />{tr("币种与汇率", "Currencies & rates")}</NavLink>
                <NavLink to={`/f/${fid}/settings`}><Icon name="settings" />{tr("家庭与设置", "Family settings")}</NavLink>
              </>
            )}
            <NavLink to="/account"><Icon name="user" />{tr("我的账号", "My account")}{tr(`（${me?.display_name}）`, ` (${me?.display_name})`)}</NavLink>
            {me?.is_system_admin && <NavLink to="/admin/users"><Icon name="settings" />{tr("系统账号", "User accounts")}</NavLink>}
            {me?.is_system_admin && <NavLink to="/admin/costs"><Icon name="wallet" />{tr("费用与告警", "Costs & alerts")}</NavLink>}
            <button type="button" className="button secondary" onClick={async () => { setMore(false); await logout(); navigate("/login"); }}>
              <Icon name="logout" />{tr("退出登录", "Sign out")}
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

const titles = (): Record<string, string> => ({
  "": tr("月度总览", "Overview"), new: tr("记一笔", "New entry"), ai: tr("AI 帮我记", "AI entry"), trash: tr("回收站", "Trash"),
  categories: tr("分类管理", "Categories"), rates: tr("币种与汇率", "Currencies & rates"), settings: tr("家庭与设置", "Family settings"),
});

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
  if (error) return <PlainShell title={tr("家庭", "Family")}><ErrorNotice error={error} onRetry={reload} /></PlainShell>;
  // 加载中与加载后保持同一层级结构，避免 Shell 重新挂载（否则手机端“更多”菜单会在加载完成时消失）
  return (
    <FamilyCtx.Provider value={data ? { fid, ...data, reload } : null}>
      <Shell breadcrumb={data ? (titles()[segment] ?? "") : tr("加载中", "Loading")} familyName={data?.family.name} fid={fid}>
        {data ? <Outlet /> : <p className="muted">{tr("加载中…", "Loading…")}</p>}
      </Shell>
    </FamilyCtx.Provider>
  );
}
