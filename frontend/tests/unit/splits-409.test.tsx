/**
 * 分摊 409 竞态（http fetch 打桩，契约级，非端到端）：
 * 任何 split 写操作遇 409：立即关闭退回/改金额弹窗、清本地覆盖、进写锁；
 * await listSplits 最新结果，仅成功应用后解锁；GET 失败维持锁定并提供重试。
 * 覆盖管理端 return / edit 与手机端 return；inline accept 服从同一锁。
 */
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import AdminSplitsPage from "@/admin/pages/Splits";
import MobileSplitsPage from "@/mobile/pages/Splits";
import { AuthProvider } from "@/auth/AuthContext";
import type { Member, Split, SplitShare } from "@/data/types";

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

const share = (memberId: string, status: SplitShare["status"]): SplitShare => ({
  memberId,
  shareCents: 150_00,
  status,
  note: status === "returned" ? "我不参与" : null,
});

const split = (
  id: string, title: string, createdBy: string, version: number, shares: SplitShare[],
): Split => ({
  id,
  txnId: "t-1",
  title,
  transactionDate: "2026-08-01T12:00:00",
  categoryId: "c-food",
  totalCents: 300_00,
  currency: "CNY",
  createdBy,
  createdAt: "2026-08-01T12:30:00",
  status: "open",
  version,
  shares,
});

/** s-1：妈妈发起、我的份额待处理（测接受/退回）；s-2：我发起、妈妈份额已退回（测修改份额） */
const splitsV = (version: number): Split[] => [
  split("s-1", "聚餐AA", "m-mom", version, [share("m-dad", "pending"), share("m-mom", "accepted")]),
  split("s-2", "家庭采购", "m-dad", version, [share("m-mom", "returned")]),
];

interface StubControl {
  writeCalls: number;
  writeBodies: unknown[];
  failGet: boolean;
  deferGet: { promise: Promise<Split[]>; resolve: (v: Split[]) => void } | null;
  writeUrl: string; // 本轮要 409 的写路径
  writeMethod: string;
  /** 非 deferred/fail 时 GET /splits 刷新返回的列表（默认 splitsV(2)） */
  getResult?: Split[];
  /** 首次 GET /splits 返回的列表（默认 splitsV(1)） */
  initialList?: Split[];
  /** true 时首个写请求也直接成功（不制造 409） */
  firstWriteOk?: boolean;
}

const expenseTxn = {
  id: "t-1",
  date: "2026-08-01T12:00:00",
  type: "expense",
  payee: "海底捞",
  note: "",
  categoryId: "c-food",
  currency: "CNY",
  amountCents: 300_00,
  baseAmountCents: 300_00,
  baseCurrency: "CNY",
  lines: [],
  source: "manual",
  createdBy: "m-dad",
  version: 1,
};

