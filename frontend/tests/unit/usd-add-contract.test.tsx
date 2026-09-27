/**
 * 手工记账提交契约（http fetch 打桩拦截 POST /api/transactions，非端到端）：
 * - Mobile Add：外币账户金额单位显示币种代码；「折合人民币金额（CNY，必填）」
 *   缺失/非法时点保存不发请求并出字段错误；外币 payload 含 baseAmountCents、
 *   currency=账户币种、无 fxRate 键；CNY payload 无 baseAmountCents；
 *   转账币种不匹配「转出与转入账户币种必须相同」；USD→USD 转账同样携带折合金额。
 * - Admin Txns 记一笔：金额 label 随账户币种「金额（元）/金额（USD）」；
 *   缺折合金额报「请填写折合人民币金额」且不发请求；成功 payload 同口径。
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import MobileAddPage from "@/mobile/pages/Add";
import AdminTxnsPage from "@/admin/pages/Txns";
import { accounts as mockAccounts, categories } from "@/data/mock";
import type { Account } from "@/data/types";

const jsonResponse = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

// 第二个 USD 账户：USD→USD 转账需要同币种的两个不同账户
const usd2: Account = {
  id: "a-usd-2",
  name: "美元储蓄",
  kind: "asset",
  scope: "personal",
  ownerId: "m-dad",
  permission: "manage",
  currency: "USD",
  balanceCents: 50_000,
  icon: "dollar-sign",
  version: 1,
};

const accounts = [...mockAccounts, usd2];

const stubFetch = () => {
  const fetchMock = vi.fn().mockImplementation((input: unknown, init?: RequestInit) => {
    const url = String(input);
    if (url === "/api/accounts") return Promise.resolve(jsonResponse(200, accounts));
    if (url === "/api/categories") return Promise.resolve(jsonResponse(200, categories));
    if (url === "/api/transactions" && init?.method === "POST") {
      return Promise.resolve(jsonResponse(201, { id: "t-new" }));
    }
    if (url === "/api/transactions") return Promise.resolve(jsonResponse(200, []));
    if (url === "/api/members") return Promise.resolve(jsonResponse(200, []));
    if (url === "/api/import-batches") return Promise.resolve(jsonResponse(200, []));
    if (url === "/api/splits") return Promise.resolve(jsonResponse(200, []));
    return Promise.resolve(jsonResponse(404, { detail: "not found" }));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
};

/** 取全部 POST /api/transactions 调用的请求体（JSON） */
const postBodies = (fetchMock: ReturnType<typeof vi.fn>) =>
  (fetchMock.mock.calls as [unknown, RequestInit?][])
    .filter(([url, init]) => String(url) === "/api/transactions" && init?.method === "POST")
    .map(([, init]) => JSON.parse(String(init!.body)) as Record<string, unknown>);

const renderMobileAdd = () =>
  render(
    <MemoryRouter>
      <MobileAddPage />
    </MemoryRouter>,
  );

/** Mobile Add：等账户/分类加载完（金额输入出现），并选择指定账户 */
async function mobileSelectAccount(user: ReturnType<typeof userEvent.setup>, name: string) {
  const combo = await screen.findByRole("combobox");
  await user.click(combo);
  await user.click(await screen.findByRole("option", { name: new RegExp(name) }));
}

