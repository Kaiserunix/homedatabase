/**
 * 管理端账户改名 409 竞态（http fetch 打桩，契约级，非端到端）：
 * 收到 409 必须立即关闭弹窗并丢弃旧快照、进入写锁；await 最新账户 GET，
 * 仅成功才解锁并提示已刷新；GET 失败诚实提示并维持锁定，提供重试。
 */
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import AccountsPage from "@/admin/pages/Accounts";
import type { Account } from "@/data/types";

const jsonResponse = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

const account = (name: string, version: number): Account => ({
  id: "a-1",
  name,
  kind: "asset",
  scope: "personal",
  ownerId: "m-dad",
  currency: "CNY",
  balanceCents: 100_00,
  icon: "landmark",
  permission: "manage",
  version,
});

interface StubControl {
  patchBodies: unknown[];
  /** 置位后 GET /accounts 一律 500（覆盖 apiFetch 自动重试） */
  failGet: boolean;
  /** 非 null 时 GET /accounts 返回 deferred promise，由测试 resolve */
  deferGet: { promise: Promise<Account[]>; resolve: (v: Account[]) => void } | null;
  patchCalls: number;
  /** true 时 PATCH 不制造 409，直接以 expectedVersion+1 返回成功 */
  noConflict?: boolean;
}

