/**
 * 综合契约与组件测试（fetch 打桩，真实 HTTP 分支，非端到端）：
 * 1. client.ts 结构化 409 detail.message 解析（不丢失服务端阻断文案与 ID 列表）；
 * 2. ImportDetailPage：源文件诊断面板、OCR 数值置信度徽标、筛选器与候选行内修正（日期/商户/金额/账户）；
 * 3. Mobile ImportPage：手机端候选行内修正与 expectedVersion 乐观锁提交；
 * 4. Mobile AddPage：补记历史上海时间归一化为 UTC ISO 提交；
 * 5. DashboardPage & Mobile HomePage：空账本 EmptyState 与跨月历史账期一键切换提示。
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ApiError, apiFetch } from "@/api/client";
import ImportDetailPage from "@/admin/pages/ImportDetail";
import AdminDashboardPage from "@/admin/pages/Dashboard";
import MobileImportPage from "@/mobile/pages/Import";
import MobileAddPage from "@/mobile/pages/Add";
import MobileHomePage from "@/mobile/pages/Home";
import { AuthProvider } from "@/auth/AuthContext";
import type { Account, Category, ImportBatch, ImportCandidate, Member, Transaction } from "@/data/types";

const jsonResponse = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

const accounts: Account[] = [
  {
    id: "a-alipay",
    name: "支付宝余额",
    kind: "asset",
    scope: "personal",
    ownerId: "m-dad",
    permission: "manage",
    currency: "CNY",
    balanceCents: 3_000_00,
    icon: "wallet",
    version: 1,
  },
  {
    id: "a-cmb",
    name: "招商银行储蓄卡",
    kind: "asset",
    scope: "personal",
    ownerId: "m-dad",
    permission: "manage",
    currency: "CNY",
    balanceCents: 12_000_00,
    icon: "landmark",
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
    balanceCents: 210_000,
    icon: "dollar-sign",
    version: 1,
  },
];

const categories: Category[] = [
  { id: "c-food", name: "餐饮", kind: "expense", parentId: null, icon: "utensils", active: true, version: 1 },
  { id: "c-grocery", name: "买菜超市", kind: "expense", parentId: null, icon: "shopping-cart", active: true, version: 1 },
];

const member: Member = {
  id: "m-dad",
  username: "dad",
  name: "爸爸",
  identity: "dad",
  isAdmin: true,
  color: "bg-teal-600",
};

const baseCandidate: ImportCandidate = {
  id: "cd-ocr",
  date: "2026-08-01T04:00:00Z", // 上海 2026-08-01 12:00
  payee: "小票识别错字店",
  direction: "expense",
  amountCents: 45_50,
  currency: "CNY",
  suggestedCategoryId: "c-food",
  originalCategory: "餐饮美食",
  targetAccountId: "a-alipay",
  duplicateOfTxnId: null,
  status: "pending",
  validationStatus: "valid",
  confidence: "0.58",
  version: 3,
};

const normalCandidate: ImportCandidate = {
  ...baseCandidate,
  id: "cd-ok",
  payee: "正常超市",
  amountCents: 20_00,
  originalCategory: null,
  confidence: "high",
  version: 1,
};

const makeReviewBatch = (): ImportBatch => ({
  id: "b-edit",
  filename: "混合账单.csv",
  source: "wechat",
  importedAt: "2026-08-02T01:00:00Z",
  status: "reviewing",
  ownerId: "m-dad",
  targetAccountId: "a-alipay",
  jobId: "job-edit",
  progress: 100,
  message: "已生成 2 条候选，等待人工确认",
  fileCount: 2,
  filesProcessed: 2,
  candidateCount: 2,
  needsOcrCount: 1,
  failedFileCount: 0,
  version: 1,
  files: [
    {
      id: "f-1",
      filename: "微信流水.csv",
      mimeType: "text/csv",
      sizeBytes: 8192,
      sha256: "sha-1",
      encrypted: true,
      expiresAt: null,
      duplicateOfFileId: null,
      parseStatus: "parsed",
      parseMessage: "",
      recordCount: 5,
      candidateCount: 2,
      rejectedCount: 3,
    },
    {
      id: "f-2",
      filename: "餐饮小票.jpg",
      mimeType: "image/jpeg",
      sizeBytes: 102400,
      sha256: "sha-2",
      encrypted: true,
      expiresAt: null,
      duplicateOfFileId: null,
      parseStatus: "needs_ocr",
      parseMessage: "图片账单等待 OCR 工作器",
      recordCount: 0,
      candidateCount: 0,
      rejectedCount: 0,
    },
  ],
  candidates: [baseCandidate, normalCandidate],
});

describe("1. client.ts 结构化对象 detail 解析", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("409 返回 { detail: { message, transactionIds } } 时提取真实 message 且保留 details", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(409, {
          detail: {
            message: "部分账目后来被修改、分摊、退款或位于锁定账期，不能整批撤销",
            transactionIds: ["t-002", "t-004"],
          },
        }),
      ),
    );

    const err = await apiFetch("/import-batches/b-1/undo", { method: "POST" }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(409);
    expect((err as ApiError).message).toBe("部分账目后来被修改、分摊、退款或位于锁定账期，不能整批撤销");
    expect((err as ApiError).details).toEqual({
      message: "部分账目后来被修改、分摊、退款或位于锁定账期，不能整批撤销",
      transactionIds: ["t-002", "t-004"],
    });
  });
});

describe("2. 管理端 ImportDetailPage：源文件诊断、置信度筛选与行内字段修正", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_USE_MOCKS", "");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  it("展示逐文件异常拒绝行数、OCR 低置信度徽标，并支持行内修改日期/商户/金额/入账账户（携带 expectedVersion）", async () => {
    const patchPayloads: Record<string, unknown>[] = [];
    const fetchMock = vi.fn().mockImplementation((input: unknown, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url === "/api/import-batches/b-edit" && method === "GET") {
        return Promise.resolve(jsonResponse(200, makeReviewBatch()));
      }
      if (url === "/api/categories") return Promise.resolve(jsonResponse(200, categories));
      if (url === "/api/accounts") return Promise.resolve(jsonResponse(200, accounts));
      if (url === "/api/import-batches/b-edit/candidates/cd-ocr" && method === "PATCH") {
        const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
        patchPayloads.push(body);
        return Promise.resolve(
          jsonResponse(200, {
            ...baseCandidate,
            date: String(body.date ?? baseCandidate.date),
            payee: String(body.payee ?? baseCandidate.payee),
            amountCents: Number(body.amountCents ?? baseCandidate.amountCents),
            targetAccountId: String(body.targetAccountId ?? baseCandidate.targetAccountId),
            version: baseCandidate.version + 1,
          }),
        );
      }
      return Promise.resolve(jsonResponse(404, { detail: "not found" }));
    });
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/imports/b-edit"]}>
        <Routes>
          <Route path="/imports/:batchId" element={<ImportDetailPage />} />
        </Routes>
      </MemoryRouter>,
    );

    // 源文件诊断自动展开（因存在拒绝行与待 OCR 文件）
    expect(await screen.findByText(/源文件诊断（2 个文件）/)).toBeInTheDocument();
    expect(screen.getByText(/拒绝 3 行/)).toBeInTheDocument();
    expect(screen.getByText("图片账单等待 OCR 工作器")).toBeInTheDocument();

    // OCR 数值置信度与原始分类线索展示
    expect(screen.getByText("低置信度 58%")).toBeInTheDocument();
    expect(screen.getByText("原分类：餐饮美食")).toBeInTheDocument();
    expect(screen.getByText("微信支付")).toBeInTheDocument(); // wechat 归一化

    // 筛选「需关注」：只显示低置信度的 cd-ocr，隐藏正常高置信的 cd-ok
    await user.click(screen.getByRole("tab", { name: /需关注（1）/ }));
    expect(screen.getByText("小票识别错字店")).toBeInTheDocument();
    expect(screen.queryByText("正常超市")).not.toBeInTheDocument();

    // 打开候选行内修正面板
    await user.click(screen.getByRole("button", { name: "修改 小票识别错字店" }));

    // 非法金额先在前端拦截，不发 PATCH
    const amtInput = screen.getByLabelText("候选金额（小票识别错字店）");
    await user.clear(amtInput);
    await user.type(amtInput, "0");
    await user.click(screen.getByRole("button", { name: "保存修改" }));
    expect(await screen.findByText(/请输入大于 0 的有效金额/)).toBeInTheDocument();
    expect(patchPayloads).toHaveLength(0);

    // 填写修正后的上海时间、商户、金额与新目标账户（招商银行储蓄卡）并保存
    const dateInput = screen.getByLabelText("交易时间（小票识别错字店）");
    const payeeInput = screen.getByLabelText("商户名称（小票识别错字店）");
    await user.clear(dateInput);
    await user.type(dateInput, "2026-08-03T19:30");
    await user.clear(payeeInput);
    await user.type(payeeInput, "海底捞火锅");
    await user.clear(amtInput);
    await user.type(amtInput, "168.80");

    // 面板内的入账账户下拉切换为招商银行储蓄卡
    const editPanel = amtInput.closest("div.space-y-3") as HTMLElement;
    await user.click(within(editPanel).getByRole("combobox"));
    await user.click(await screen.findByRole("option", { name: /招商银行储蓄卡/ }));

    await user.click(screen.getByRole("button", { name: "保存修改" }));

    await waitFor(() => expect(patchPayloads).toHaveLength(1));
    expect(patchPayloads[0]).toEqual({
      date: "2026-08-03T11:30:00.000Z", // 上海 19:30 → UTC 11:30
      payee: "海底捞火锅",
      amountCents: 168_80,
      targetAccountId: "a-cmb",
      expectedVersion: 3,
    });

    // 保存后行内容更新为新商户、新日期、新金额与新账户
    expect(await screen.findByText("海底捞火锅")).toBeInTheDocument();
    expect(screen.getByText("8月3日")).toBeInTheDocument();
    expect(screen.getByText("-¥168.80")).toBeInTheDocument();
    expect(screen.getByText("招商银行储蓄卡")).toBeInTheDocument();
  });
});

describe("3. 手机端 ImportPage 候选行内修正 & AddPage 补记历史日期", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_USE_MOCKS", "");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  it("Mobile ImportPage：点击「修改」可更正商户与金额并携带 expectedVersion 提交", async () => {
    const patchPayloads: Record<string, unknown>[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((input: unknown, init?: RequestInit) => {
        const url = String(input);
        const method = init?.method ?? "GET";
        if (url === "/api/import-batches") return Promise.resolve(jsonResponse(200, [makeReviewBatch()]));
        if (url === "/api/accounts") return Promise.resolve(jsonResponse(200, accounts));
        if (url === "/api/categories") return Promise.resolve(jsonResponse(200, categories));
        if (url === "/api/import-batches/b-edit/candidates/cd-ocr" && method === "PATCH") {
          const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
          patchPayloads.push(body);
          return Promise.resolve(
            jsonResponse(200, {
              ...baseCandidate,
              payee: String(body.payee ?? baseCandidate.payee),
              amountCents: Number(body.amountCents ?? baseCandidate.amountCents),
              version: baseCandidate.version + 1,
            }),
          );
        }
        return Promise.resolve(jsonResponse(404, { detail: "not found" }));
      }),
    );

    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <MobileImportPage />
      </MemoryRouter>,
    );

    await screen.findByText("小票识别错字店");
    await user.click(screen.getByRole("button", { name: "修改 小票识别错字店" }));

    const payeeInput = screen.getByLabelText("商户名称（小票识别错字店）");
    const amtInput = screen.getByLabelText("候选金额（小票识别错字店）");
    await user.clear(payeeInput);
    await user.type(payeeInput, "瑞幸咖啡");
    await user.clear(amtInput);
    await user.type(amtInput, "19.90");
    await user.click(screen.getByRole("button", { name: "保存修改" }));

    await waitFor(() => expect(patchPayloads).toHaveLength(1));
    expect(patchPayloads[0]).toEqual({
      payee: "瑞幸咖啡",
      amountCents: 19_90,
      expectedVersion: 3,
    });
    expect(await screen.findByText("瑞幸咖啡")).toBeInTheDocument();
  });

  it("Mobile AddPage：选填历史交易时间（上海时间）按精确 UTC ISO 提交", async () => {
    const postPayloads: Record<string, unknown>[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((input: unknown, init?: RequestInit) => {
        const url = String(input);
        if (url === "/api/accounts") return Promise.resolve(jsonResponse(200, accounts));
        if (url === "/api/categories") return Promise.resolve(jsonResponse(200, categories));
        if (url === "/api/transactions" && init?.method === "POST") {
          postPayloads.push(JSON.parse(String(init.body)) as Record<string, unknown>);
          return Promise.resolve(jsonResponse(201, { id: "t-backdated" }));
        }
        return Promise.resolve(jsonResponse(404, { detail: "not found" }));
      }),
    );

    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <MobileAddPage />
      </MemoryRouter>,
    );

    await screen.findByRole("combobox");
    await user.type(screen.getByPlaceholderText("例如：盒马鲜生、工资"), "昨晚买菜");
    await user.type(screen.getByPlaceholderText("0.00"), "36.50");
    // 上海 2026-08-01 00:30 = UTC 2026-07-31T16:30:00.000Z
    await user.type(screen.getByLabelText("交易时间（上海时间）"), "2026-08-01T00:30");
    await user.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(postPayloads).toHaveLength(1));
    expect(postPayloads[0]).toMatchObject({
      type: "expense",
      payee: "昨晚买菜",
      amountCents: 36_50,
      date: "2026-07-31T16:30:00.000Z",
    });
  });
});

describe("4. DashboardPage & Mobile HomePage：空账本空态与跨月历史账期一键切换", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-11-20T12:00:00+08:00")); // 当前月 2026-11
    vi.stubEnv("VITE_USE_MOCKS", "");
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

  const stubOverviewFetch = (txns: Transaction[]) => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((input: unknown) => {
        const url = String(input);
        if (url === "/api/auth/me") return Promise.resolve(jsonResponse(200, member));
        if (url === "/api/transactions") return Promise.resolve(jsonResponse(200, txns));
        if (url === "/api/accounts") return Promise.resolve(jsonResponse(200, accounts));
        if (url === "/api/categories") return Promise.resolve(jsonResponse(200, categories));
        if (url === "/api/members") return Promise.resolve(jsonResponse(200, [member]));
        if (url === "/api/import-batches") return Promise.resolve(jsonResponse(200, []));
        if (url === "/api/splits") return Promise.resolve(jsonResponse(200, []));
        if (url === "/api/backups") return Promise.resolve(jsonResponse(200, []));
        return Promise.resolve(jsonResponse(404, { detail: "not found" }));
      }),
    );
  };

  it("空个人账本：Dashboard 与手机首页均展示引导式 EmptyState，不出现空白死区", async () => {
    stubOverviewFetch([]);
    const { unmount } = render(
      <MemoryRouter>
        <AdminDashboardPage />
      </MemoryRouter>,
    );
    expect(await screen.findByText("账本中还没有交易记录")).toBeInTheDocument();
    expect(screen.getByText("该账期暂无分类支出")).toBeInTheDocument();
    // 外币账户原币余额清晰独立展示
    expect(screen.getByText(/USD 2,100\.00/)).toBeInTheDocument();
    unmount();

    render(
      <MemoryRouter>
        <AuthProvider>
          <MobileHomePage />
        </AuthProvider>
      </MemoryRouter>,
    );
    expect(await screen.findByText("还没有记账记录")).toBeInTheDocument();
  });

  it("当月无交易但历史有账目（如 Demo 库 2026-09）：提示并支持一键切换至最近有账月份", async () => {
    const septTxn: Transaction = {
      id: "t-sept",
      date: "2026-09-10T04:00:00Z",
      type: "expense",
      payee: "九月超市采购",
      note: "",
      categoryId: "c-grocery",
      currency: "CNY",
      amountCents: 288_00,
      baseAmountCents: 288_00,
      baseCurrency: "CNY",
      lines: [],
      source: "manual",
      createdBy: "m-dad",
      version: 1,
    };
    stubOverviewFetch([septTxn]);
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });

    render(
      <MemoryRouter>
        <AdminDashboardPage />
      </MemoryRouter>,
    );

    const jumpBtn = await screen.findByRole("button", { name: "切换至 2026年9月" });
    await user.click(jumpBtn);

    expect(await screen.findByText("所选月净支出")).toBeInTheDocument();
    expect(screen.getAllByText("2026 年 9 月")).toHaveLength(2);
    expect(screen.getAllByText("-¥288.00").length).toBeGreaterThan(0);
  });
});