describe("Mobile Add：USD 手工支出/转账提交契约", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_USE_MOCKS", ""); // 关闭 mock，走 http 分支
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  it("USD 账户支出：金额单位显示 USD，payload 含 baseAmountCents、currency=USD、无 fxRate 键", async () => {
    const fetchMock = stubFetch();
    const user = userEvent.setup();
    renderMobileAdd();

    await mobileSelectAccount(user, "美元账户");
    // 外币账户：金额单位为币种代码，且出现折合人民币必填字段
    expect(screen.getByText("USD")).toBeInTheDocument();
    expect(screen.getByText("折合人民币金额（CNY，必填）")).toBeInTheDocument();

    await user.type(screen.getByPlaceholderText("例如：盒马鲜生、工资"), "Apple Store");
    await user.type(screen.getAllByPlaceholderText("0.00")[0], "29.00");
    await user.type(screen.getByLabelText("折合人民币金额（CNY）"), "207.64");
    await user.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(postBodies(fetchMock)).toHaveLength(1));
    const body = postBodies(fetchMock)[0];
    expect(body).toMatchObject({
      type: "expense",
      payee: "Apple Store",
      accountId: "a-usd",
      amountCents: 29_00, // 原币分
      currency: "USD",
      baseAmountCents: 207_64, // 折合 CNY 分
    });
    expect(body).not.toHaveProperty("fxRate");
  });

  it("USD 账户缺折合金额：报「请输入折合人民币金额」，不发出 POST", async () => {
    const fetchMock = stubFetch();
    const user = userEvent.setup();
    renderMobileAdd();

    await mobileSelectAccount(user, "美元账户");
    await user.type(screen.getByPlaceholderText("例如：盒马鲜生、工资"), "Apple Store");
    await user.type(screen.getAllByPlaceholderText("0.00")[0], "29.00");
    await user.click(screen.getByRole("button", { name: "保存" }));

    expect(await screen.findByText("请输入折合人民币金额")).toBeInTheDocument();
    expect(postBodies(fetchMock)).toHaveLength(0);
  });

  it("USD 账户折合金额为 0：报「折合人民币金额必须大于 0」，不发出 POST", async () => {
    const fetchMock = stubFetch();
    const user = userEvent.setup();
    renderMobileAdd();

    await mobileSelectAccount(user, "美元账户");
    await user.type(screen.getByPlaceholderText("例如：盒马鲜生、工资"), "Apple Store");
    await user.type(screen.getAllByPlaceholderText("0.00")[0], "29.00");
    // 绕过输入过滤（onChange 正则会拦非法字符），直接火事件填非法值不现实——填 0 走 parsePositiveCents
    await user.type(screen.getByLabelText("折合人民币金额（CNY）"), "0");
    await user.click(screen.getByRole("button", { name: "保存" }));

    expect(await screen.findByText("折合人民币金额必须大于 0")).toBeInTheDocument();
    expect(postBodies(fetchMock)).toHaveLength(0);
  });

  it("USD→USD 转账：payload 带 toAccountId 与 baseAmountCents；币种不匹配被前端拦截", async () => {
    const fetchMock = stubFetch();
    const user = userEvent.setup();
    renderMobileAdd();

    await user.click(await screen.findByRole("button", { name: "转账" }));
    // 从账户 → 美元账户
    const combos = screen.getAllByRole("combobox");
    await user.click(combos[0]);
    await user.click(await screen.findByRole("option", { name: /美元账户/ }));
    // 到账户 → 美元储蓄（同为 USD）
    await user.click(screen.getAllByRole("combobox")[1]);
    await user.click(await screen.findByRole("option", { name: /美元储蓄/ }));

    await user.type(screen.getByPlaceholderText("例如：盒马鲜生、工资"), "美元互转");
    await user.type(screen.getAllByPlaceholderText("0.00")[0], "10.00");
    await user.type(screen.getByLabelText("折合人民币金额（CNY）"), "71.60");
    await user.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(postBodies(fetchMock)).toHaveLength(1));
    const body = postBodies(fetchMock)[0];
    expect(body).toMatchObject({
      type: "transfer",
      accountId: "a-usd",
      toAccountId: "a-usd-2",
      amountCents: 10_00,
      currency: "USD",
      baseAmountCents: 71_60,
      categoryId: null,
    });
    expect(body).not.toHaveProperty("fxRate");
  });

  it("转账 USD→CNY：报「转出与转入账户币种必须相同」，不发出 POST", async () => {
    const fetchMock = stubFetch();
    const user = userEvent.setup();
    renderMobileAdd();

    await user.click(await screen.findByRole("button", { name: "转账" }));
    await user.click(screen.getAllByRole("combobox")[0]);
    await user.click(await screen.findByRole("option", { name: /美元账户/ }));
    // 到账户默认是支付宝（CNY），与 USD 转出币种不匹配
    await user.type(screen.getByPlaceholderText("例如：盒马鲜生、工资"), "美元转人民币");
    await user.type(screen.getAllByPlaceholderText("0.00")[0], "10.00");
    await user.type(screen.getByLabelText("折合人民币金额（CNY）"), "71.60");
    await user.click(screen.getByRole("button", { name: "保存" }));

    expect(await screen.findByText("转出与转入账户币种必须相同")).toBeInTheDocument();
    expect(postBodies(fetchMock)).toHaveLength(0);
  });

  it("CNY 账户支出：payload 无 baseAmountCents，currency=CNY", async () => {
    const fetchMock = stubFetch();
    const user = userEvent.setup();
    renderMobileAdd();

    // 默认预选首个可写账户（招商银行储蓄卡，CNY）：不出现折合字段
    await screen.findByRole("combobox");
    expect(screen.queryByText("折合人民币金额（CNY，必填）")).toBeNull();

    await user.type(screen.getByPlaceholderText("例如：盒马鲜生、工资"), "盒马鲜生");
    await user.type(screen.getByPlaceholderText("0.00"), "12.50");
    await user.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(postBodies(fetchMock)).toHaveLength(1));
    const body = postBodies(fetchMock)[0];
    expect(body).toMatchObject({
      type: "expense",
      accountId: "a-cmb",
      amountCents: 12_50,
      currency: "CNY",
    });
    expect(body).not.toHaveProperty("baseAmountCents");
    expect(body).not.toHaveProperty("fxRate");
  });
});

