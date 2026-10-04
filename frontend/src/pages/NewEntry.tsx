import { useNavigate } from "react-router";
import { EntryForm } from "../components/EntryForm";
import { PageHeading } from "../components/common";
import { useFamily } from "../components/Layout";

export default function NewEntry() {
  const fam = useFamily();
  const navigate = useNavigate();
  return (
    <>
      <PageHeading title="记一笔" subtitle="保存后直接入账，按记账日期的汇率折算成纽币和人民币。" />
      <div className="two-column">
        <section className="card">
          <EntryForm mode="create" onCancel={() => navigate(`/f/${fam.fid}`)}
            onSaved={(e) => navigate(`/f/${fam.fid}?month=${e.business_date.slice(0, 7)}`)} />
        </section>
        <aside>
          <section className="card aside-note">
            <h3>清楚记录，轻松回看。</h3>
            <p>保留原币金额，自动折算为纽币和人民币。每笔账目使用记账日期的汇率，以后回看也不会随汇率变化。</p>
            <p className="tiny-note">退款请在月度总览中打开原消费，点击“退款”，系统会关联原消费并检查可退金额。</p>
          </section>
        </aside>
      </div>
    </>
  );
}
