/**
 * 真实响应形态的外币交易展示契约（http fetch 打桩 / 组件直渲，非端到端）：
 * USD 29.00 / 基础币 CNY 207.64（损益行 accountId=null + ledgerCode，
 * 账户行 a-usd 带 accountAmountCents=2900 accountCurrency="USD"，date 带 Z）。
 * - Admin Dashboard：本月支出按 baseAmountCents 含 207.64，绝不是原币 29.00，
 *   也绝不把基础币金额贴上 USD 标签；
 * - Admin Txns 列表：金额列显示基础币 -¥207.64 + 「原币 USD 29.00 · 汇率 7.16」，
 *   fxRate 缺失时不出现「汇率 null」；
 * - TxnDetailContent：账户行「账户币金额 USD 29.00」、金额行「（折 ¥207.64）」；
 * - CNY 账户代付外币：账户行 accountAmountCents/accountCurrency=折后 CNY 时，
 *   账户币金额按 CNY 显示，不显示成 USD。
 */
import { render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import AdminDashboardPage from "@/admin/pages/Dashboard";
import AdminTxnsPage from "@/admin/pages/Txns";
import { TxnDetailContent } from "@/components/TxnDetailContent";
import { accounts, categories, members } from "@/data/mock";
import type { Transaction } from "@/data/types";

const jsonResponse = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

/** 后端真实形态：USD 29.00 / base CNY 207.64（账户行另记原币实际变动） */
const usdTxn: Transaction = {
  id: "x-usd",
  date: "2026-08-03T03:20:00Z", // 上海 8/3 11:20
  type: "expense",
  payee: "Apple Store",
  note: "充电器（美元卡）",
  categoryId: "c-other",
  currency: "USD",
  fxRate: "7.16",
  amountCents: 29_00,
  baseAmountCents: 207_64,
  baseCurrency: "CNY",
  source: "manual",
  createdBy: "m-dad",
  version: 1,
  lines: [
    { accountId: null, ledgerCode: "expense:c-other", debitCents: 207_64, creditCents: 0, accountAmountCents: null, accountCurrency: null },
    { accountId: "a-usd", ledgerCode: null, debitCents: 0, creditCents: 207_64, accountAmountCents: 29_00, accountCurrency: "USD" },
  ],
};

/** 无汇率外币交易（导入补录后 fxRate 可能为空）：不得渲染「汇率 null」 */
const usdTxnNoFx: Transaction = {
  ...usdTxn,
  id: "x-usd-nofx",
  payee: "Amazon US",
  fxRate: undefined,
  date: "2026-08-04T03:20:00Z",
};

/** CNY 账户代付外币：账户币金额记折后 CNY（基础币），不得误贴 USD 标签 */
const cnyPaidFxTxn: Transaction = {
  ...usdTxn,
  id: "x-cny-paid",
  payee: "Booking.com",
  lines: [
    { accountId: null, ledgerCode: "expense:c-other", debitCents: 207_64, creditCents: 0, accountAmountCents: null, accountCurrency: null },
    { accountId: "a-cmb", ledgerCode: null, debitCents: 0, creditCents: 207_64, accountAmountCents: 207_64, accountCurrency: "CNY" },
  ],
};

const renderDetail = (txn: Transaction) =>
  render(
    <TxnDetailContent
      txn={txn}
      accounts={accounts}
      categories={categories}
      members={members}
    />,
  );

describe("真实形态 USD 交易：Admin Dashboard / Txns 列表（http 打桩）", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-08-05T12:00:00+08:00"));
    vi.stubEnv("VITE_USE_MOCKS", ""); // 关闭 mock，走 http 分支
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  const stubFetch = (txns: Transaction[]) => {
    const fetchMock = vi.fn().mockImplementation((input: unknown) => {
      const url = String(input);
      if (url === "/api/transactions") return Promise.resolve(jsonResponse(200, txns));
      if (url === "/api/accounts") return Promise.resolve(jsonResponse(200, accounts));
      if (url === "/api/categories") return Promise.resolve(jsonResponse(200, categories));
      if (url === "/api/members") return Promise.resolve(jsonResponse(200, members));
      if (url === "/api/import-batches") return Promise.resolve(jsonResponse(200, []));
      if (url === "/api/splits") return Promise.resolve(jsonResponse(200, []));
      if (url === "/api/backups") return Promise.resolve(jsonResponse(200, []));
      return Promise.resolve(jsonResponse(404, { detail: "not found" }));
    });
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  };

  it("Admin Dashboard：本月支出按基础币含 -¥207.64，不是原币 -¥29.00，也不把 207.64 贴 USD 标签", async () => {
    stubFetch([usdTxn]);
    render(
      <MemoryRouter>
        <AdminDashboardPage />
      </MemoryRouter>,
    );
    await screen.findByText("本月净支出");
    // 统计卡与最近交易行都以基础币展示（两处均可命中）
    expect(screen.getAllByText("-¥207.64").length).toBeGreaterThan(0);
    expect(screen.queryByText("-¥29.00")).not.toBeInTheDocument();
    expect(screen.queryByText(/USD 207\.64/)).not.toBeInTheDocument();
    // 最近交易外币行：基础币金额 + 「原币 USD 29.00」小字
    const row = screen.getByText("Apple Store").closest("tr")!;
    expect(row.textContent).toContain("-¥207.64");
    expect(row.textContent).toContain("原币");
    expect(row.textContent).toContain("USD 29.00");
    // 分类统计按 categoryId+type 汇总基础币：其他 ¥207.64（取分类行的 span，避开最近交易表单元格）
    const catName = screen.getAllByText("其他").find((el) => el.tagName === "SPAN")!;
    expect(catName.parentElement!.textContent).toContain("¥207.64");
  });

  it("Admin Txns 列表：金额列基础币 -¥207.64 + 原币 USD 29.00 · 汇率 7.16；fxRate 缺失无「汇率 null」", async () => {
    stubFetch([usdTxn, usdTxnNoFx]);
    render(
      <MemoryRouter>
        <AdminTxnsPage />
      </MemoryRouter>,
    );
    const row = (await screen.findByText("Apple Store")).closest("tr")!;
    expect(within(row).getByText("-¥207.64")).toBeInTheDocument();
    expect(row.textContent).toContain("原币");
    expect(row.textContent).toContain("USD 29.00");
    expect(row.textContent).toContain("汇率 7.16");
    // 无汇率的外币行：有原币小字但绝不出现「汇率 null」
    const noFxRow = screen.getByText("Amazon US").closest("tr")!;
    expect(noFxRow.textContent).toContain("原币");
    expect(noFxRow.textContent).toContain("USD 29.00");
    expect(screen.queryByText(/汇率 null/)).toBeNull();
  });
});

