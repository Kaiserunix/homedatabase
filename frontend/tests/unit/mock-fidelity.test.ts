/**
 * mock 忠于后端：buildLocalTransaction 与后端 create_transaction 同口径
 *（mock 模式直调 resources.createTransaction，setMockLatency(0)）。
 * - 外币缺 baseAmountCents 且无 fxRate → 422「外币交易必须提供实际人民币金额或固定汇率」；
 * - CNY 交易 baseAmountCents≠amountCents → 422「本币交易折算金额必须等于交易金额」；
 * - 仅 fxRate 时按 HALF_UP 精确折算（不经浮点）；
 * - 成功交易的分录形态与后端 presenter 一致：账户行带 accountAmountCents/accountCurrency
 *   （账户币实际变动），损益行 accountId=null + ledgerCode；CNY 账户代付外币时
 *   账户币金额记折后基础币。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/api/client";
import { createTransaction, setMockLatency } from "@/api/resources";
import type { NewTransactionInput } from "@/api/resources";

const baseInput: NewTransactionInput = {
  type: "expense",
  payee: "Apple Store",
  note: "",
  categoryId: "c-other",
  accountId: "a-usd",
  amountCents: 29_00,
  currency: "USD",
  date: "2026-08-03T03:20:00Z",
};

describe("mock 模式 createTransaction：与后端同口径（VITE_USE_MOCKS=true）", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_USE_MOCKS", "true");
    setMockLatency(0);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  it("外币缺 baseAmountCents 且无 fxRate：reject 422，绝不默认原币=CNY", async () => {
    const err = await createTransaction(baseInput).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(422);
    expect((err as ApiError).message).toBe("外币交易必须提供实际人民币金额或固定汇率");
  });

  it("CNY 交易 base≠amount：reject 422「本币交易折算金额必须等于交易金额」", async () => {
    const err = await createTransaction({
      ...baseInput,
      accountId: "a-cmb",
      currency: "CNY",
      amountCents: 12_50,
      baseAmountCents: 12_51,
    }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(422);
    expect((err as ApiError).message).toBe("本币交易折算金额必须等于交易金额");
  });

  it("CNY 交易 base==amount：成功，baseAmountCents=amountCents、baseCurrency=CNY", async () => {
    const txn = await createTransaction({
      ...baseInput,
      accountId: "a-cmb",
      currency: "CNY",
      amountCents: 12_50,
      baseAmountCents: 12_50,
    });
    expect(txn.baseAmountCents).toBe(12_50);
    expect(txn.baseCurrency).toBe("CNY");
    // CNY 账户行：账户币金额即基础币金额
    const acct = txn.lines.find((l) => l.accountId === "a-cmb")!;
    expect(acct).toMatchObject({ accountAmountCents: 12_50, accountCurrency: "CNY", ledgerCode: null });
  });

  it("外币带 baseAmountCents：成功，账户行记原币实际变动、损益行 ledgerCode 正确", async () => {
    const txn = await createTransaction({ ...baseInput, baseAmountCents: 207_64 });
    expect(txn.amountCents).toBe(29_00);
    expect(txn.baseAmountCents).toBe(207_64);
    expect(txn.baseCurrency).toBe("CNY");

    // 账户行：a-usd 为 USD 账户，账户币金额=原币 29.00；借贷为基础币 207.64
    const acct = txn.lines.find((l) => l.accountId === "a-usd")!;
    expect(acct).toMatchObject({
      ledgerCode: null,
      debitCents: 0,
      creditCents: 207_64,
      accountAmountCents: 29_00,
      accountCurrency: "USD",
    });
    // 损益行：accountId=null + ledgerCode=expense:<categoryId>，不带账户币金额
    const pl = txn.lines.find((l) => l.accountId === null)!;
    expect(pl).toMatchObject({
      ledgerCode: "expense:c-other",
      debitCents: 207_64,
      creditCents: 0,
      accountAmountCents: null,
      accountCurrency: null,
    });
    // 借贷平衡
    const debit = txn.lines.reduce((s, l) => s + l.debitCents, 0);
    const credit = txn.lines.reduce((s, l) => s + l.creditCents, 0);
    expect(debit).toBe(credit);
  });

  it("未分类支出：ledgerCode=expense:uncategorized", async () => {
    const txn = await createTransaction({ ...baseInput, categoryId: null, baseAmountCents: 207_64 });
    const pl = txn.lines.find((l) => l.accountId === null)!;
    expect(pl.ledgerCode).toBe("expense:uncategorized");
  });

  it("仅 fxRate：按 HALF_UP 精确折算（0.5 进位，不经浮点）", async () => {
    // 1 分 × 2.5 = 2.5 分 → HALF_UP 得 3（向下取整会给 2，银行家舍入也给 2）
    const txn = await createTransaction({
      ...baseInput,
      amountCents: 1,
      fxRate: "2.5",
    });
    expect(txn.baseAmountCents).toBe(3);
    expect(txn.fxRate).toBe("2.5");
  });

  it("CNY 账户代付外币：账户币金额记折后基础币（CNY），不记原币", async () => {
    const txn = await createTransaction({
      ...baseInput,
      accountId: "a-cmb", // CNY 账户
      currency: "USD",
      baseAmountCents: 207_64,
    });
    const acct = txn.lines.find((l) => l.accountId === "a-cmb")!;
    expect(acct).toMatchObject({ accountAmountCents: 207_64, accountCurrency: "CNY" });
  });

  it("转账：双账户行，账户币金额=交易金额（同币种）", async () => {
    const txn = await createTransaction({
      type: "transfer",
      payee: "招行转家庭账户",
      note: "",
      categoryId: null,
      accountId: "a-cmb",
      toAccountId: "a-family",
      amountCents: 100_00,
      currency: "CNY",
      date: "2026-08-03T03:20:00Z",
    });
    expect(txn.lines).toHaveLength(2);
    expect(txn.lines.every((l) => l.accountId !== null && l.ledgerCode === null)).toBe(true);
    expect(txn.lines.every((l) => l.accountAmountCents === 100_00 && l.accountCurrency === "CNY")).toBe(true);
    const from = txn.lines.find((l) => l.accountId === "a-cmb")!;
    const to = txn.lines.find((l) => l.accountId === "a-family")!;
    expect(from).toMatchObject({ debitCents: 0, creditCents: 100_00 });
    expect(to).toMatchObject({ debitCents: 100_00, creditCents: 0 });
  });
});
