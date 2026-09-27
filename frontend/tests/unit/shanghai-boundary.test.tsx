/**
 * 上海月首边界（页面级，http fetch 打桩，非端到端）：
 * 后端返回 UTC Z 的真实形态；2026-07-31T16:30:00Z（上海 8/1 00:30）必须
 * 被 Mobile Home / Mobile Txns / Admin Dashboard 归入 2026-08 账期月与
 * 8月1日 日分组，绝不能被 UTC/本地时区口径错划进 7 月。
 *
 * 判别设计：另放一笔上海 8/2 的交易（UTC 与上海同月）。
 * 若实现按 UTC 取月，「本月」= 2026-08 只会含 8/2 那笔（¥2.00）；
 * 正确的上海口径应含两笔（¥3.00）。
 */
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import MobileHomePage from "@/mobile/pages/Home";
import MobileTxnsPage from "@/mobile/pages/Txns";
import AdminDashboardPage from "@/admin/pages/Dashboard";
import { AuthProvider } from "@/auth/AuthContext";
import type { Member, Transaction } from "@/data/types";

const member: Member = {
  id: "m-dad",
  username: "dad",
  name: "爸爸",
  identity: "dad",
  isAdmin: true,
  color: "#3b82f6",
};

const jsonResponse = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

const txn = (overrides: Partial<Transaction>): Transaction => ({
  id: "x-1",
  date: "2026-08-02T03:00:00Z",
  type: "expense",
  payee: "商户",
  note: "",
  categoryId: null,
  currency: "CNY",
  amountCents: 1_00,
  baseAmountCents: 1_00,
  baseCurrency: "CNY",
  lines: [],
  source: "manual",
  createdBy: "m-dad",
  version: 1,
  ...overrides,
});

/** 边界交易：UTC 7/31 16:30 = 上海 8/1 00:30，必须归 2026-08 */
const boundaryTxn = txn({ id: "x-boundary", date: "2026-07-31T16:30:00Z", payee: "跨月边界消费", amountCents: 1_00, baseAmountCents: 1_00 });
/** 对照交易：UTC 8/2 03:00 = 上海 8/2 11:00，两种口径都归 2026-08 */
const aug2Txn = txn({ id: "x-aug2", date: "2026-08-02T03:00:00Z", payee: "八月消费", amountCents: 2_00, baseAmountCents: 2_00 });

const txns = [boundaryTxn, aug2Txn];

/** 通用打桩：各页面取数端点全部返回最小合法响应 */
const stubFetch = () => {
  const fetchMock = vi.fn().mockImplementation((input: unknown) => {
    const url = String(input);
    if (url === "/api/auth/me") return Promise.resolve(jsonResponse(200, member));
    if (url === "/api/transactions") return Promise.resolve(jsonResponse(200, txns));
    if (url === "/api/accounts") return Promise.resolve(jsonResponse(200, []));
    if (url === "/api/categories") return Promise.resolve(jsonResponse(200, []));
    if (url === "/api/members") return Promise.resolve(jsonResponse(200, [member]));
    if (url === "/api/import-batches") return Promise.resolve(jsonResponse(200, []));
    if (url === "/api/splits") return Promise.resolve(jsonResponse(200, []));
    if (url === "/api/backups") return Promise.resolve(jsonResponse(200, []));
    return Promise.resolve(jsonResponse(404, { detail: "not found" }));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
};

describe("上海月首边界（页面级）", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-08-02T12:00:00+08:00"));
    vi.stubEnv("VITE_USE_MOCKS", ""); // 关闭 mock，走 http 分支
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

  it("Mobile Home：边界交易计入上海 2026-08 本月支出（-¥3.00，不是 UTC 口径的 -¥2.00）", async () => {
    stubFetch();
    render(
      <MemoryRouter>
        <AuthProvider>
          <MobileHomePage />
        </AuthProvider>
      </MemoryRouter>,
    );
    await screen.findByText("本月净支出");
    // 合计 -¥3.00 只可能出现在统计卡（行金额各为 -¥1.00/-¥2.00）；
    // 若实现按 UTC 取月，本月只会含 8/2 那笔，此处应为 -¥2.00
    expect(screen.getByText("-¥3.00")).toBeInTheDocument();
    // 最近流水的日期显示也走上海口径：边界交易显示 8月1日
    expect(screen.getAllByText(/· 8月1日/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/7月31日/)).not.toBeInTheDocument();
  });

  it("Mobile Txns：日分组出现 8月1日 / 8月2日，绝不出现 7月31日 分组", async () => {
    stubFetch();
    render(
      <MemoryRouter>
        <MobileTxnsPage />
      </MemoryRouter>,
    );
    // 日分组标题（精确文本，排除行内「商户 · 8月1日」的拼接文本）
    expect(await screen.findByText("8月1日")).toBeInTheDocument();
    expect(screen.getByText("8月2日")).toBeInTheDocument();
    expect(screen.queryByText("7月31日")).toBeNull();
    // 边界交易落在 8月1日 分组里
    const group = screen.getByText("8月1日").closest("div")!.parentElement!;
    expect(group.textContent).toContain("跨月边界消费");
    expect(group.textContent).not.toContain("八月消费");
  });

  it("Admin Dashboard：本月支出按上海账期月含边界交易（-¥3.00），月标签 2026 年 8 月", async () => {
    stubFetch();
    render(
      <MemoryRouter>
        <AdminDashboardPage />
      </MemoryRouter>,
    );
    await screen.findByText("本月净支出");
    // 合计 -¥3.00 只可能出现在统计卡；UTC 口径会错算成 -¥2.00
    expect(screen.getByText("-¥3.00")).toBeInTheDocument();
    // 月标签（收入/支出两卡各一次）
    expect(screen.getAllByText("2026 年 8 月")).toHaveLength(2);
    // 最近交易表的日期列同样按上海口径
    expect(screen.getByText("8月1日")).toBeInTheDocument();
    expect(screen.queryByText("7月31日")).toBeNull();
  });
});
