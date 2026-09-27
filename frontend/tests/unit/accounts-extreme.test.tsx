/**
 * 极值合计显示（http fetch 打桩，契约级，非端到端）：
 * 合计超 MAX_SAFE 但每个后端字段都合法时，两端账户页必须精确显示，
 * 不得抛 RangeError 进入 ErrorBoundary。bigint 贯穿聚合与格式化。
 */
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import AdminAccountsPage from "@/admin/pages/Accounts";
import MobileAccountsPage from "@/mobile/pages/Accounts";
import type { Account } from "@/data/types";

const jsonResponse = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

const bigAccount = (id: string, name: string, balanceCents: number): Account => ({
  id,
  name,
  kind: "asset",
  scope: "personal",
  ownerId: "m-dad",
  currency: "CNY",
  balanceCents,
  icon: "landmark",
  permission: "manage",
  version: 1,
});

// MAX_SAFE + 1 分：合计 9007199254740992 分 = 90,071,992,547,409.92 元
const extremeAccounts = [
  bigAccount("a-1", "大额账户", 9007199254740991),
  bigAccount("a-2", "零头账户", 1),
];

function stubFetch() {
  const fetchMock = vi.fn().mockImplementation((input: unknown) => {
    const url = String(input);
    if (url === "/api/accounts") return Promise.resolve(jsonResponse(200, extremeAccounts));
    if (url === "/api/transactions") return Promise.resolve(jsonResponse(200, []));
    if (url === "/api/members") return Promise.resolve(jsonResponse(200, []));
    return Promise.resolve(jsonResponse(404, { detail: "not found" }));
  });
  vi.stubGlobal("fetch", fetchMock);
}

describe("极值合计精确显示（不进 ErrorBoundary）", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_USE_MOCKS", ""); // 关闭 mock，走 http 分支
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  it("Admin Accounts：MAX_SAFE + 1 分合计精确显示 90,071,992,547,409.92", async () => {
    stubFetch();
    render(
      <MemoryRouter>
        <AdminAccountsPage />
      </MemoryRouter>,
    );
    // 资产合计与净资产都是同一精确文案；页面未进错误边界
    const hits = await screen.findAllByText("¥90,071,992,547,409.92");
    expect(hits.length).toBeGreaterThanOrEqual(2);
    expect(screen.queryByText(/页面出了点问题/)).not.toBeInTheDocument();
    expect(screen.queryByText(/账户加载失败/)).not.toBeInTheDocument();
  });

  it("Mobile Accounts：MAX_SAFE + 1 分合计精确显示且不抛错", async () => {
    stubFetch();
    render(
      <MemoryRouter>
        <MobileAccountsPage />
      </MemoryRouter>,
    );
    const hits = await screen.findAllByText("¥90,071,992,547,409.92");
    expect(hits.length).toBeGreaterThanOrEqual(2); // 资产合计 + 净资产
    expect(screen.queryByText(/页面出了点问题/)).not.toBeInTheDocument();
    expect(screen.queryByText(/账户加载失败/)).not.toBeInTheDocument();
  });
});
