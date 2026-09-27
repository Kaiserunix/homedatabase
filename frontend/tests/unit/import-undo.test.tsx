/**
 * 导入整批撤销入口差异（Admin Imports，http fetch 打桩，非端到端）：
 * canUndoBatch = (status posted 或 needs_ocr) 且候选中有 confirmed。
 * mock 无 needs_ocr+confirmed 混合批次 fixture，这里自造：
 * (a) needs_ocr 且含 1 个 confirmed 候选 → 显示「整批撤销」，状态徽标保留
 *     警告态「需要人工识别」（可撤销不代表导入成功）；
 * (b) 纯 needs_ocr（无 confirmed 候选）→ 不显示撤销按钮。
 */
import { render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import AdminImportsPage from "@/admin/pages/Imports";
import type { ImportBatch, ImportCandidate } from "@/data/types";

const jsonResponse = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

const candidate = (id: string, status: ImportCandidate["status"]): ImportCandidate => ({
  id,
  date: "2026-07-30T04:00:00Z",
  payee: "商户",
  amountCents: 10_00,
  suggestedCategoryId: null,
  direction: "expense",
  currency: "CNY",
  status,
  duplicateOfTxnId: null,
  originalCategory: null,
  targetAccountId: "a-alipay",
  validationStatus: "valid",
  confidence: "high",
  version: 1,
});

const batch = (
  id: string,
  filename: string,
  status: ImportBatch["status"],
  candidates: ImportCandidate[],
): ImportBatch => ({
  id,
  filename,
  source: "微信支付",
  importedAt: "2026-08-01T02:00:00Z",
  status,
  ownerId: "m-dad",
  targetAccountId: "a-alipay",
  jobId: `job-${id}`,
  progress: 100,
  message: status === "needs_ocr" ? "部分文件需要人工识别" : "",
  fileCount: 1,
  filesProcessed: 1,
  candidateCount: candidates.length,
  needsOcrCount: status === "needs_ocr" ? 1 : 0,
  failedFileCount: 0,
  version: 1,
  files: [],
  candidates,
});

describe("Admin Imports：整批撤销入口按候选状态判定（http 打桩）", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_USE_MOCKS", ""); // 关闭 mock，走 http 分支
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  const renderImports = (batches: ImportBatch[]) => {
    const fetchMock = vi.fn().mockImplementation((input: unknown) => {
      const url = String(input);
      if (url === "/api/import-batches") return Promise.resolve(jsonResponse(200, batches));
      if (url === "/api/accounts") return Promise.resolve(jsonResponse(200, []));
      return Promise.resolve(jsonResponse(404, { detail: "not found" }));
    });
    vi.stubGlobal("fetch", fetchMock);
    render(
      <MemoryRouter>
        <AdminImportsPage />
      </MemoryRouter>,
    );
    return fetchMock;
  };

  it("needs_ocr + 含 confirmed 候选：显示「整批撤销」且保留警告态「需要人工识别」", async () => {
    renderImports([
      batch("b-mixed", "混合批次.csv", "needs_ocr", [
        candidate("cd-1", "confirmed"),
        candidate("cd-2", "pending"),
      ]),
    ]);

    const row = (await screen.findByText("混合批次.csv")).closest("tr")!;
    // 可撤销：有 confirmed 候选
    expect(within(row).getByRole("button", { name: "整批撤销" })).toBeInTheDocument();
    // 但状态绝不因此变成成功态：仍是警告态 needs_ocr
    const badge = within(row).getByText("需要人工识别");
    expect(badge.className).toContain("text-warn");
  });

  it("纯 needs_ocr（无 confirmed 候选）：不显示撤销按钮，仍是警告态", async () => {
    renderImports([
      batch("b-pure", "纯识别批次.csv", "needs_ocr", [candidate("cd-1", "pending")]),
    ]);

    const row = (await screen.findByText("纯识别批次.csv")).closest("tr")!;
    expect(within(row).queryByRole("button", { name: "整批撤销" })).toBeNull();
    const badge = within(row).getByText("需要人工识别");
    expect(badge.className).toContain("text-warn");
  });

  it("同屏对照：混合批次可撤销、纯 needs_ocr 不可撤销、reviewing 批次只有「去确认」", async () => {
    renderImports([
      batch("b-mixed", "混合批次.csv", "needs_ocr", [candidate("cd-1", "confirmed")]),
      batch("b-pure", "纯识别批次.csv", "needs_ocr", [candidate("cd-2", "pending")]),
      batch("b-review", "待确认批次.csv", "reviewing", [candidate("cd-3", "pending")]),
    ]);

    await screen.findByText("混合批次.csv");
    expect(screen.getAllByRole("button", { name: "整批撤销" })).toHaveLength(1);
    const reviewRow = screen.getByText("待确认批次.csv").closest("tr")!;
    expect(within(reviewRow).queryByRole("button", { name: "整批撤销" })).toBeNull();
    expect(within(reviewRow).getByRole("link", { name: "去确认" })).toBeInTheDocument();
  });
});
