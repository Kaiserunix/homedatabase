/**
 * 手机端导入页写操作锁定（http fetch 打桩，契约级，非端到端）：
 * - 候选 PATCH 在途时 confirm/skip 锁定（DOM disabled + 事件处理 guard 双保险）；
 * - 409 后立即清空旧勾选并锁定，刷新 GET 失败时明示失败、保持锁定，
 *   「重试刷新」成功后才解锁。
 */
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import ImportPage from "@/mobile/pages/Import";
import type { ImportBatch, ImportCandidate } from "@/data/types";

const jsonResponse = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

const candidate = (id: string, payee: string, version: number): ImportCandidate => ({
  id,
  date: "2026-08-01T12:00:00",
  payee,
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
  version,
});

const batch = (version: number): ImportBatch => ({
  id: "b-1",
  filename: "账单.csv",
  source: "手动CSV",
  importedAt: "2026-08-02T21:14:00",
  status: "reviewing",
  ownerId: "m-dad",
  targetAccountId: "a-cmb",
  jobId: "job-1",
  progress: 100,
  message: "",
  fileCount: 1,
  filesProcessed: 1,
  candidateCount: 1,
  needsOcrCount: 0,
  failedFileCount: 0,
  version,
  files: [],
  candidates: [candidate("cd-A", "商户甲", version)],
});

const accounts = [
  {
    id: "a-cmb", name: "招商银行储蓄卡", kind: "asset", scope: "personal", ownerId: "m-dad",
    permission: "manage", currency: "CNY", balanceCents: 0, icon: "landmark", version: 1,
  },
];
const categories = [
  { id: "c-food", name: "餐饮", kind: "expense", parentId: null, icon: "utensils", active: true, version: 1 },
];

const renderPage = () =>
  render(
    <MemoryRouter>
      <ImportPage />
    </MemoryRouter>,
  );

/** 候选行容器：label 包裹 checkbox，分类下拉在其父级 div 内 */
const candidateRow = async () =>
  (await screen.findByText("商户甲")).closest("label")!.parentElement!;

describe("手机端导入：PATCH 在途与 409 刷新失败的写锁", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_USE_MOCKS", ""); // 关闭 mock，走 http 分支
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  it("候选 PATCH 在途时确认按钮锁定且不会发出 confirm；resolve 后才可操作", async () => {
    let resolvePatch!: (r: Response) => void;
    const patchPromise = new Promise<Response>((r) => {
      resolvePatch = r;
    });
    let confirmCalls = 0;
    const fetchMock = vi.fn().mockImplementation((input: unknown, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url === "/api/import-batches") return Promise.resolve(jsonResponse(200, [batch(1)]));
      if (url === "/api/accounts") return Promise.resolve(jsonResponse(200, accounts));
      if (url === "/api/categories") return Promise.resolve(jsonResponse(200, categories));
      if (url === "/api/import-batches/b-1/candidates/cd-A" && method === "PATCH") {
        return patchPromise; // deferred：测试控制 resolve 时机
      }
      if (url === "/api/import-batches/b-1/confirm" && method === "POST") {
        confirmCalls += 1;
        return Promise.resolve(jsonResponse(200, batch(2)));
      }
      return Promise.resolve(jsonResponse(404, { detail: "not found" }));
    });
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    renderPage();

    // 触发候选分类 PATCH（deferred）
    const row = await candidateRow();
    await user.click(within(row).getByRole("combobox"));
    await user.click(await screen.findByRole("option", { name: "餐饮" }));

    // PATCH 在途：确认按钮禁用，事件处理 guard 也不会发出 confirm
    const confirmBtn = screen.getByRole("button", { name: /确认入账/ });
    await waitFor(() => expect(confirmBtn).toBeDisabled());
    await user.click(confirmBtn);
    expect(confirmCalls).toBe(0);

    // PATCH resolve：以返回候选（v2）更新后解锁
    await act(async () => {
      resolvePatch(jsonResponse(200, { ...candidate("cd-A", "商户甲", 2), suggestedCategoryId: "c-food" }));
    });
    await waitFor(() => expect(confirmBtn).toBeEnabled());
    await user.click(confirmBtn);
    await waitFor(() => expect(confirmCalls).toBe(1));
  });

  it("409 后刷新 GET 失败：清空旧勾选、显示刷新失败并保持锁定；重试刷新成功才解锁", async () => {
    let refreshShouldFail = true;
    let confirmCalls = 0;
    const fetchMock = vi.fn().mockImplementation((input: unknown, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url === "/api/import-batches") return Promise.resolve(jsonResponse(200, [batch(1)]));
      if (url === "/api/import-batches/b-1" && method === "GET") {
        // 冲突刷新在标志位置下前一律失败（含 apiFetch 的 GET 自动重试）
        return Promise.resolve(
          refreshShouldFail
            ? jsonResponse(500, { detail: "服务器内部错误" })
            : jsonResponse(200, batch(2)),
        );
      }
      if (url === "/api/accounts") return Promise.resolve(jsonResponse(200, accounts));
      if (url === "/api/categories") return Promise.resolve(jsonResponse(200, categories));
      if (url === "/api/import-batches/b-1/confirm" && method === "POST") {
        confirmCalls += 1;
        return Promise.resolve(
          confirmCalls === 1
            ? jsonResponse(409, { detail: "版本冲突，请刷新后重试" })
            : jsonResponse(200, batch(2)),
        );
      }
      return Promise.resolve(jsonResponse(404, { detail: "not found" }));
    });
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    renderPage();

    // 默认勾选商户甲，确认 → 409 → 刷新 GET 500
    await screen.findByText("商户甲");
    await user.click(screen.getByRole("button", { name: /确认入账/ }));

    // 刷新失败：明示失败、保持锁定，绝不谎报「已刷新」
    await screen.findByText(/刷新失败/);
    expect(screen.queryByText(/已刷新为最新内容/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /确认入账/ })).toBeDisabled();
    expect(confirmCalls).toBe(1); // 锁定期间不会再发出 confirm

    // 重试刷新成功：解除锁定并显示已刷新，之后才能重新确认
    refreshShouldFail = false;
    await user.click(screen.getByRole("button", { name: "重试刷新" }));
    await screen.findByText(/已刷新为最新内容，请重新确认/);
    const confirmBtn = screen.getByRole("button", { name: /确认入账/ });
    await waitFor(() => expect(confirmBtn).toBeEnabled());
    await user.click(confirmBtn);
    await waitFor(() => expect(confirmCalls).toBe(2));
  });
});
