/**
 * 账本概览、现金流对比与多月选择器的纯确定性领域模块（零 React / DOM 依赖）。
 *
 * 口径铁律：
 * - 所有金额合计全程走 BigInt（sumCents），超 Number.MAX_SAFE_INTEGER 不失真；
 * - 跨交易统计一律按基础币 baseAmountCents 汇总并由 baseCurrencyOf 防御币种混加；
 * - 月份归属一律按 Asia/Shanghai 账期月（ledgerMonthKey），禁止对 ISO 字符串直接切片；
 * - 净支出 = 支出 − 退款（退款按自身账期归月冲减支出，绝不虚增收入）。
 */
import type { Account, Category, Transaction } from "@/data/types";
import { baseCurrencyOf, sumBaseAmounts } from "@/lib/aggregate";
import { sumCents } from "@/lib/decimal";
import { currentLedgerMonthKey, ledgerMonthKey, shiftLedgerMonthKey } from "@/lib/ledger-date";
import { accountDisplayName } from "@/lib/privacy";

/** 交易涉及的第一个真实账户展示名（损益行跳过；隐私对手方自动脱敏） */
export function mainAccountName(t: Transaction, accts: readonly Account[]): string {
  const line = t.lines.find((l) => l.accountId !== null);
  return line?.accountId ? accountDisplayName(line.accountId, [...accts], () => "—") : "—";
}

/** 推导所有可选上海账期月（倒序排列，始终包含基准当前月） */
export function selectAvailableLedgerMonths(
  txns: readonly Transaction[],
  currentMonth: string = currentLedgerMonthKey(),
): string[] {
  const set = new Set<string>([currentMonth]);
  for (const t of txns) {
    set.add(ledgerMonthKey(t.date));
  }
  return [...set].sort((a, b) => b.localeCompare(a));
}

/** 返回账本中最近一个实际存在交易的上海账期月（空账本返回 null） */
export function selectLatestActiveMonth(txns: readonly Transaction[]): string | null {
  let latest: string | null = null;
  for (const t of txns) {
    const m = ledgerMonthKey(t.date);
    if (latest === null || m > latest) latest = m;
  }
  return latest;
}

/** 紧凑月份文案（不含空格，避免与统计卡副标题冲突）："2026-08" → "2026年8月" */
export function formatCompactMonthLabel(monthKey: string): string {
  const [y, m] = monthKey.split("-");
  return `${Number(y)}年${Number(m)}月`;
}

/** 统计卡副标题月份文案："2026-08" → "2026 年 8 月" */
export function formatSpacedMonthLabel(monthKey: string): string {
  const [y, m] = monthKey.split("-");
  return `${y} 年 ${Number(m)} 月`;
}

export interface CategoryExpenseItem {
  cat: Category;
  total: bigint;
  /** 绘图进度条百分比宽度（4 ~ 100），有界安全近似 */
  barWidthPct: number;
}

export interface LedgerOverview {
  month: string;
  prevMonth: string;
  baseCurrency: string;
  /** 人民币资产账户余额合计（外币账户不硬折算，见 foreignAccounts） */
  totalAssetCents: bigint;
  /** 人民币负债账户余额合计 */
  totalLiabilityCents: bigint;
  /** 人民币净资产 = 人民币总资产 − 人民币总负债 */
  netWorthCents: bigint;
  /** 非人民币账户列表（按原币如实展示余额） */
  foreignAccounts: Account[];
  monthTxnCount: number;
  monthIncomeCents: bigint;
  monthExpenseGrossCents: bigint;
  monthRefundCents: bigint;
  /** 当期净支出 = 支出 − 退款 */
  monthNetExpenseCents: bigint;
  /** 当期净结余（现金流）= 收入 − 净支出 */
  monthNetCashflowCents: bigint;
  /** 上一自然月交易笔数 */
  prevMonthTxnCount: number;
  /** 上一自然月净支出（仅同基础币时有效） */
  prevMonthNetExpenseCents: bigint | null;
  /** 较上月净支出差额（正数表示多花，负数表示少花；上月无记录时为 null） */
  momExpenseDeltaCents: bigint | null;
  /** 按启用支出分类统计的净支出排行（> 0n） */
  expenseByCategory: CategoryExpenseItem[];
  /** 按时间倒序的最近交易 */
  recentTxns: Transaction[];
}

