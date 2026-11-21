import { describe, expect, it } from "vitest";
import type { Account, Category, Transaction } from "@/data/types";
import {
  formatCompactMonthLabel,
  formatSpacedMonthLabel,
  selectAvailableLedgerMonths,
  selectLatestActiveMonth,
  selectLedgerOverview,
} from "@/domain/ledger-insights";

const accounts: Account[] = [
  {
    id: "a-cny",
    name: "招行储蓄卡",
    kind: "asset",
    scope: "personal",
    ownerId: "m-dad",
    permission: "manage",
    currency: "CNY",
    balanceCents: 100_000_00,
    icon: "landmark",
    version: 1,
  },
  {
    id: "a-loan",
    name: "信用卡",
    kind: "liability",
    scope: "personal",
    ownerId: "m-dad",
    permission: "manage",
    currency: "CNY",
    balanceCents: 12_000_00,
    icon: "credit-card",
    version: 1,
  },
  {
    id: "a-usd",
    name: "美元账户",
    kind: "asset",
    scope: "personal",
    ownerId: "m-dad",
    permission: "manage",
    currency: "USD",
    balanceCents: 500_00,
    icon: "dollar-sign",
    version: 1,
  },
];

const categories: Category[] = [
  { id: "c-food", name: "餐饮", kind: "expense", parentId: null, icon: "utensils", active: true, version: 1 },
  { id: "c-home", name: "居家", kind: "expense", parentId: null, icon: "sofa", active: true, version: 1 },
  { id: "c-salary", name: "工资", kind: "income", parentId: null, icon: "briefcase", active: true, version: 1 },
];

const makeTxn = (overrides: Partial<Transaction>): Transaction => ({
  id: "t-1",
  date: "2026-08-05T04:00:00Z",
  type: "expense",
  payee: "商户",
  note: "",
  categoryId: "c-food",
  currency: "CNY",
  amountCents: 100_00,
  baseAmountCents: 100_00,
  baseCurrency: "CNY",
  lines: [],
  source: "manual",
  createdBy: "m-dad",
  version: 1,
  ...overrides,
});

describe("domain/ledger-insights 确定性聚合与多月选择器", () => {
  const txns: Transaction[] = [
    // 2026-07：净支出 200.00
    makeTxn({ id: "t-jul", date: "2026-07-15T04:00:00Z", amountCents: 200_00, baseAmountCents: 200_00, categoryId: "c-home" }),
    // 2026-08 跨月上海边界（UTC 7/31 16:30 = 上海 8/1 00:30）：餐饮支出 300.00
    makeTxn({ id: "t-aug-1", date: "2026-07-31T16:30:00Z", amountCents: 300_00, baseAmountCents: 300_00, categoryId: "c-food" }),
    // 2026-08：餐饮退款 50.00（冲减支出，净餐饮支出 = 250.00）
    makeTxn({ id: "t-aug-ref", date: "2026-08-02T04:00:00Z", type: "refund", refundOf: "t-aug-1", amountCents: 50_00, baseAmountCents: 50_00, categoryId: "c-food" }),
    // 2026-08：工资收入 1000.00
    makeTxn({ id: "t-aug-inc", date: "2026-08-10T02:00:00Z", type: "income", amountCents: 1_000_00, baseAmountCents: 1_000_00, categoryId: "c-salary" }),
  ];

  it("月份列表包含当前基准月与历史交易月（倒序去重），且识别最近有账月份", () => {
    expect(selectAvailableLedgerMonths(txns, "2026-11")).toEqual(["2026-11", "2026-08", "2026-07"]);
    expect(selectLatestActiveMonth(txns)).toBe("2026-08");
    expect(selectLatestActiveMonth([])).toBeNull();
    expect(formatCompactMonthLabel("2026-08")).toBe("2026年8月");
    expect(formatSpacedMonthLabel("2026-08")).toBe("2026 年 8 月");
  });

  it("selectLedgerOverview：精确核算净资产、外币隔离、净支出冲减退款、净结余与环比差额", () => {
    const aug = selectLedgerOverview({
      txns,
      accts: accounts,
      cats: categories,
      month: "2026-08",
      categoryScope: "month",
    });

    expect(aug.totalAssetCents).toBe(100_000_00n); // USD 账户不混加进 CNY 总资产
    expect(aug.totalLiabilityCents).toBe(12_000_00n);
    expect(aug.netWorthCents).toBe(88_000_00n);
    expect(aug.foreignAccounts.map((a) => a.id)).toEqual(["a-usd"]);

    expect(aug.monthTxnCount).toBe(3);
    expect(aug.monthIncomeCents).toBe(1_000_00n);
    expect(aug.monthExpenseGrossCents).toBe(300_00n);
    expect(aug.monthRefundCents).toBe(50_00n);
    expect(aug.monthNetExpenseCents).toBe(250_00n);
    expect(aug.monthNetCashflowCents).toBe(750_00n); // 1000 - 250

    // 上月 2026-07 净支出 200.00 → 8 月较 7 月多花 +50.00
    expect(aug.prevMonth).toBe("2026-07");
    expect(aug.prevMonthNetExpenseCents).toBe(200_00n);
    expect(aug.momExpenseDeltaCents).toBe(50_00n);

    // 当月按分类仅含 8 月餐饮（250.00），不含 7 月居家
    expect(aug.expenseByCategory.map((x) => [x.cat.id, x.total])).toEqual([["c-food", 250_00n]]);

    // 全部范围按分类同时含餐饮（250.00）与居家（200.00）
    const allScope = selectLedgerOverview({
      txns,
      accts: accounts,
      cats: categories,
      month: "2026-08",
      categoryScope: "all",
    });
    expect(allScope.expenseByCategory.map((x) => [x.cat.id, x.total])).toEqual([
      ["c-food", 250_00n],
      ["c-home", 200_00n],
    ]);
  });
});
