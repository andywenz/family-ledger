export const ENTRY_TYPES = [
  ["expense", "支出"],
  ["income", "收入"],
  ["refund", "退款"],
  ["internal_transfer", "内部转账"],
  ["exchange", "换汇"],
  ["card_repayment", "信用卡还款"],
  ["receivable", "往来款"],
] as const;
export type EntryType = (typeof ENTRY_TYPES)[number][0];
export const TYPE_LABEL: Record<string, string> = Object.fromEntries(ENTRY_TYPES);
export const KIND_FOR_TYPE: Record<string, string> = {
  expense: "expense",
  refund: "expense",
  income: "income",
  internal_transfer: "internal_transfer",
  exchange: "exchange",
  card_repayment: "card_repayment",
  receivable: "receivable",
};
export const KIND_LABEL: Record<string, string> = {
  expense: "支出分类",
  income: "收入分类",
  internal_transfer: "内部转账",
  exchange: "换汇",
  card_repayment: "信用卡还款",
  receivable: "往来款",
};
export const METHODS = [
  ["credit_card", "信用卡"],
  ["debit_card", "借记卡"],
  ["cash", "现金"],
] as const;
export const METHOD_LABEL: Record<string, string> = Object.fromEntries(METHODS);
export const STAT_TYPES = new Set(["expense", "income", "refund"]);
export function methodRule(type: string): "required" | "optional" | "none" {
  if (type === "expense") return "required";
  if (type === "income") return "optional";
  return "none";
}
