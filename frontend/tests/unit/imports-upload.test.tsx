/**
 * 管理端导入上传弹窗（mock 模式，非端到端）：
 * 目标账户可选中后恢复「不指定，逐条补选」；关闭弹窗后表单状态（文件/来源/账户/错误）重置。
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterAll, afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import ImportsPage from "@/admin/pages/Imports";
import { setMockLatency } from "@/api/resources";
import type { ImportBatch, ImportCandidate } from "@/data/types";

const jsonResponse = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

describe("导入上传弹窗：目标账户哨兵与表单重置（mock 模式）", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_USE_MOCKS", "true"); // mock 仅由显式开关启用
    setMockLatency(0);
  });

  afterAll(() => {
    vi.unstubAllEnvs();
  });

  const renderPage = () =>
    render(
      <MemoryRouter>
        <ImportsPage />
      </MemoryRouter>,
    );

  it("选中账户后可恢复「不指定，逐条补选」；关闭再打开表单被重置", async () => {
    const user = userEvent.setup();
    renderPage();

    // 打开上传弹窗：目标账户默认为「不指定，逐条补选」
    await user.click(screen.getByRole("button", { name: /上传账单/ }));
    const dialog = await screen.findByRole("dialog");
    const accountCombo = within(dialog).getAllByRole("combobox")[1]; // 来源 / 目标账户
    expect(within(accountCombo).getByText("不指定，逐条补选")).toBeInTheDocument();

    // 选中一个可写账户
    await user.click(accountCombo);
    await user.click(await screen.findByRole("option", { name: "支付宝余额" }));
    expect(within(accountCombo).getByText("支付宝余额")).toBeInTheDocument();

    // 再恢复为「不指定，逐条补选」
    await user.click(accountCombo);
    await user.click(await screen.findByRole("option", { name: "不指定，逐条补选" }));
    expect(within(accountCombo).getByText("不指定，逐条补选")).toBeInTheDocument();

    // 只读账户在选项中被禁用（view 权限不可作为导入目标）
    await user.click(accountCombo);
    const viewOption = await screen.findByRole("option", { name: /房贷/ });
    expect(viewOption).toHaveAttribute("aria-disabled", "true");
    await user.keyboard("{Escape}");

    // 选个账户再关闭弹窗：重新打开后必须回到「不指定」
    await user.click(accountCombo);
    await user.click(await screen.findByRole("option", { name: "现金" }));
    await user.click(within(dialog).getByRole("button", { name: "取消" }));
    await user.click(screen.getByRole("button", { name: /上传账单/ }));
    const dialog2 = await screen.findByRole("dialog");
    const accountCombo2 = within(dialog2).getAllByRole("combobox")[1];
    expect(within(accountCombo2).getByText("不指定，逐条补选")).toBeInTheDocument();
  });
});

describe("导入列表重复数（isDuplicate 口径，fetch 打桩）", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_USE_MOCKS", ""); // 关闭 mock，走 http 分支
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  const cand = (id: string, overrides: Partial<ImportCandidate>): ImportCandidate => ({
    id,
    date: "2026-08-01T12:00:00",
    payee: id,
    direction: "expense",
    amountCents: 10_00,
    currency: "CNY",
    suggestedCategoryId: null,
    originalCategory: null,
    targetAccountId: "a-cmb",
    duplicateOfTxnId: null,
    status: "pending",
    validationStatus: "valid",
    confidence: "high",
    version: 1,
    ...overrides,
  });

  it("回归：duplicate_source_record（无 duplicateOfTxnId）也计入疑似重复数", async () => {
    const dupBatch: ImportBatch = {
      id: "b-dup",
      filename: "含重复.csv",
      source: "手动CSV",
      importedAt: "2026-08-02T21:14:00",
      status: "reviewing",
      ownerId: "m-dad",
      targetAccountId: "a-cmb",
      jobId: "job-dup",
      progress: 100,
      message: "",
      fileCount: 1,
      filesProcessed: 1,
      candidateCount: 3,
      needsOcrCount: 0,
      failedFileCount: 0,
      version: 1,
      files: [],
      candidates: [
        cand("cd-1", { validationStatus: "valid" }),
        cand("cd-2", { validationStatus: "exact_duplicate", duplicateOfTxnId: "t-1" }),
        // 来源记录重复：没有 duplicateOfTxnId，只看 duplicateOfTxnId 会漏算
        cand("cd-3", { validationStatus: "duplicate_source_record", duplicateOfTxnId: null }),
      ],
    };
    vi.stubGlobal(
      "fetch",
      // 每次调用返回新的 Response（Response body 只能消费一次）
      vi.fn().mockImplementation((input: unknown) =>
        Promise.resolve(
          String(input).startsWith("/api/accounts")
            ? jsonResponse(200, [])
            : jsonResponse(200, [dupBatch]),
        ),
      ),
    );

    render(
      <MemoryRouter>
        <ImportsPage />
      </MemoryRouter>,
    );

    const row = (await screen.findByText("含重复.csv")).closest("tr")!;
    expect(within(row).getByText("2")).toBeInTheDocument();
  });
});
