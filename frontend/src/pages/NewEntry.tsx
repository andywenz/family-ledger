import { useNavigate } from "react-router";
import { tr } from "../lib/i18n";
import { EntryForm } from "../components/EntryForm";
import { PageHeading } from "../components/common";
import { useFamily } from "../components/Layout";

export default function NewEntry() {
  const fam = useFamily();
  const navigate = useNavigate();
  return (
    <>
      <PageHeading title={tr("记一笔", "New entry")} subtitle={tr("保存后直接入账，按记账日期的汇率折算成纽币和人民币。", "Saved straight to the ledger and converted to NZD and CNY at that date’s exchange rate.")} />
      <div className="two-column">
        <section className="card">
          <EntryForm mode="create" onCancel={() => navigate(`/f/${fam.fid}`)}
            onSaved={(e) => navigate(`/f/${fam.fid}?month=${e.business_date.slice(0, 7)}`)} />
        </section>
        <aside>
          <section className="card aside-note">
            <h3>{tr("清楚记录，轻松回看。", "Record clearly, review easily.")}</h3>
            <p>{tr("保留原币金额，自动折算为纽币和人民币。每笔账目使用记账日期的汇率，以后回看也不会随汇率变化。", "The original amount is kept and converted to NZD and CNY automatically. Each entry uses the rate on its date, so it won’t change when rates move later.")}</p>
            <p className="tiny-note">{tr("退款请在月度总览中打开原消费，点击“退款”，系统会关联原消费并检查可退金额。", "For a refund, open the original expense in the Overview and choose “Refund”; it’s linked to the expense and checked against the refundable amount.")}</p>
          </section>
        </aside>
      </div>
    </>
  );
}