describe("Admin Txns 记一笔：USD 提交契约", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_USE_MOCKS", "");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  const openDialog = async (user: ReturnType<typeof userEvent.setup>) => {
    render(
      <MemoryRouter>
        <AdminTxnsPage />
      </MemoryRouter>,
    );
    await user.click(await screen.findByRole("button", { name: /记一笔/ }));
    return screen.findByRole("dialog");
  };

  it("金额 label 随账户币种：未选账户「金额（元）」，选 USD 账户「金额（USD）」并出现折合字段", async () => {
    stubFetch();
    const user = userEvent.setup();
    const dialog = await openDialog(user);

    expect(within(dialog).getByText("金额（元）")).toBeInTheDocument();
    // 账户下拉（类型 / 分类 / 账户 → 第 3 个 combobox）
    const combos = within(dialog).getAllByRole("combobox");
    await user.click(combos[2]);
    await user.click(await screen.findByRole("option", { name: /美元账户/ }));

    expect(within(dialog).getByText("金额（USD）")).toBeInTheDocument();
    expect(within(dialog).getByText("折合人民币金额（CNY）")).toBeInTheDocument();
  });

  it("USD 账户缺折合金额：报「请填写折合人民币金额」，不发出 POST；补齐后 payload 同口径", async () => {
    const fetchMock = stubFetch();
    const user = userEvent.setup();
    const dialog = await openDialog(user);

    const combos = within(dialog).getAllByRole("combobox");
    await user.click(combos[2]);
    await user.click(await screen.findByRole("option", { name: /美元账户/ }));

    await user.type(within(dialog).getByPlaceholderText("例如：永辉超市"), "Apple Store");
    await user.type(within(dialog).getByLabelText(/金额（USD）/), "29.00");
    await user.click(within(dialog).getByRole("button", { name: "保存" }));

    expect(await within(dialog).findByText("请填写折合人民币金额")).toBeInTheDocument();
    expect(postBodies(fetchMock)).toHaveLength(0);

    // 错误文案渲染在同一 label 内，label 精确匹配会失效；折合输入框是第二个 0.00 占位
    await user.type(within(dialog).getAllByPlaceholderText("0.00")[1], "207.64");
    await user.click(within(dialog).getByRole("button", { name: "保存" }));

    await waitFor(() => expect(postBodies(fetchMock)).toHaveLength(1));
    const body = postBodies(fetchMock)[0];
    expect(body).toMatchObject({
      type: "expense",
      payee: "Apple Store",
      accountId: "a-usd",
      amountCents: 29_00,
      currency: "USD",
      baseAmountCents: 207_64,
    });
    expect(body).not.toHaveProperty("fxRate");
  });
});
