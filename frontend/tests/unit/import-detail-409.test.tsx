/**
 * 管理端导入详情 409 冲突处理（http fetch 打桩，契约级，非端到端）：
 * 任一写路径 409 后必须 await 拉取最新批次并清空本地勾选改动，
 * 强制用户基于最新数据重新审阅，绝不允许沿用旧勾选直接确认。
 */
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import ImportDetailPage from "@/admin/pages/ImportDetail";
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
  candidateCount: 2,
  needsOcrCount: 0,
  failedFileCount: 0,
  version,
  files: [],
  candidates: [candidate("cd-A", "商户甲", version), candidate("cd-B", "商户乙", version)],
});

describe("导入详情：409 后强制刷新并重建勾选（fetch 打桩）", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_USE_MOCKS", ""); // 关闭 mock，走 http 分支
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  it("确认遇 409：本地取消的勾选被重置，再次确认提交的是刷新后的完整勾选", async () => {
    let batchVersion = 1;
    let confirmCalls = 0;
    const confirmBodies: unknown[] = [];
    const fetchMock = vi.fn().mockImplementation((input: unknown, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url === "/api/import-batches/b-1" && method === "GET") {
        return Promise.resolve(jsonResponse(200, batch(batchVersion)));
      }
      if (url === "/api/categories") return Promise.resolve(jsonResponse(200, []));
      if (url === "/api/accounts") return Promise.resolve(jsonResponse(200, []));
      if (url === "/api/import-batches/b-1/confirm" && method === "POST") {
        confirmCalls += 1;
        confirmBodies.push(JSON.parse(String(init?.body)));
        if (confirmCalls === 1) {
          batchVersion = 2; // 他人已修改，版本前进
          return Promise.resolve(jsonResponse(409, { detail: "版本冲突，请刷新后重试" }));
        }
        return Promise.resolve(jsonResponse(200, batch(batchVersion)));
      }
      return Promise.resolve(jsonResponse(404, { detail: "not found" }));
    });
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/imports/b-1"]}>
        <Routes>
          <Route path="/imports/:batchId" element={<ImportDetailPage />} />
        </Routes>
      </MemoryRouter>,
    );

    const cbA = await screen.findByRole("checkbox", { name: "选择 商户甲" });
    expect(cbA).toBeChecked(); // valid 候选默认勾选
    // 用户本地取消勾选商户甲（尚未提交）
    await user.click(cbA);
    expect(screen.getByRole("checkbox", { name: "选择 商户甲" })).not.toBeChecked();

    // 确认遇 409：必须 await 刷新最新批次、清空本地勾选改动并提示重新审阅
    await user.click(screen.getByRole("button", { name: "确认入账" }));
    await screen.findByText(/已为你刷新最新候选，请重新核对后再操作/);
    await waitFor(() =>
      expect(screen.getByRole("checkbox", { name: "选择 商户甲" })).toBeChecked(),
    );

    // 重新审阅后再次确认：提交的是刷新后两条候选，而不是 409 前的旧勾选（只有 cd-B）
    await user.click(screen.getByRole("button", { name: "确认入账" }));
    await waitFor(() => expect(confirmCalls).toBe(2));
    expect(confirmBodies[0]).toEqual({ candidateIds: ["cd-B"] });
    expect(confirmBodies[1]).toEqual({ candidateIds: ["cd-A", "cd-B"] });
  });

  it("409 后刷新 GET 失败：清空旧勾选并保持锁定、显示刷新失败；重试刷新成功才解锁", async () => {
    let loaded = false;
    let refreshShouldFail = true;
    let confirmCalls = 0;
    const fetchMock = vi.fn().mockImplementation((input: unknown, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url === "/api/import-batches/b-1" && method === "GET") {
        if (!loaded) {
          loaded = true;
          return Promise.resolve(jsonResponse(200, batch(1)));
        }
        // 冲突刷新在标志位置下前一律失败（含 apiFetch 的 GET 自动重试）
        return Promise.resolve(
          refreshShouldFail
            ? jsonResponse(500, { detail: "服务器内部错误" })
            : jsonResponse(200, batch(2)),
        );
      }
      if (url === "/api/categories") return Promise.resolve(jsonResponse(200, []));
      if (url === "/api/accounts") return Promise.resolve(jsonResponse(200, []));
      if (url === "/api/import-batches/b-1/confirm" && method === "POST") {
        confirmCalls += 1;
        return Promise.resolve(jsonResponse(409, { detail: "版本冲突，请刷新后重试" }));
      }
      return Promise.resolve(jsonResponse(404, { detail: "not found" }));
    });
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/imports/b-1"]}>
        <Routes>
          <Route path="/imports/:batchId" element={<ImportDetailPage />} />
        </Routes>
      </MemoryRouter>,
    );

    // 本地取消勾选商户甲后确认 → 409 → 刷新 GET 500
    const cbA = await screen.findByRole("checkbox", { name: "选择 商户甲" });
    await user.click(cbA);
    await user.click(screen.getByRole("button", { name: "确认入账" }));

    // 刷新失败：明示失败文案，绝不谎报「已刷新」
    await screen.findByText(/刷新最新内容失败/);
    expect(screen.queryByText(/已为你刷新最新候选/)).not.toBeInTheDocument();
    // 旧勾选被立即清空（回到默认勾选），但写操作保持锁定
    await waitFor(() =>
      expect(screen.getByRole("checkbox", { name: "选择 商户甲" })).toBeChecked(),
    );
    expect(screen.getByRole("button", { name: "确认入账" })).toBeDisabled();
    expect(confirmCalls).toBe(1); // 锁定期间不会再发出 confirm

    // 重试刷新成功：解除锁定并显示已刷新，之后才能重新确认
    refreshShouldFail = false;
    await user.click(screen.getByRole("button", { name: "重试刷新" }));
    await screen.findByText(/已为你刷新最新候选，请重新核对后再操作/);
    const confirmBtn = screen.getByRole("button", { name: "确认入账" });
    await waitFor(() => expect(confirmBtn).toBeEnabled());
    await user.click(confirmBtn);
    await waitFor(() => expect(confirmCalls).toBe(2));
  });

  it("候选 PATCH 在途时确认/跳过被锁定；PATCH resolve 后基于新状态才可操作", async () => {
    let resolvePatch!: (r: Response) => void;
    const patchPromise = new Promise<Response>((r) => {
      resolvePatch = r;
    });
    let confirmCalls = 0;
    const fetchMock = vi.fn().mockImplementation((input: unknown, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      if (url === "/api/import-batches/b-1" && method === "GET") {
        return Promise.resolve(jsonResponse(200, batch(1)));
      }
      if (url === "/api/categories") {
        return Promise.resolve(
          jsonResponse(200, [
            { id: "c-food", name: "餐饮", kind: "expense", parentId: null, icon: "utensils", active: true, version: 1 },
          ]),
        );
      }
      if (url === "/api/accounts") return Promise.resolve(jsonResponse(200, []));
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
    render(
      <MemoryRouter initialEntries={["/imports/b-1"]}>
        <Routes>
          <Route path="/imports/:batchId" element={<ImportDetailPage />} />
        </Routes>
      </MemoryRouter>,
    );

    // 触发商户甲的分类 PATCH（deferred，始终不 resolve 直到测试放行）
    const row = (await screen.findByText("商户甲")).closest("tr")!;
    await user.click(within(row).getByRole("combobox"));
    await user.click(await screen.findByRole("option", { name: "餐饮" }));

    // PATCH 在途：确认按钮禁用，且事件处理本身也有 guard——不会发出 confirm
    const confirmBtn = screen.getByRole("button", { name: "确认入账" });
    await waitFor(() => expect(confirmBtn).toBeDisabled());
    await user.click(confirmBtn);
    expect(confirmCalls).toBe(0);

    // PATCH resolve：以返回候选（v2，已选分类）更新后才解除锁定
    await act(async () => {
      resolvePatch(jsonResponse(200, { ...candidate("cd-A", "商户甲", 2), suggestedCategoryId: "c-food" }));
    });
    await waitFor(() => expect(confirmBtn).toBeEnabled());
    await user.click(confirmBtn);
    await waitFor(() => expect(confirmCalls).toBe(1));
  });
});