function stubFetch(ctrl: StubControl) {
  let loaded = false;
  const fetchMock = vi.fn().mockImplementation((input: unknown, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    if (url === "/api/auth/me") return Promise.resolve(jsonResponse(200, dad));
    if (url === "/api/members") return Promise.resolve(jsonResponse(200, members));
    if (url === "/api/transactions") return Promise.resolve(jsonResponse(200, [expenseTxn]));
    if (url === "/api/splits" && method === "GET") {
      if (!loaded) {
        loaded = true;
        return Promise.resolve(jsonResponse(200, ctrl.initialList ?? splitsV(1)));
      }
      if (ctrl.deferGet) return ctrl.deferGet.promise.then((v) => jsonResponse(200, v));
      if (ctrl.failGet) return Promise.resolve(jsonResponse(500, { detail: "服务器内部错误" }));
      return Promise.resolve(jsonResponse(200, ctrl.getResult ?? splitsV(2)));
    }
    if (url === ctrl.writeUrl && method === ctrl.writeMethod) {
      ctrl.writeCalls += 1;
      ctrl.writeBodies.push(JSON.parse(String(init?.body)));
      // 成功响应必须返回被写的那一个 split（s-2 的写返回 s-2；s-1 退回后我的份额为 returned），不得串号
      const okSplit = url.includes("/s-2/")
        ? splitsV(3)[1]
        : split("s-1", "聚餐AA", "m-mom", 3, [share("m-dad", "returned"), share("m-mom", "accepted")]);
      return Promise.resolve(
        !ctrl.firstWriteOk && ctrl.writeCalls === 1
          ? jsonResponse(409, { detail: "版本冲突，请刷新后重试" })
          : jsonResponse(200, okSplit),
      );
    }
    return Promise.resolve(jsonResponse(404, { detail: "not found" }));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const renderAdmin = () =>
  render(
    <MemoryRouter>
      <AuthProvider>
        <AdminSplitsPage />
      </AuthProvider>
    </MemoryRouter>,
  );

const renderMobile = () =>
  render(
    <MemoryRouter>
      <AuthProvider>
        <MobileSplitsPage />
      </AuthProvider>
    </MemoryRouter>,
  );

describe("管理端分摊 409 写锁", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_USE_MOCKS", "");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  it("退回：v1→409→deferred GET v2，旧弹窗不可重提；重开后 expectedVersion=2；accept 同锁", async () => {
    let resolveGet!: (v: Split[]) => void;
    const ctrl: StubControl = {
      writeCalls: 0,
      writeBodies: [],
      failGet: false,
      deferGet: { promise: new Promise((r) => (resolveGet = r)), resolve: null as never },
      writeUrl: "/api/splits/s-1/respond",
      writeMethod: "POST",
    };
    ctrl.deferGet!.resolve = resolveGet;
    stubFetch(ctrl);

    const user = userEvent.setup();
    renderAdmin();

    // 打开退回弹窗并提交 → respond 409
    await user.click(await screen.findByRole("button", { name: "退回" }));
    await user.type(await screen.findByPlaceholderText("请填写理由，便于发起人调整"), "我不参与");
    await user.click(screen.getByRole("button", { name: "确认退回" }));

    // 409：弹窗立即关闭、理由清空；GET 在途期间写锁生效（accept 同锁，在途时显示「提交中…」）
    await waitFor(() => expect(ctrl.writeCalls).toBe(1));
    await waitFor(() => expect(screen.queryByText("退回分摊")).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: /接受|提交中/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: "退回" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "修改份额" })).toBeDisabled();

    // 刷新 v2 成功：解锁并提示重新审阅
    await act(async () => {
      resolveGet(splitsV(2));
    });
    await screen.findByText(/已为你刷新列表，请重新审阅后再操作/);
    await waitFor(() => expect(screen.getByRole("button", { name: "退回" })).toBeEnabled());

    // 重新打开退回并提交：携带刷新后的 expectedVersion=2
    await user.click(screen.getByRole("button", { name: "退回" }));
    await user.type(await screen.findByPlaceholderText("请填写理由，便于发起人调整"), "我不参与");
    await user.click(screen.getByRole("button", { name: "确认退回" }));
    await waitFor(() => expect(ctrl.writeCalls).toBe(2));
    expect(ctrl.writeBodies[0]).toMatchObject({ expectedVersion: 1 });
    expect(ctrl.writeBodies[1]).toMatchObject({ expectedVersion: 2 });
    // 成功响应确实落入 s-1：版本徽章前进到第 3 版
    await screen.findByText(/第 3 版/);
  });

  it("修改份额：v1→409→刷新 v2 后重开携带 expectedVersion=2", async () => {
    const ctrl: StubControl = {
      writeCalls: 0,
      writeBodies: [],
      failGet: false,
      deferGet: null,
      writeUrl: "/api/splits/s-2/shares/m-mom",
      writeMethod: "PATCH",
    };
    stubFetch(ctrl);

    const user = userEvent.setup();
    renderAdmin();

    await user.click(await screen.findByRole("button", { name: "修改份额" }));
    await screen.findByRole("button", { name: "保存修改" });
    await user.click(screen.getByRole("button", { name: "保存修改" }));

    // 409：弹窗关闭、列表刷新成功 → 解锁
    await screen.findByText(/已为你刷新列表，请重新审阅后再操作/);
    expect(screen.queryByRole("button", { name: "保存修改" })).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "修改份额" })).toBeEnabled());

    await user.click(screen.getByRole("button", { name: "修改份额" }));
    await user.click(await screen.findByRole("button", { name: "保存修改" }));
    await waitFor(() => expect(ctrl.writeCalls).toBe(2));
    expect(ctrl.writeBodies[0]).toMatchObject({ expectedVersion: 1 });
    expect(ctrl.writeBodies[1]).toMatchObject({ expectedVersion: 2 });
    // 成功响应确实落入 s-2（返回的是 s-2 而非 s-1）：版本徽章前进到第 3 版且不串行
    await screen.findByText(/第 3 版/);
    expect(screen.getAllByText(/第 \d+ 版/)).toHaveLength(2); // s-1 第 2 版 + s-2 第 3 版，无重复 key 串号
  });

  it("刷新 GET 失败：维持写锁并诚实提示；重试成功才解锁", async () => {
    const ctrl: StubControl = {
      writeCalls: 0,
      writeBodies: [],
      failGet: true,
      deferGet: null,
      writeUrl: "/api/splits/s-1/respond",
      writeMethod: "POST",
    };
    stubFetch(ctrl);

    const user = userEvent.setup();
    renderAdmin();

    await user.click(await screen.findByRole("button", { name: "退回" }));
    await user.type(await screen.findByPlaceholderText("请填写理由，便于发起人调整"), "我不参与");
    await user.click(screen.getByRole("button", { name: "确认退回" }));

    await screen.findByText(/刷新最新列表失败/);
    expect(screen.queryByText(/已为你刷新列表/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "接受" })).toBeDisabled();
    expect(ctrl.writeCalls).toBe(1);

    ctrl.failGet = false;
    await user.click(screen.getByRole("button", { name: "重试刷新" }));
    await screen.findByText(/已为你刷新列表，请重新审阅后再操作/);
    await waitFor(() => expect(screen.getByRole("button", { name: "接受" })).toBeEnabled());
  });

  it("新建 POST /splits 409：旧创建框不可重复提交、全部写锁；刷新后显示后端已有 split", async () => {
    // 监视 controlled/uncontrolled 警告（Select 受控化回归）
    const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    let resolveGet!: (v: Split[]) => void;
    const ctrl: StubControl = {
      writeCalls: 0,
      writeBodies: [],
      failGet: false,
      deferGet: { promise: new Promise((r) => (resolveGet = r)), resolve: null as never },
      writeUrl: "/api/splits",
      writeMethod: "POST",
    };
    ctrl.deferGet!.resolve = resolveGet;
    stubFetch(ctrl);

    const user = userEvent.setup();
    renderAdmin();

    // 打开新建框：选交易（成员默认全选）→ 发起 → POST 409
    await user.click(await screen.findByRole("button", { name: "发起新分摊" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("combobox"));
    await user.click(await screen.findByRole("option", { name: /海底捞/ }));
    await user.click(within(dialog).getByRole("button", { name: "发起分摊" }));

    // 409：新建框立即关闭（旧选择被丢弃）；GET 在途期间写锁生效
    await waitFor(() => expect(ctrl.writeCalls).toBe(1));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: "发起新分摊" })).toBeDisabled();
    expect(screen.getByRole("button", { name: /接受|提交中/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: "退回" })).toBeDisabled();

    // 刷新成功：列表显示后端已有的 split，写锁解除
    await act(async () => {
      resolveGet([
        ...splitsV(2),
        split("s-3", "后端已有分摊", "m-mom", 1, [share("m-dad", "pending"), share("m-mom", "accepted")]),
      ]);
    });
    await screen.findByText(/已为你刷新列表，请重新审阅后再操作/);
    await screen.findByText("后端已有分摊");
    await waitFor(() => expect(screen.getByRole("button", { name: "发起新分摊" })).toBeEnabled());

    // 全程不得出现 Radix Select controlled/uncontrolled 切换警告
    const controlledWarnings = consoleSpy.mock.calls.filter((args) =>
      args.some((a) => typeof a === "string" && /controlled|uncontrolled/i.test(a)),
    );
    expect(controlledWarnings).toHaveLength(0);
    consoleSpy.mockRestore();
  });

  it("修改份额预填精确：.90 份额打开后不改直接保存仍发原 cents（不经浮点）", async () => {
    const bigShare = 9007199254740990; // (x/100).toFixed(2) 会错成 ...409.91
    const ctrl: StubControl = {
      writeCalls: 0,
      writeBodies: [],
      failGet: false,
      deferGet: null,
      writeUrl: "/api/splits/s-2/shares/m-mom",
      writeMethod: "PATCH",
      firstWriteOk: true,
      initialList: [
        split("s-2", "家庭采购", "m-dad", 1, [
          { memberId: "m-mom", shareCents: bigShare, status: "returned", note: "太贵了" },
        ]),
      ],
    };
    stubFetch(ctrl);

    const user = userEvent.setup();
    renderAdmin();

    await user.click(await screen.findByRole("button", { name: "修改份额" }));
    // 预填必须是精确的 .90，而不是浮点误差产生的 .91
    const input = await screen.findByDisplayValue("90071992547409.90");
    expect(input).toBeInTheDocument();
    // 不改动直接保存：发出的 shareCents 与原值精确一致
    await user.click(screen.getByRole("button", { name: "保存修改" }));
    await waitFor(() => expect(ctrl.writeCalls).toBe(1));
    expect(ctrl.writeBodies[0]).toMatchObject({ shareCents: 9007199254740990, expectedVersion: 1 });
  });

  it("修改份额金额无效/超界时不发出 PATCH", async () => {
    const ctrl: StubControl = {
      writeCalls: 0,
      writeBodies: [],
      failGet: false,
      deferGet: null,
      writeUrl: "/api/splits/s-2/shares/m-mom",
      writeMethod: "PATCH",
    };
    const fetchMock = stubFetch(ctrl);

    const user = userEvent.setup();
    renderAdmin();

    await user.click(await screen.findByRole("button", { name: "修改份额" }));
    // 份额金额标签按分摊币种动态展示（该用例 mock 分摊为 CNY）
    await screen.findByText("新的应摊金额（CNY）");
    const input = screen.getByDisplayValue("150.00");
    await user.clear(input);
    await user.type(input, "1.005"); // 三位小数：非法
    const saveBtn = screen.getByRole("button", { name: "保存修改" });
    expect(saveBtn).toBeDisabled();
    await user.click(saveBtn);
    expect(ctrl.writeCalls).toBe(0);
    const patches = fetchMock.mock.calls.filter((c) => (c[1] as RequestInit | undefined)?.method === "PATCH");
    expect(patches).toHaveLength(0);
  });
});

