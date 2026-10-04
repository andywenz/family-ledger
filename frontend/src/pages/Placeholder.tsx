import { PageHeading } from "../components/common";
import { Icon } from "../components/Icon";

/** 尚未实现阶段的页面：明确说明，不展示假数据或假按钮。 */
export function NotYet({ eyebrow, title, stage, detail }: { eyebrow: string; title: string; stage: string; detail: string }) {
  return (
    <>
      <PageHeading eyebrow={eyebrow} title={title} />
      <div className="notice"><Icon name="info" /><div><strong>{stage}</strong><p>{detail}</p></div></div>
    </>
  );
}
