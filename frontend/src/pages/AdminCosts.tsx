import { api, type Schemas } from "../api/client";
import { plural, tr } from "../lib/i18n";
import { ErrorNotice, PageHeading, useLoad } from "../components/common";
import { PlainShell } from "../components/Layout";
import { decimalToMinor, minorToDecimal, money } from "../lib/format";

const channels = (): Record<string, string> => ({ admin_page: tr("管理页", "Admin page"), feishu: tr("飞书", "Feishu"), email: tr("邮件", "Email") });
const statuses = (): Record<string, string> => ({ sent: tr("已送达", "Delivered"), pending: tr("发送中", "Sending"), failed: tr("失败", "Failed"), unknown: tr("结果未知", "Unknown") });

export default function AdminCosts() {
  const costs = useLoad(() => api.get<Schemas["CostSummary"]>("/admin/costs"), []);
  const alerts = useLoad(() => api.get<{ items: Schemas["Alert"][] }>("/admin/alerts"), []);
  const c = costs.data;
  return (
    <PlainShell title={tr("费用与告警", "Costs & alerts")}>
      <PageHeading title={tr("费用与告警", "Costs & alerts")} subtitle={tr("预算 NZD 15／月；达到 80%、100% 时通知，服务不会自动暂停。", "Budget NZD 15 a month. You’re notified at 80% and 100%; the service doesn’t pause automatically.")} />
      <ErrorNotice error={costs.error ?? alerts.error} />
      {c && (
        <div className="settings-grid">
          <section className="card">
            <div className="section-head"><h2>{tr(`${c.month} 应用估算`, `${c.month} app estimate`)}</h2><small>{tr("只含已知模型用量", "Known model usage only")}</small></div>
            <div className="budget-number">NZD {money(c.estimated.known_nzd)} <small>/ {money(c.budget_nzd)}</small></div>
            <p className="tiny-note">
              {tr(`模型调用 ${c.estimated.model_calls} 次，其中 `, `${plural(c.estimated.model_calls, "model call", "model calls")}; `)}<strong>{c.estimated.unknown_usage_calls}</strong>{tr(" 次用量未知（未计入估算，不按零计）。", " with unknown usage (left out of the estimate, not counted as zero).")}
              {c.estimated.price_verified_at && <><br />{tr("单价核实日期：", "Prices checked: ")}{c.estimated.price_verified_at}</>}
            </p>
            <p className="tiny-note">{tr("估算不是账单；计算、存储、网络等费用以 AWS 账单为准。", "This is an estimate, not a bill; compute, storage and network costs come from the AWS bill.")}</p>
          </section>
          <section className="card">
            <div className="section-head"><h2>{tr("AWS 账单同步", "AWS bill")}</h2></div>
            {c.billed ? (
              <>
                <div className="budget-number">{c.billed.currency} {money(c.billed.amount)}</div>
                <p className="tiny-note">
                  {c.billed.usd_amount && <>{tr("AWS 用量", "AWS usage")} USD {money(c.billed.usd_amount)}{tr("（抵扣额度之前）", " (before credits)")}{c.billed.untagged_usd && c.billed.untagged_usd !== "0.00" && <>{tr(`，其中 USD ${money(c.billed.untagged_usd)} 未打项目标签`, `, of which USD ${money(c.billed.untagged_usd)} is untagged`)}</>}<br /></>}
                  {c.billed.usd_amount && c.billed.credits_usd && c.billed.credits_usd !== "0.00" && <>{tr("已用抵扣额度", "Credits applied")} USD {money(c.billed.credits_usd)}{tr("，实付", "; paid")} USD {money(minorToDecimal(decimalToMinor(c.billed.usd_amount) + decimalToMinor(c.billed.credits_usd)))}<br /></>}
                  {tr("同步于", "Synced")} {c.billed.as_of.slice(0, 10)} · {tr("标签覆盖：", "Tag coverage: ")}{c.billed.tag_coverage === "complete" ? tr("完整", "complete") : c.billed.tag_coverage === "partial" ? tr("部分", "partial") : tr("未知", "unknown")}
                </p>
                <p className="tiny-note">{tr("金额与预算告警按抵扣前的用量计算（抵扣额度用完或过期后需真实付费）。每日从 AWS Cost Explorer 同步一次，数据约有 1 天延迟。", "The amount and budget alerts use usage before credits (once credits run out or expire, it’s paid for real). Synced daily from AWS Cost Explorer; data lags by about a day.")}</p>
              </>
            ) : <p className="muted">{tr("尚无账单数据。每日从 AWS Cost Explorer 同步一次当月至今的费用，首次同步后显示（AWS 账单数据约有 1 天延迟）。", "No bill data yet. Month-to-date costs are synced daily from AWS Cost Explorer and appear after the first sync (AWS data lags by about a day).")}</p>}
            <p className="tiny-note">{tr("阈值：", "Thresholds: ")}{c.thresholds.map((t) => `${t.percent}% = NZD ${t.amount_nzd}`).join(tr("，", ", "))}</p>
          </section>
        </div>
      )}
      <section className="card space-top">
        <div className="section-head"><h2>{tr("本月告警", "Alerts this month")}</h2></div>
        {alerts.data?.items.length === 0 && <p className="muted">{tr("本月尚未触发告警。", "No alerts this month.")}</p>}
        {alerts.data?.items.map((a) => (
          <div key={a.alert_id} className="member-row">
            <div>
              <strong>{a.threshold_percent}% · {a.basis === "billed" ? tr("账单", "Bill") : tr("应用估算", "App estimate")}</strong>
              <small>{a.triggered_at.slice(0, 16).replace("T", " ")} · {a.delivery.map((d) => `${channels()[d.channel] ?? d.channel}：${statuses()[d.status] ?? d.status}`).join("；")}</small>
            </div>
          </div>
        ))}
      </section>
    </PlainShell>
  );
}
