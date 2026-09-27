import type { TxnType } from "@/data/types";
import { parseDecimalCents } from "@/lib/decimal";

/** 记一笔表单的原始输入（金额为元字符串） */
export interface TxnFormInput {
  type: TxnType;
  /** 账面名称（商户/对手方），必填 */
  payee: string;
  /** 元为单位的输入字符串，如 "12.5" */
  amount: string;
  /** 分类允许为空；transfer 传 null */
  categoryId: string | null;
  /** 支出=付款账户；收入=收款账户；transfer=转出账户 */
  accountId: string;
  /** 仅 transfer：转入账户 */
  toAccountId?: string;
  /** 仅 refund：被退款的原支出交易 ID（必填） */
  refundOf?: string;
}

export type TxnField = "payee" | "amount" | "account" | "toAccount" | "refundOf";

export interface TxnError {
  field: TxnField;
  message: string;
}

/** 提交时校验：账面名称必填、分类允许为空；返回错误列表，空数组即通过 */
export function validateTransaction(input: TxnFormInput): TxnError[] {
  const errors: TxnError[] = [];

  if (!input.payee.trim()) {
    errors.push({ field: "payee", message: "请填写账面名称" });
  }

  const amountStr = input.amount.trim();
  if (!amountStr) {
    errors.push({ field: "amount", message: "请输入金额" });
  } else {
    // 精确十进制解析：三位小数/指数/超界一律拒绝，不经浮点
    const parsed = parseDecimalCents(amountStr);
    if (!parsed.ok) {
      errors.push({
        field: "amount",
        message: parsed.error.includes("两位小数") ? "金额最多支持两位小数" : parsed.error,
      });
    } else if (parsed.cents <= 0) {
      errors.push({ field: "amount", message: "金额必须大于 0" });
    }
  }

  if (input.type === "refund" && !input.refundOf) {
    errors.push({ field: "refundOf", message: "退款必须选择原支出交易" });
  }

  if (input.type === "transfer") {
    if (!input.accountId) errors.push({ field: "account", message: "请选择转出账户" });
    if (!input.toAccountId) {
      errors.push({ field: "toAccount", message: "请选择转入账户" });
    } else if (input.accountId && input.accountId === input.toAccountId) {
      errors.push({ field: "toAccount", message: "转出与转入账户不能相同" });
    }
  } else {
    if (!input.accountId) errors.push({ field: "account", message: "请选择账户" });
  }

  return errors;
}