function stubFetch(ctrl: StubControl) {
  let loaded = false;
  const fetchMock = vi.fn().mockImplementation((input: unknown, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    if (url === "/api/accounts" && method === "GET") {
      if (!loaded) {
        loaded = true;
        return Promise.resolve(jsonResponse(200, [account("招商银行储蓄卡", 1)]));
      }
      if (ctrl.deferGet) return ctrl.deferGet.promise.then((v) => jsonResponse(200, v));
      if (ctrl.failGet) return Promise.resolve(jsonResponse(500, { detail: "服务器内部错误" }));
      return Promise.resolve(jsonResponse(200, [account("招行储蓄卡（他人改）", 2)]));
    }
    if (url === "/api/transactions") return Promise.resolve(jsonResponse(200, []));
    if (url === "/api/members") return Promise.resolve(jsonResponse(200, []));
    if (url === "/api/accounts/a-1" && method === "PATCH") {
      ctrl.patchCalls += 1;
      const body = JSON.parse(String(init?.body));
      ctrl.patchBodies.push(body);
      if (!ctrl.noConflict && ctrl.patchCalls === 1) {
        return Promise.resolve(jsonResponse(409, { detail: "版本冲突，请刷新后重试" }));
      }
      // 成功：版本前进到 expectedVersion+1
      return Promise.resolve(jsonResponse(200, account("招行储蓄卡（我改）", body.expectedVersion + 1)));
    }
    return Promise.resolve(jsonResponse(404, { detail: "not found" }));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const renderPage = () =>
  render(
    <MemoryRouter>
      <AccountsPage />
    </MemoryRouter>,
  );

/** 从卡片进入详情弹窗并打开改名弹窗 */
async function openDetailAndRename(user: ReturnType<typeof userEvent.setup>, cardName: string) {
  await user.click(await screen.findByText(cardName));
  await user.click(screen.getByRole("button", { name: "改名 / 图标" }));
  await screen.findByText(/当前版本 v/);
}

describe("账户改名 409：写锁与刷新", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_USE_MOCKS", ""); // 关闭 mock，走 http 分支
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  it("deferred GET：409 后旧弹窗立即关闭且锁定；刷新 v2 成功后重开携带 expectedVersion=2", async () => {
    let resolveGet!: (v: Account[]) => void;
    const ctrl: StubControl = {
      patchBodies: [],
      failGet: false,
      deferGet: { promise: new Promise((r) => (resolveGet = r)), resolve: null as never },
      patchCalls: 0,
    };
    ctrl.deferGet!.resolve = resolveGet;
    stubFetch(ctrl);

    const user = userEvent.setup();
    renderPage();
    await openDetailAndRename(user, "招商银行储蓄卡");
    await user.click(screen.getByRole("button", { name: "保存" }));

    // 409：改名弹窗立即关闭（不存在「保存」可重提旧版本），详情弹窗内写操作已锁
    await waitFor(() => expect(ctrl.patchCalls).toBe(1));
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "保存" })).not.toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: "改名 / 图标" })).toBeDisabled();
    // 关掉详情弹窗后，页面级写操作（新增账户）同样锁定
    await user.keyboard("{Escape}");
    expect(screen.getByRole("button", { name: /新增账户/ })).toBeDisabled();

    // 刷新 v2 成功：解锁并提示已刷新
    await act(async () => {
      resolveGet([account("招行储蓄卡（他人改）", 2)]);
    });
    await screen.findByText(/已为你刷新最新数据/);
    await waitFor(() => expect(screen.getByRole("button", { name: /新增账户/ })).toBeEnabled());

    // 重新打开并保存：携带刷新后的 expectedVersion=2；返回 v3 立即成为权威对象
    await openDetailAndRename(user, "招行储蓄卡（他人改）");
    await user.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(ctrl.patchCalls).toBe(2));
    expect(ctrl.patchBodies[0]).toMatchObject({ expectedVersion: 1 });
    expect(ctrl.patchBodies[1]).toMatchObject({ expectedVersion: 2 });

    // 详情弹窗仍开着且已原子更新为 v3；第三次重开只能携带 expectedVersion=3
    await screen.findByText(/当前余额/);
    expect((await screen.findAllByText("招行储蓄卡（我改）")).length).toBeGreaterThan(0);
    await user.click(screen.getByRole("button", { name: "改名 / 图标" }));
    await screen.findByText(/当前版本 v3/);
    await user.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(ctrl.patchCalls).toBe(3));
    expect(ctrl.patchBodies[2]).toMatchObject({ expectedVersion: 3 });
  });

  it("普通 PATCH v1 返回 v2 后立即再开：只能携带 expectedVersion=2（不经列表刷新）", async () => {
    const ctrl: StubControl = {
      patchBodies: [],
      failGet: false,
      deferGet: null,
      patchCalls: 0,
      noConflict: true,
    };
    stubFetch(ctrl);

    const user = userEvent.setup();
    renderPage();
    await openDetailAndRename(user, "招商银行储蓄卡");
    await user.click(screen.getByRole("button", { name: "保存" }));

    // 成功返回 v2 立即生效：详情弹窗仍开着、显示新版本
    await waitFor(() => expect(ctrl.patchCalls).toBe(1));
    expect((await screen.findAllByText("招行储蓄卡（我改）")).length).toBeGreaterThan(0);

    // 立即再开保存：携带的是 v2，而不是旧 v1
    await user.click(screen.getByRole("button", { name: "改名 / 图标" }));
    await screen.findByText(/当前版本 v2/);
    await user.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(ctrl.patchCalls).toBe(2));
    expect(ctrl.patchBodies[0]).toMatchObject({ expectedVersion: 1 });
    expect(ctrl.patchBodies[1]).toMatchObject({ expectedVersion: 2 });
  });

  it("GET 失败：诚实提示刷新失败并维持写锁；重试刷新成功后才解锁", async () => {
    const ctrl: StubControl = { patchBodies: [], failGet: true, deferGet: null, patchCalls: 0 };
    stubFetch(ctrl);

    const user = userEvent.setup();
    renderPage();
    await openDetailAndRename(user, "招商银行储蓄卡");
    await user.click(screen.getByRole("button", { name: "保存" }));

    // 刷新失败：明示失败、弹窗已关、写锁维持，不声称已刷新
    await screen.findByText(/刷新最新数据失败/);
    expect(screen.queryByText(/已为你刷新最新数据/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "保存" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "改名 / 图标" })).toBeDisabled();
    expect(ctrl.patchCalls).toBe(1);
    await user.keyboard("{Escape}");
    expect(screen.getByRole("button", { name: /新增账户/ })).toBeDisabled();

    // 重试刷新成功：解锁
    ctrl.failGet = false;
    await user.click(screen.getByRole("button", { name: "重试刷新" }));
    await screen.findByText(/已为你刷新最新数据/);
    await waitFor(() => expect(screen.getByRole("button", { name: /新增账户/ })).toBeEnabled());

    // 重开保存：携带 expectedVersion=2
    await openDetailAndRename(user, "招行储蓄卡（他人改）");
    await user.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(ctrl.patchCalls).toBe(2));
    expect(ctrl.patchBodies[1]).toMatchObject({ expectedVersion: 2 });
  });

  it("期初余额超界/非法时不发出创建请求", async () => {
    const ctrl: StubControl = { patchBodies: [], failGet: false, deferGet: null, patchCalls: 0 };
    const fetchMock = stubFetch(ctrl);

    const user = userEvent.setup();
    renderPage();
    await screen.findByText("招商银行储蓄卡");

    await user.click(screen.getByRole("button", { name: /新增账户/ }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByPlaceholderText("例如：招商银行储蓄卡"), "测试账户");
    await user.type(within(dialog).getByPlaceholderText("0.00"), "90071992547409.92");
    await user.click(within(dialog).getByRole("button", { name: "保存" }));

    expect(await within(dialog).findByText(/超出可安全表示的范围/)).toBeInTheDocument();
    const posts = fetchMock.mock.calls.filter(
      (c) => String(c[0]) === "/api/accounts" && (c[1] as RequestInit | undefined)?.method === "POST",
    );
    expect(posts).toHaveLength(0); // 绝不发送无效金额
  });
});
