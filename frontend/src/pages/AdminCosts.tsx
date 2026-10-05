import { api, type Schemas } from "../api/client";
import { ErrorNotice, PageHeading, useLoad } from "../components/common";
import { PlainShell } from "../components/Layout";
import { money } from "../lib/format";

const CHANNEL: Record<string, string> = { admin_page: "管理页", feishu: "飞书", email: "邮件" };
const STATUS: Record<string, string> = { sent: "已送达", pending: "发送中", failed: "失败", unknown: "结果未知" };

export default function AdminCosts() {
  const costs = useLoad(() => api.get<Schemas["CostSummary"]>("/admin/costs"), []);
  const alerts = useLoad(() => api.get<{ items: Schemas["Alert"][] }>("/admin/alerts"), []);
  const c = costs.data;
  return (
    <PlainShell title="费用与告警">
      <PageHeading title="费用与告警" subtitle="预算 NZD 15／月；达到 80%、100% 时通知，服务不会自动暂停。" />
      <ErrorNotice error={costs.error ?? alerts.error} />
      {c && (
        <div className="settings-grid">
          <section className="card">
            <div className="section-head"><h2>{c.month} 应用估算</h2><small>只含已知模型用量</small></div>
            <div className="budget-number">NZD {money(c.estimated.known_nzd)} <small>/ {money(c.budget_nzd)}</small></div>
            <p className="tiny-note">
              模型调用 {c.estimated.model_calls} 次，其中 <strong>{c.estimated.unknown_usage_calls}</strong> 次用量未知（未计入估算，不按零计）。
              {c.estimated.price_verified_at && <><br />单价核实日期：{c.estimated.price_verified_at}</>}
            </p>
            <p className="tiny-note">估算不是账单；计算、存储、网络等费用以 AWS 账单为准。</p>
          </section>
          <section className="card">
            <div className="section-head"><h2>AWS 账单同步</h2></div>
            {c.billed ? (
              <>
                <div className="budget-number">{c.billed.currency} {money(c.billed.amount)}</div>
                <p className="tiny-note">
                  {c.billed.usd_amount && <>AWS 账单 USD {money(c.billed.usd_amount)}{c.billed.untagged_usd && c.billed.untagged_usd !== "0.00" && <>（其中 USD {money(c.billed.untagged_usd)} 未打项目标签）</>}<br /></>}
                  同步于 {c.billed.as_of.slice(0, 10)} · 标签覆盖：{c.billed.tag_coverage === "complete" ? "完整" : c.billed.tag_coverage === "partial" ? "部分" : "未知"}
                </p>
                <p className="tiny-note">每日从 AWS Cost Explorer 同步一次；AWS 账单数据约有 1 天延迟。</p>
              </>
            ) : <p className="muted">尚无账单数据。每日从 AWS Cost Explorer 同步一次当月至今的费用，首次同步后显示（AWS 账单数据约有 1 天延迟）。</p>}
            <p className="tiny-note">阈值：{c.thresholds.map((t) => `${t.percent}% = NZD ${t.amount_nzd}`).join("，")}</p>
          </section>
        </div>
      )}
      <section className="card space-top">
        <div className="section-head"><h2>本月告警</h2></div>
        {alerts.data?.items.length === 0 && <p className="muted">本月尚未触发告警。</p>}
        {alerts.data?.items.map((a) => (
          <div key={a.alert_id} className="member-row">
            <div>
              <strong>{a.threshold_percent}% · {a.basis === "billed" ? "账单" : "应用估算"}</strong>
              <small>{a.triggered_at.slice(0, 16).replace("T", " ")} · {a.delivery.map((d) => `${CHANNEL[d.channel] ?? d.channel}：${STATUS[d.status] ?? d.status}`).join("；")}</small>
            </div>
          </div>
        ))}
      </section>
    </PlainShell>
  );
}
