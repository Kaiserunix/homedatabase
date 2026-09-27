/**
 * 分摊契约（http fetch 打桩 / mock 直调，非端到端）：
 * - Admin Splits 编辑份额弹窗标签按分摊币种动态展示：USD 分摊显示
 *   「新的应摊金额（USD）」，旧文案「新的应摊金额（元）」不得出现；
 * - 发起新分摊 POST /api/splits 请求体只有 {txnId,totalCents,shares}，
 *   绝不携带 title（标题由后端从原交易账面名称派生）；
 * - mock 模式 createSplit 与后端同口径：返回 title=原交易 payee、
 *   currency=原交易币，即使调用方夹带自填 title 也被忽略。
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import AdminSplitsPage from "@/admin/pages/Splits";
import { AuthProvider } from "@/auth/AuthContext";
import { createSplit, setMockLatency, type NewSplitInput } from "@/api/resources";
import type { Member, Split, Transaction } from "@/data/types";
import { transactions as mockTransactions, splits as mockSplits } from "@/data/mock";

const jsonResponse = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

const dad: Member = { id: "m-dad", username: "dad", name: "爸爸", identity: "dad", isAdmin: true, color: "#3b82f6" };
const members: Member[] = [
  dad,
  { id: "m-mom", username: "mom", name: "妈妈", identity: "mom", isAdmin: false, color: "#ec4899" },
  { id: "m-son", username: "son", name: "儿子", identity: "son", isAdmin: false, color: "#22c55e" },
];

/** USD 支出交易（真实形态：原币 USD 29.00 / 基础币 CNY 207.64） */
const usdTxn: Transaction = {
  id: "t-usd",
  date: "2026-08-03T03:20:00Z",
  type: "expense",
  payee: "Apple Store",
  note: "",
  categoryId: "c-other",
  currency: "USD",
  fxRate: "7.16",
  amountCents: 29_00,
  baseAmountCents: 207_64,
  baseCurrency: "CNY",
  lines: [],
  source: "manual",
  createdBy: "m-dad",
  version: 1,
};

/** USD 分摊：我发起，妈妈份额已退回 → 出现「修改份额」入口 */
const usdSplit: Split = {
  id: "s-usd",
  txnId: "t-usd",
  title: "Apple Store 充电器拼单",
  transactionDate: "2026-08-03T03:20:00Z",
  categoryId: "c-other",
  totalCents: 29_00,
  currency: "USD",
  createdBy: "m-dad",
  createdAt: "2026-08-03T04:00:00Z",
  status: "open",
  version: 1,
  shares: [
    { memberId: "m-dad", shareCents: 14_50, status: "accepted" },
    { memberId: "m-mom", shareCents: 14_50, status: "returned", note: "我不需要" },
  ],
};

const renderAdmin = () =>
  render(
    <MemoryRouter>
      <AuthProvider>
        <AdminSplitsPage />
      </AuthProvider>
    </MemoryRouter>,
  );