describe("真实形态 USD 交易：TxnDetailContent（组件直渲）", () => {
  it("账户行显示「账户币金额 USD 29.00」，金额行含「（折 ¥207.64）」，借贷为基础币", () => {
    renderDetail(usdTxn);
    // 金额行：原币 + 折基础币
    expect(screen.getByText("USD 29.00（折 ¥207.64）")).toBeInTheDocument();
    // 分录标题明示借贷口径
    expect(screen.getByText("复式分录（借贷平衡，借贷金额为基础币 CNY）")).toBeInTheDocument();
    // 账户行：科目为真实账户名 + 账户币实际变动
    expect(screen.getByText("美元账户")).toBeInTheDocument();
    expect(screen.getByText("账户币金额 USD 29.00")).toBeInTheDocument();
    // 损益行：ledgerCode 解析为「支出 · 分类名」，绝不裸露 expense:c-other
    expect(screen.getByText("支出 · 其他")).toBeInTheDocument();
    expect(screen.queryByText(/expense:c-other/)).toBeNull();
    expect(screen.queryByText(/equity:/)).toBeNull();
    // 借贷列合计为基础币 207.64（借方、贷方各一次）
    const table = screen.getByRole("table");
    expect(within(table).getAllByText("207.64")).toHaveLength(2);
  });

  it("uncategorized 损益行显示「支出 · 未分类」", () => {
    renderDetail({
      ...usdTxn,
      categoryId: null,
      lines: [
        { accountId: null, ledgerCode: "expense:uncategorized", debitCents: 207_64, creditCents: 0, accountAmountCents: null, accountCurrency: null },
        usdTxn.lines[1],
      ],
    });
    expect(screen.getByText("支出 · 未分类")).toBeInTheDocument();
    expect(screen.queryByText(/uncategorized/)).toBeNull();
  });

  it("CNY 账户代付外币：账户币金额按 CNY 显示（折后基础币），不显示成 USD", () => {
    renderDetail(cnyPaidFxTxn);
    expect(screen.getByText("招商银行储蓄卡")).toBeInTheDocument();
    expect(screen.getByText("账户币金额 CNY 207.64")).toBeInTheDocument();
    expect(screen.queryByText(/账户币金额 USD/)).toBeNull();
    // 交易本身仍是 USD 29.00 / 折 ¥207.64
    expect(screen.getByText("USD 29.00（折 ¥207.64）")).toBeInTheDocument();
  });
});
