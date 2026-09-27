/**
 * 跨交易汇总口径：一律按基础币 baseAmountCents 合计、以 baseCurrency 展示。
 * 基础币不一致直接抛 RangeError（绝不静默混加）；空列表回退 CNY 零值，正常显示。
 */
import type { Transaction } from "@/data/types";
import { sumCents } from "./decimal";

/** 校验一组交易的基础币一致并返回之；空列表回退 "CNY" */
export function baseCurrencyOf(txns: readonly Transaction[]): string {
  let currency: string | null = null;
  for (const t of txns) {
    if (currency === null) currency = t.baseCurrency;
    else if (t.baseCurrency !== currency) {
      throw new RangeError(`基础币不一致（${currency} vs ${t.baseCurrency}），无法跨交易汇总`);
    }
  }
  return currency ?? "CNY";
}

/** 按基础币精确合计（bigint 贯穿，超界不失真） */
export function sumBaseAmounts(txns: readonly Transaction[]): { cents: bigint; currency: string } {
  return {
    cents: sumCents(txns.map((t) => t.baseAmountCents)),
    currency: baseCurrencyOf(txns),
  };
}