describe("手机端分摊 409 写锁", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_USE_MOCKS", "");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  it("退回：v1→409→deferred GET v2，旧弹窗（returnTarget 快照）不可重提；重开后 expectedVersion=2", async () => {
    let resolveGet!: (v: Split[]) => void;
    const ctrl: StubControl = {
      writeCalls: 0,
      writeBodies: [],
      failGet: false,
      deferGet: { promise: new Promise((r) => (resolveGet = r)), resolve: null as never },
      writeUrl: "/api/splits/s-1/respond",
      writeMethod: "POST",
    };
    ctrl.deferGet!.resolve = resolveGet;
    stubFetch(ctrl);

    const user = userEvent.setup();
    renderMobile();

    await user.click(await screen.findByRole("button", { name: "退回" }));
    await user.type(await screen.findByPlaceholderText("例如：这笔费用我不参与"), "我不参与");
    await user.click(screen.getByRole("button", { name: "确认退回" }));

    // 409：退回弹窗立即关闭（旧 Split 快照被丢弃）；GET 在途期间写锁生效（在途时接受按钮显示「提交中…」）
    await waitFor(() => expect(ctrl.writeCalls).toBe(1));
    await waitFor(() => expect(screen.queryByText("退回分摊")).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: /接受|提交中/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: "退回" })).toBeDisabled();

    await act(async () => {
      resolveGet(splitsV(2));
    });
    await screen.findByText(/列表已刷新，请重新审阅后再操作/);
    await waitFor(() => expect(screen.getByRole("button", { name: "退回" })).toBeEnabled());

    await user.click(screen.getByRole("button", { name: "退回" }));
    await user.type(await screen.findByPlaceholderText("例如：这笔费用我不参与"), "我不参与");
    await user.click(screen.getByRole("button", { name: "确认退回" }));
    await waitFor(() => expect(ctrl.writeCalls).toBe(2));
    expect(ctrl.writeBodies[0]).toMatchObject({ expectedVersion: 1 });
    expect(ctrl.writeBodies[1]).toMatchObject({ expectedVersion: 2 });
    // 成功响应确实落入 s-1：我的份额已退回，待办列表清空
    await screen.findByText("没有待处理的分摊");
  });

  it("刷新 GET 失败：维持写锁并诚实提示；重试成功才解锁", async () => {
    const ctrl: StubControl = {
      writeCalls: 0,
      writeBodies: [],
      failGet: true,
      deferGet: null,
      writeUrl: "/api/splits/s-1/respond",
      writeMethod: "POST",
    };
    stubFetch(ctrl);

    const user = userEvent.setup();
    renderMobile();

    await user.click(await screen.findByRole("button", { name: "退回" }));
    await user.type(await screen.findByPlaceholderText("例如：这笔费用我不参与"), "我不参与");
    await user.click(screen.getByRole("button", { name: "确认退回" }));

    await screen.findByText(/刷新失败/);
    expect(screen.queryByText(/列表已刷新/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "接受" })).toBeDisabled();
    expect(ctrl.writeCalls).toBe(1);

    ctrl.failGet = false;
    await user.click(screen.getByRole("button", { name: "重试刷新" }));
    await screen.findByText(/列表已刷新，请重新审阅后再操作/);
    await waitFor(() => expect(screen.getByRole("button", { name: "接受" })).toBeEnabled());
  });
});
