import { useState } from "react";
import { plural, tr } from "../lib/i18n";
import { currencyName } from "../lib/format";
import { api, type Member, type Schemas } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { ErrorNotice, PageHeading, useAction, useLoad, useToast } from "../components/common";
import { useFamily } from "../components/Layout";
import { methods } from "../lib/labels";

// 家庭时区：常用的放前面并附中文名，其余取浏览器支持的 IANA 时区（服务端再校验）
const COMMON_ZONES: [string, string, string][] = [
  ["Pacific/Auckland", "新西兰", "New Zealand"], ["Australia/Sydney", "悉尼／墨尔本", "Sydney / Melbourne"], ["Australia/Brisbane", "布里斯班", "Brisbane"],
  ["Australia/Perth", "珀斯", "Perth"], ["Asia/Shanghai", "中国大陆", "Mainland China"], ["Asia/Hong_Kong", "香港", "Hong Kong"], ["Asia/Macau", "澳门", "Macau"],
  ["Asia/Taipei", "台北", "Taipei"], ["Asia/Singapore", "新加坡", "Singapore"], ["Asia/Tokyo", "东京", "Tokyo"], ["Asia/Seoul", "首尔", "Seoul"],
  ["Europe/London", "伦敦", "London"], ["Europe/Paris", "巴黎／柏林", "Paris / Berlin"], ["America/Vancouver", "温哥华", "Vancouver"],
  ["America/Los_Angeles", "洛杉矶", "Los Angeles"], ["America/Toronto", "多伦多", "Toronto"], ["America/New_York", "纽约", "New York"], ["UTC", "协调世界时", "UTC"],
];

