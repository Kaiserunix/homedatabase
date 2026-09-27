/**
 * 导入候选的可确认判定（与后端口径一致）：
 * 后端只允许 status=pending 且 validationStatus=valid 的候选确认；
 * needs_fx_review、exact_duplicate、duplicate_source_record 以及其他
 * 非 valid 状态一律不可默认勾选、不可确认——前端绝不把必然 422 的
 * 候选发给后端。needs_fx_review 的外币候选由人工补全 fxRate /
 * baseAmountCents 并 PATCH 保存后，后端会重新给出 validationStatus。
 */
import type { ImportCandidate } from "@/data/types";
import { isPositiveDecimalString, parseDecimalCents } from "@/lib/decimal";

export const FX_REVIEW_STATUS = "needs_fx_review";

/** 后端判定为重复的 validationStatus；duplicate_source_record 可能没有 duplicateOfTxnId */
const DUPLICATE_STATUSES = new Set(["exact_duplicate", "duplicate_source_record"]);

/** 候选是否处于「待补汇率」状态（仅待确认的候选才需要人工补录） */
export function needsFxReview(c: ImportCandidate): boolean {
  return c.status === "pending" && c.validationStatus === FX_REVIEW_STATUS;
}

/** 疑似重复：后端标了重复交易 ID，或 validationStatus 判定为重复（不只看 duplicateOfTxnId） */
export function isDuplicate(c: ImportCandidate): boolean {
  return c.duplicateOfTxnId != null || DUPLICATE_STATUSES.has(c.validationStatus);
}

/** 候选当前可否被勾选确认：仅 pending 且 validationStatus=valid（与后端确认口径一致） */
export function isConfirmable(c: ImportCandidate): boolean {
  return c.status === "pending" && c.validationStatus === "valid";
}

/** 解析并校验人工输入的汇率与折合人民币金额（元字符串）；不合法时返回错误文案 */
export function parseFxInput(
  fxRateInput: string,
  baseAmountInput: string,
): { ok: true; fxRate: string; baseAmountCents: number } | { ok: false; error: string } {
  // 汇率以原字符串提交，正数校验用纯字符串判定（不经 Number，避免大数失真）
  const fx = fxRateInput.trim();
  if (!isPositiveDecimalString(fx)) {
    return { ok: false, error: "请输入有效汇率（大于 0 的数字）" };
  }
  // 折合人民币金额：精确十进制解析，超界/三位小数/负值一律拒绝
  const parsed = parseDecimalCents(baseAmountInput);
  if (!parsed.ok) {
    return { ok: false, error: "请输入折合人民币金额（大于 0，最多两位小数）" };
  }
  if (parsed.cents <= 0) {
    return { ok: false, error: "请输入折合人民币金额（大于 0，最多两位小数）" };
  }
  return { ok: true, fxRate: fx, baseAmountCents: parsed.cents };
}
