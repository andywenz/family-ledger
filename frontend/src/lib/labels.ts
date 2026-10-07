import { tr } from "./i18n";

// 固定选项的显示名随界面语言变化（值不变）；用函数取当前语言的名称
const TYPES = {
  expense: ["支出", "Expense"],
  income: ["收入", "Income"],
  refund: ["退款", "Refund"],
  internal_transfer: ["内部转账", "Transfer"],
  exchange: ["换汇", "Exchange"],
  card_repayment: ["信用卡还款", "Card repayment"],
  receivable: ["往来款", "IOU"],
} as const;
export type EntryType = keyof typeof TYPES;
export const entryTypes = (): [EntryType, string][] =>
  (Object.keys(TYPES) as EntryType[]).map((v) => [v, typeLabel(v)]);
export function typeLabel(type: string): string {
  const l = TYPES[type as EntryType];
  return l ? tr(l[0], l[1]) : type;
}

export const KIND_FOR_TYPE: Record<string, string> = {
  expense: "expense",
  refund: "expense",
  income: "income",
  internal_transfer: "internal_transfer",
  exchange: "exchange",
  card_repayment: "card_repayment",
  receivable: "receivable",
};
const KINDS = {
  expense: ["支出分类", "Expenses"],
  income: ["收入分类", "Income"],
  internal_transfer: ["内部转账", "Transfers"],
  exchange: ["换汇", "Exchange"],
  card_repayment: ["信用卡还款", "Card repayments"],
  receivable: ["往来款", "IOUs"],
} as const;
export const kindLabels = (): [string, string][] => Object.entries(KINDS).map(([k, l]) => [k, tr(l[0], l[1])]);
export function kindLabel(kind: string): string {
  const l = KINDS[kind as keyof typeof KINDS];
  return l ? tr(l[0], l[1]) : kind;
}

const METHOD_NAMES = {
  credit_card: ["信用卡", "Credit card"],
  debit_card: ["借记卡", "Debit card"],
  cash: ["现金", "Cash"],
} as const;
export const methods = (): [keyof typeof METHOD_NAMES, string][] =>
  (Object.keys(METHOD_NAMES) as (keyof typeof METHOD_NAMES)[]).map((m) => [m, methodLabel(m)]);
export function methodLabel(method: string): string {
  const l = METHOD_NAMES[method as keyof typeof METHOD_NAMES];
  return l ? tr(l[0], l[1]) : method;
}

export const STAT_TYPES = new Set(["expense", "income", "refund"]);
export function methodRule(type: string): "required" | "optional" | "none" {
  if (type === "expense") return "required";
  if (type === "income") return "optional";
  return "none";
}