function otherZones(current: string): string[] {
  let all: string[] = [];
  try {
    all = Intl.supportedValuesOf("timeZone");
  } catch {
    all = [];
  }
  const common = new Set(COMMON_ZONES.map(([z]) => z));
  if (!all.includes(current)) all = [current, ...all]; // 保证当前值可选中
  return all.filter((z) => !common.has(z));
}

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
  const [tz, setTz] = useState(fam.config.timezone);
  const invites = useLoad(() => (isAdmin ? api.get<{ items: Schemas["Invitation"][] }>(`/families/${fam.fid}/invitations`) : Promise.resolve({ items: [] })), [fam.fid, isAdmin]);

  async function call(fn: (key: string) => Promise<unknown>, msg: string) {
    const r = await action.run(fn);
    if (r !== undefined) { toast.show(msg); await fam.reload(); await invites.reload(); await reloadMe(); }
  }

  const memberAction = (m: Member) => (
    <div className="row-actions">
      <button className="button secondary small" onClick={() => void call((key) => api.patch(`/families/${fam.fid}/members/${m.user_id}`, { role: m.role === "admin" ? "member" : "admin", expected_version: m.version }, { key }), tr("角色已更新", "Role updated"))}>
        {m.role === "admin" ? tr("撤销管理员", "Remove admin") : tr("设为管理员", "Make admin")}
      </button>
      <button className="button secondary small danger" onClick={() => {
        if (confirm(tr(`移除 ${m.display_name}？其历史账目保留在家庭中，但将立即无法访问。`, `Remove ${m.display_name}? Their past entries stay in the family, but they lose access immediately.`))) void call((key) => api.del(`/families/${fam.fid}/members/${m.user_id}`, { key, query: { expected_version: m.version } }), tr("已移除成员", "Member removed"));
      }}>{tr("移除", "Remove")}</button>
    </div>
  );

  return (
    <>
      <PageHeading title={tr("家庭与设置", "Family settings")} subtitle={tr("管理成员、邀请新成员，设定默认币种和付款方式。", "Manage members, invite people, and set the default currency and payment method.")} />
      <ErrorNotice error={action.error} />
      <div className="settings-grid">
        <div>
          <section className="card">
            <div className="section-head"><h2>{fam.family.name}</h2><span className="badge">{tr(`${fam.members.filter((m) => m.status === "active").length} 位成员`, plural(fam.members.filter((m) => m.status === "active").length, "member", "members"))}</span></div>
            {fam.members.filter((m) => m.status === "active").map((m) => (
              <div key={m.user_id} className="member-row">
                <span className="avatar">{m.display_name.slice(0, 1)}</span>
                <div><strong>{m.display_name}{m.user_id === me?.user_id && tr("（我）", " (me)")}</strong><small>{m.login_name}</small></div>
                <span className="badge">{m.role === "admin" ? tr("管理员", "Admin") : tr("成员", "Member")}</span>
                {isAdmin && memberAction(m)}
              </div>
            ))}
            {fam.members.some((m) => m.status !== "active") && (
              <p className="tiny-note">{tr("已离开：", "Left: ")}{fam.members.filter((m) => m.status !== "active").map((m) => m.display_name).join(tr("、", ", "))}{tr("（历史账目保留）", " (past entries kept)")}</p>
            )}
          </section>
          {isAdmin && (
            <section className="card space-top">
              <div className="section-head"><h2>{tr("邀请成员", "Invite a member")}</h2><small>{tr("对方需已有账号", "They need an existing account")}</small></div>
              <form onSubmit={(e) => { e.preventDefault(); void call((key) => api.post(`/families/${fam.fid}/invitations`, { login_name: login, role }, { key }), tr("已发出邀请（7 天内有效）", "Invitation sent (valid for 7 days)")).then(() => setLogin("")); }}>
                <div className="field-grid">
                  <label className="field">{tr("登录名", "Username")}<input value={login} onChange={(e) => setLogin(e.target.value)} required /></label>
                  <label className="field">{tr("角色", "Role")}<select value={role} onChange={(e) => setRole(e.target.value)}><option value="member">{tr("成员", "Member")}</option><option value="admin">{tr("管理员", "Admin")}</option></select></label>
                </div>
                <button className="button primary">{tr("发送邀请", "Send invitation")}</button>
              </form>
              {invites.data?.items.filter((i) => i.status === "pending").map((i) => (
                <div key={i.invitation_id} className="member-row">
                  <div><strong>{tr("待接受", "Pending")}</strong><small>{tr(`${i.expires_at.slice(0, 10)} 前有效`, `Valid until ${i.expires_at.slice(0, 10)}`)}</small></div>
                  <button className="button secondary small" onClick={() => void call((key) => api.del(`/families/${fam.fid}/invitations/${i.invitation_id}`, { key }), tr("已撤销邀请", "Invitation withdrawn"))}>{tr("撤销", "Withdraw")}</button>
                </div>
              ))}
            </section>
          )}
        </div>
        <section className="card">
          <div className="section-head"><h2>{tr("记账默认值", "Entry defaults")}</h2><small>{tr("AI 使用时会标注", "Marked when AI uses them")}</small></div>
          <div className="field-grid">
            <label className="field">{tr("默认币种", "Default currency")}<select value={cur} disabled={!isAdmin} onChange={(e) => { setCur(e.target.value); if (secondary === e.target.value) setSecondary(""); }}>{fam.currencies.map((c) => <option key={c.code}>{c.code}</option>)}</select></label>
            <label className="field">{tr("默认消费方式", "Default payment method")}<select value={method} disabled={!isAdmin} onChange={(e) => setMethod(e.target.value as typeof method)}>{methods().map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
            <label className="field">{tr("辅助币种（可选）", "Secondary currency (optional)")}
              <select value={secondary} disabled={!isAdmin} onChange={(e) => setSecondary(e.target.value)}>
                <option value="">{tr("不显示", "Don’t show")}</option>
                {fam.currencies.filter((c) => c.code !== cur).map((c) => <option key={c.code} value={c.code}>{currencyName(c.code, c.name_zh)}{tr(`（${c.code}）`, ` (${c.code})`)}</option>)}
              </select>
            </label>
            <label className="field">{tr("家庭时区", "Family time zone")}
              <select value={tz} disabled={!isAdmin} onChange={(e) => setTz(e.target.value)}>
                <optgroup label={tr("常用", "Common")}>
                  {COMMON_ZONES.map(([z, zh, en]) => <option key={z} value={z}>{tr(`${zh}（${z}）`, `${en} (${z})`)}</option>)}
                </optgroup>
                <optgroup label={tr("其他时区", "Other time zones")}>
                  {otherZones(tz).map((z) => <option key={z} value={z}>{z}</option>)}
                </optgroup>
              </select>
            </label>
          </div>
          <p className="tiny-note">{tr("默认币种是新记账时预选的币种，也是月度总览的主显示币种；辅助币种显示在净支出、消费方式的小字和明细列表的第二个“合 xxx”列。选“不显示”则只显示默认币种。两者都只影响显示，不改变已入账的金额，也不影响导出。", "The default currency is preselected for new entries and is the main currency in the Overview. The secondary currency appears in the small figures under net spending and payment methods, and as the second “≈” column in the entry list. Choose “Don’t show” to see only the default currency. Neither changes saved amounts or exports.")}</p>
          <p className="tiny-note">{tr("家庭时区决定“今天”是哪一天：新记账的默认日期、AI 识别“昨天”等相对日期、月度总览默认打开的月份。修改后已入账账目的日期不变。", "The family time zone decides what “today” is: the default date for new entries, relative dates such as “yesterday” in AI entry, and which month the Overview opens on. Changing it doesn’t change dates of saved entries.")}<br />{tr("原始照片随有效账目长期保存。", "Original photos are kept for as long as their entries are.")}</p>
          {isAdmin && <button className="button primary" onClick={() => void call((key) => api.patch(`/families/${fam.fid}/config`, { default_currency: cur, default_payment_method: method, secondary_currency: secondary || null, timezone: tz, expected_version: fam.config.version }, { key }), tr("设置已保存", "Settings saved"))}>{tr("保存设置", "Save settings")}</button>}
        </section>
      </div>
      {toast.node}
    </>
  );
}