describe("Admin Splits：USD 分摊与发起分摊契约（http 打桩）", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_USE_MOCKS", ""); // 关闭 mock，走 http 分支
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  const stubFetch = (splits: Split[]) => {
    const fetchMock = vi.fn().mockImplementation((input: unknown, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/auth/me") return Promise.resolve(jsonResponse(200, dad));
      if (url === "/api/members") return Promise.resolve(jsonResponse(200, members));
      if (url === "/api/transactions") return Promise.resolve(jsonResponse(200, [usdTxn]));
      if (url === "/api/splits" && init?.method === "POST") {
        // 后端口径：标题从原交易账面名称派生
        const body = JSON.parse(String(init.body)) as { txnId: string; totalCents: number; shares: { memberId: string; shareCents: number }[] };
        return Promise.resolve(
          jsonResponse(201, {
            ...usdSplit,
            id: "s-new",
            title: usdTxn.payee,
            totalCents: body.totalCents,
            shares: body.shares.map((sh) => ({ ...sh, status: "pending" })),
          }),
        );
      }
      if (url === "/api/splits") return Promise.resolve(jsonResponse(200, splits));
      return Promise.resolve(jsonResponse(404, { detail: "not found" }));
    });
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  };

  it("USD 分摊编辑份额弹窗：标签「新的应摊金额（USD）」，无「（元）」旧文案", async () => {
    stubFetch([usdSplit]);
    const user = userEvent.setup();
    renderAdmin();

    await user.click(await screen.findByRole("button", { name: "修改份额" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("新的应摊金额（USD）")).toBeInTheDocument();
    expect(within(dialog).queryByText(/新的应摊金额（元）/)).toBeNull();
    // 预填为退回份额的原值（USD 14.50）
    expect(within(dialog).getByDisplayValue("14.50")).toBeInTheDocument();
  });

  it("发起新分摊：POST /api/splits 请求体为 {txnId,totalCents,shares}，无 title 键", async () => {
    const fetchMock = stubFetch([]);
    const user = userEvent.setup();
    renderAdmin();

    await user.click(await screen.findByRole("button", { name: /发起新分摊/ }));
    const dialog = await screen.findByRole("dialog");
    // 选择 USD 支出交易（选项文本：商户 · 日期 · 原币金额）
    await user.click(within(dialog).getByRole("combobox"));
    await user.click(await screen.findByRole("option", { name: /Apple Store/ }));
    // 参与人默认全选（3 人），直接发起
    await user.click(within(dialog).getByRole("button", { name: "发起分摊" }));

    await waitFor(() => {
      const posts = (fetchMock.mock.calls as [unknown, RequestInit?][]).filter(
        ([url, init]) => String(url) === "/api/splits" && init?.method === "POST",
      );
      expect(posts).toHaveLength(1);
    });
    const post = (fetchMock.mock.calls as [unknown, RequestInit?][]).find(
      ([url, init]) => String(url) === "/api/splits" && init?.method === "POST",
    )!;
    const body = JSON.parse(String(post[1]!.body)) as Record<string, unknown>;
    expect(body).toMatchObject({ txnId: "t-usd", totalCents: 29_00 });
    expect(body).not.toHaveProperty("title");
    // 均分总额精确等于原交易原币金额（USD 分）
    const shares = body.shares as { memberId: string; shareCents: number }[];
    expect(shares).toHaveLength(3);
    expect(shares.reduce((s, sh) => s + sh.shareCents, 0)).toBe(29_00);
  });
});

describe("mock 模式 createSplit：标题与币种从原交易派生", () => {
  const transactionCount = mockTransactions.length;
  const splitCount = mockSplits.length;
  beforeEach(() => {
    vi.stubEnv("VITE_USE_MOCKS", "true");
    setMockLatency(0);
    // Existing fixtures already have splits; create independent, unsplit transactions.
    for (const id of ["t-003", "t-008"]) {
      mockTransactions.push({ ...mockTransactions.find((txn) => txn.id === id)!, id: `${id}-fresh`, splitId: undefined });
    }
  });

  afterEach(() => {
    mockTransactions.splice(transactionCount);
    mockSplits.splice(splitCount);
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  it("CNY 交易 t-003：title=「海底捞」（payee），currency=CNY", async () => {
    const created = await createSplit({
      txnId: "t-003-fresh",
      totalCents: 468_00,
      shares: [
        { memberId: "m-dad", shareCents: 234_00 },
        { memberId: "m-mom", shareCents: 234_00 },
      ],
    });
    expect(created.title).toBe("海底捞");
    expect(created.currency).toBe("CNY");
    expect(created.shares.find((sh) => sh.memberId === "m-dad")?.status).toBe("accepted");
    expect(created.shares.find((sh) => sh.memberId === "m-mom")?.status).toBe("pending");
  });

  it("USD 交易 t-008：title=「Apple Store」，currency=USD", async () => {
    const created = await createSplit({
      txnId: "t-008-fresh",
      totalCents: 29_00,
      shares: [{ memberId: "m-dad", shareCents: 14_50 }, { memberId: "m-mom", shareCents: 14_50 }],
    });
    expect(created.title).toBe("Apple Store");
    expect(created.currency).toBe("USD");
  });

  it("调用方夹带自填 title 也被忽略（与后端不接受客户端标题同口径）", async () => {
    const rogue = {
      txnId: "t-003-fresh",
      totalCents: 468_00,
      shares: [{ memberId: "m-dad", shareCents: 234_00 }, { memberId: "m-mom", shareCents: 234_00 }],
      title: "自填标题",
    } as NewSplitInput;
    const created = await createSplit(rogue);
    expect(created.title).toBe("海底捞");
  });
});
