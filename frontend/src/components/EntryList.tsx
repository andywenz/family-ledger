import type { Entry } from "../api/client";
import { tr } from "../lib/i18n";
import { cash, dayLabel, decimalToMinor, minorToDecimal, money } from "../lib/format";
import { methodLabel, typeLabel } from "../lib/labels";

// 手机版明细：按日期分组的流水清单，无需左右滑动即可看到关键信息。
// 一级分类的色点与上方饼图同色，便于对照“钱花在了哪里”。
export function EntryList({ items, primary, secondary, colorOf, onOpen }: {
  items: Entry[];
  primary: string;
  secondary: string | null;
  colorOf: (parentId: string) => string | undefined;
  onOpen: (e: Entry) => void;
}) {
  const days: { date: string; entries: Entry[]; spentMinor: number }[] = [];
  for (const e of items) {
    let day = days[days.length - 1];
    if (!day || day.date !== e.business_date) {
      day = { date: e.business_date, entries: [], spentMinor: 0 };
      days.push(day);
    }
    day.entries.push(e);
    // 当日净支出：消费减退款，只算计入收支的账目（整数分值累加）
    if (e.counts_in_stats && e.primary && (e.type === "expense" || e.type === "refund")) {
      day.spentMinor += (e.type === "expense" ? 1 : -1) * decimalToMinor(e.primary.amount);
    }
  }

  return (
    <div className="entry-list">
      <p className="entry-legend">
        {tr("金额为", "Amounts ")}<span data-currency-role="primary">{tr("合 ", "≈ ")}{primary}</span>
        {secondary && <>{tr("，小字为", "; small figures ")}<span data-currency-role="secondary">{tr("合 ", "≈ ")}{secondary}</span></>}
      </p>
      {days.map((d) => (
        <section key={d.date} className="entry-day" aria-label={dayLabel(d.date)}>
          <h3 className="entry-day-head">
            <span>{dayLabel(d.date)}</span>
            {d.spentMinor !== 0 && <span className="entry-day-total">{tr("支出", "Spent")} {cash(primary, minorToDecimal(d.spentMinor))}</span>}
          </h3>
          <ul>
            {d.entries.map((e) => {
              const tone = e.type === "income" ? "income" : e.type === "refund" ? "refund" : "";
              const sign = e.type === "income" || e.type === "refund" ? "+" : "";
              const meta = [
                e.payment_method ? methodLabel(e.payment_method) : null,
                e.created_by_display + (e.created_by_left ? tr("（已离开）", " (left)") : ""),
                e.currency !== primary ? tr(`原 ${e.currency} ${money(e.amount)}`, `Orig. ${e.currency} ${money(e.amount)}`) : null,
                e.source === "import" ? tr("历史导入", "Imported") : null,
              ].filter(Boolean);
              return (
                <li key={e.entry_id} data-entry data-type={e.type}>
                  <button type="button" className="entry-item" onClick={() => onOpen(e)} aria-label={tr(`查看或编辑 ${e.note || e.category.leaf_name}`, `View or edit ${e.note || e.category.leaf_name}`)}>
                    <span className="entry-line">
                      <span className="entry-cat">
                        <i className="entry-dot" style={{ background: colorOf(e.category.parent_id) ?? "var(--line)" }}></i>
                        <strong>{e.category.leaf_name}</strong>
                        <span className="entry-parent">{e.category.parent_name}</span>
                      </span>
                      <span className={`entry-amount ${tone}`}>{e.primary ? sign + cash(primary, e.primary.amount) : "—"}</span>
                    </span>
                    <span className="entry-line">
                      <span className="entry-note">
                        {e.type !== "expense" && <span className={`badge type-${e.type}`}>{typeLabel(e.type)}</span>}
                        {!e.counts_in_stats && <span className="badge">{tr("不计收支", "Not in totals")}</span>}
                        {e.note || <span className="entry-empty">{tr("无备注", "No note")}</span>}
                        {e.attachments.length > 0 && <span title={tr("有照片", "Has photo")}> 📎</span>}
                      </span>
                      {secondary && <span className="entry-secondary">{e.secondary ? cash(secondary, e.secondary.amount) : "—"}</span>}
                    </span>
                    <span className="entry-line entry-meta">
                      <span>{meta.join(" · ")}</span>
                      <span className="entry-open">{e.can_edit ? tr("编辑", "Edit") : tr("查看", "View")} ›</span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </section>
      ))}
      {items.length === 0 && <p className="empty">{tr("还没有符合条件的记录。", "No matching entries yet.")}</p>}
    </div>
  );
}