export function selectLedgerOverview(params: {
  txns: readonly Transaction[];
  accts: readonly Account[];
  cats?: readonly Category[];
  month?: string;
  categoryScope?: "month" | "all";
  recentLimit?: number;
}): LedgerOverview {
  const {
    txns,
    accts,
    cats = [],
    month = currentLedgerMonthKey(),
    categoryScope = "month",
    recentLimit = 8,
  } = params;

  const totalAssetCents = sumCents(
    accts.filter((a) => a.kind === "asset" && a.currency === "CNY").map((a) => a.balanceCents),
  );
  const totalLiabilityCents = sumCents(
    accts.filter((a) => a.kind === "liability" && a.currency === "CNY").map((a) => a.balanceCents),
  );
  const netWorthCents = totalAssetCents - totalLiabilityCents;
  const foreignAccounts = accts.filter((a) => a.currency !== "CNY");

  const inMonth = txns.filter((t) => ledgerMonthKey(t.date) === month);
  const baseCurrency = baseCurrencyOf(inMonth);

  const monthIncome = sumBaseAmounts(inMonth.filter((t) => t.type === "income"));
  const monthExpenseGross = sumBaseAmounts(inMonth.filter((t) => t.type === "expense"));
  const monthRefund = sumBaseAmounts(inMonth.filter((t) => t.type === "refund"));
  const monthNetExpenseCents = monthExpenseGross.cents - monthRefund.cents;
  const monthNetCashflowCents = monthIncome.cents - monthNetExpenseCents;

  const prevMonth = shiftLedgerMonthKey(month, -1);
  const inPrevMonth = txns.filter((t) => ledgerMonthKey(t.date) === prevMonth);
  let prevMonthNetExpenseCents: bigint | null = null;
  let momExpenseDeltaCents: bigint | null = null;
  if (inPrevMonth.length > 0) {
    const prevCurrency = baseCurrencyOf(inPrevMonth);
    if (inMonth.length === 0 || prevCurrency === baseCurrency) {
      const prevExp = sumCents(
        inPrevMonth.filter((t) => t.type === "expense").map((t) => t.baseAmountCents),
      );
      const prevRef = sumCents(
        inPrevMonth.filter((t) => t.type === "refund").map((t) => t.baseAmountCents),
      );
      prevMonthNetExpenseCents = prevExp - prevRef;
      momExpenseDeltaCents = monthNetExpenseCents - prevMonthNetExpenseCents;
    }
  }

  const scopedForCat = categoryScope === "month" ? inMonth : txns;
  const rawCats = cats
    .filter((c) => c.kind === "expense" && c.active)
    .map((c) => ({
      cat: c,
      total:
        sumCents(
          scopedForCat.filter((t) => t.type === "expense" && t.categoryId === c.id).map((t) => t.baseAmountCents),
        ) -
        sumCents(
          scopedForCat.filter((t) => t.type === "refund" && t.categoryId === c.id).map((t) => t.baseAmountCents),
        ),
    }))
    .filter((x) => x.total > 0n)
    .sort((a, b) => (a.total === b.total ? 0 : b.total > a.total ? 1 : -1));

  const maxCat = rawCats[0]?.total ?? 1n;
  const expenseByCategory: CategoryExpenseItem[] = rawCats.map(({ cat, total }) => ({
    cat,
    total,
    barWidthPct: Math.max(4, Number((total * 10000n) / maxCat) / 100),
  }));

  const recentTxns = [...txns].sort((a, b) => b.date.localeCompare(a.date)).slice(0, recentLimit);

  return {
    month,
    prevMonth,
    baseCurrency,
    totalAssetCents,
    totalLiabilityCents,
    netWorthCents,
    foreignAccounts,
    monthTxnCount: inMonth.length,
    monthIncomeCents: monthIncome.cents,
    monthExpenseGrossCents: monthExpenseGross.cents,
    monthRefundCents: monthRefund.cents,
    monthNetExpenseCents,
    monthNetCashflowCents,
    prevMonthTxnCount: inPrevMonth.length,
    prevMonthNetExpenseCents,
    momExpenseDeltaCents,
    expenseByCategory,
    recentTxns,
  };
}
