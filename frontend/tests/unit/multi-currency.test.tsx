/**
 * 外币金额语义（契约级，非端到端）：
 * - 管理端账户详情近期交易：按真实账户分录的 accountAmountCents/accountCurrency 展示原币变动，
 *   不把基础币金额贴原币标签（USD 账户的 t-008 必须是 -USD 29.00，而非 -USD 207.64）；
 * - 手机首页/流水跨交易统计一律汇总 baseAmountCents 并以 baseCurrency 展示；
 * - 双端分摊总额/份额按 split.currency（原交易币）展示，USD 不显示 ¥；
 * - 基础币不一致明确抛错防御，不静默混加；空数据正常显示。
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import AdminAccountsPage from "@/admin/pages/Accounts";
import AdminSplitsPage from "@/admin/pages/Splits";
import MobileHomePage from "@/mobile/pages/Home";
import MobileTxnsPage from "@/mobile/pages/Txns";
import MobileSplitsPage from "@/mobile/pages/Splits";
import { AuthProvider } from "@/auth/AuthContext";
import { setMockLatency } from "@/api/resources";
import { baseCurrencyOf, sumBaseAmounts } from "@/lib/aggregate";
import type { Transaction } from "@/data/types";

const jsonResponse = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

const renderWithProviders = (ui: React.ReactElement) =>
  render(
    <MemoryRouter>
      <AuthProvider>{ui}</AuthProvider>
    </MemoryRouter>,
  );

describe("外币语义（mock 模式，含 USD t-008 与 USD 分摊 s-003）", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-07-31T12:00:00+08:00"));
    vi.stubEnv("VITE_USE_MOCKS", "true");
    setMockLatency(0);
    // jsdom 无 matchMedia（InstallPrompt 依赖），补最小实现
    if (!window.matchMedia) {
      window.matchMedia = ((query: string) => ({
        matches: false,
        media: query,
        onchange: null,
        addEventListener: () => {},
        removeEventListener: () => {},
        addListener: () => {},
        removeListener: () => {},
        dispatchEvent: () => false,
      })) as unknown as typeof window.matchMedia;
    }
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  it("Admin Accounts：USD 账户近期交易显示 -USD 29.00，不是基础币 -USD 207.64", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AdminAccountsPage />);

    await user.click(await screen.findByText("美元账户"));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("近期交易")).toBeInTheDocument();
    expect(within(dialog).getByText("Apple Store")).toBeInTheDocument();
    // 原币口径：-USD 29.00；绝不把基础币 207.64 贴上 USD 标签
    expect(within(dialog).getByText("-USD 29.00")).toBeInTheDocument();
    expect(within(dialog).queryByText(/207\.64/)).not.toBeInTheDocument();
  });

  it("Mobile Home：本月净支出按 baseAmountCents 扣除退款，显示 -¥1,189.34", async () => {
    renderWithProviders(<MobileHomePage />);
    await screen.findByText("本月净支出");
    // 7 月支出 1448.34 元（含 USD 折算 207.64），扣除 259 元退款。
    expect(screen.getByText("-¥1,189.34")).toBeInTheDocument();
    expect(screen.getByText("+¥24,000.00")).toBeInTheDocument();
    expect(screen.queryByText(/1,269\.70/)).not.toBeInTheDocument();
  });

  it("Admin Splits：USD 分摊总额与份额均为 USD，不出现 ¥ 标签", async () => {
    renderWithProviders(<AdminSplitsPage />);
    const title = await screen.findByText("Apple Store");
    const card = title.closest("[class*='p-5']")!;
    expect(within(card as HTMLElement).getByText("USD 29.00")).toBeInTheDocument();
    expect(within(card as HTMLElement).getAllByText("USD 14.50")).toHaveLength(2);
    expect(within(card as HTMLElement).queryByText(/¥/)).not.toBeInTheDocument();
  });

  it("Mobile Splits：我发起的 USD 分摊总额与份额均为 USD", async () => {
    const user = userEvent.setup();
    renderWithProviders(<MobileSplitsPage />);
    await user.click(await screen.findByRole("tab", { name: "我发起的" }));
    const title = await screen.findByText("Apple Store");
    const card = title.closest("[class*='p-4']")!;
    expect(within(card as HTMLElement).getByText("USD 29.00")).toBeInTheDocument();
    expect(within(card as HTMLElement).getAllByText("USD 14.50")).toHaveLength(2);
    expect(within(card as HTMLElement).queryByText(/¥/)).not.toBeInTheDocument();
  });
});

// ---------- Mobile Txns 混合币种按日汇总（http fetch 打桩，自造同日混合币种流水） ----------

const txnFixture = (overrides: Partial<Transaction>): Transaction => ({
  id: "x-1",
  // 后端真实形态：UTC Z；上海口径归 2026-08-03（09:00Z = 上海 17:00），日分组不漂
  date: "2026-08-03T09:00:00Z",
  type: "expense",
  payee: "商户",
  note: "",
  categoryId: null,
  currency: "CNY",
  amountCents: 6_00,
  baseAmountCents: 6_00,
  baseCurrency: "CNY",
  lines: [],
  source: "manual",
  createdBy: "m-dad",
  version: 1,
  ...overrides,
});

const mixedDayTxns: Transaction[] = [
  txnFixture({ id: "x-cny", payee: "地铁" }),
  txnFixture({
    id: "x-usd",
    payee: "Apple Store",
    currency: "USD",
    fxRate: "7.16",
    amountCents: 29_00,
    baseAmountCents: 207_64,
  }),
];

describe("Mobile Txns：某日混合币种按 baseAmountCents 汇总", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_USE_MOCKS", "");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  it("CNY 6.00 + USD 29.00（折 CNY 207.64）同日：当日支出 -¥213.64（基础币口径）", async () => {
    const fetchMock = vi.fn().mockImplementation((input: unknown) => {
      const url = String(input);
      if (url === "/api/transactions") return Promise.resolve(jsonResponse(200, mixedDayTxns));
      if (url === "/api/accounts") return Promise.resolve(jsonResponse(200, []));
      if (url === "/api/categories") return Promise.resolve(jsonResponse(200, []));
      if (url === "/api/members") return Promise.resolve(jsonResponse(200, []));
      if (url === "/api/import-batches") return Promise.resolve(jsonResponse(200, []));
      if (url === "/api/splits") return Promise.resolve(jsonResponse(200, []));
      return Promise.resolve(jsonResponse(404, { detail: "not found" }));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter>
        <MobileTxnsPage />
      </MemoryRouter>,
    );

    // 行金额仍各按原币展示
    expect(await screen.findByText("-USD 29.00")).toBeInTheDocument();
    expect(screen.getByText("-¥6.00")).toBeInTheDocument();
    // 当日支出：基础币合计 6.00 + 207.64 = 213.64，币种 CNY
    expect(screen.getByText("当日净支出")).toBeInTheDocument();
    expect(screen.getByText("-¥213.64")).toBeInTheDocument();
    expect(screen.queryByText(/-USD 213\.64/)).not.toBeInTheDocument();
    expect(screen.queryByText(/-¥35\.00/)).not.toBeInTheDocument(); // 不是原币混加 6.00+29.00
  });
});

// ---------- 基础币汇总防御（纯函数级） ----------

describe("基础币汇总防御", () => {
  it("基础币不一致直接抛 RangeError，不静默混加", () => {
    const bad: Transaction[] = [
      txnFixture({ id: "a" }),
      txnFixture({ id: "b", baseCurrency: "USD" }),
    ];
    expect(() => sumBaseAmounts(bad)).toThrow(RangeError);
    expect(() => baseCurrencyOf(bad)).toThrow(/基础币不一致/);
  });

  it("空数据回退 CNY 零值，正常显示", () => {
    expect(sumBaseAmounts([])).toEqual({ cents: 0n, currency: "CNY" });
    expect(baseCurrencyOf([])).toBe("CNY");
  });

  it("同基础币精确合计（含外币折算口径）", () => {
    expect(sumBaseAmounts(mixedDayTxns)).toEqual({ cents: 213_64n, currency: "CNY" });
  });
});
