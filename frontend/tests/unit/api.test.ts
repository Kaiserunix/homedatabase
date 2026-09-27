import { act, renderHook, waitFor } from "@testing-library/react";
import { afterAll, afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiFetch } from "@/api/client";
import {
  confirmImportCandidates,
  createTransaction,
  getImportBatch,
  isMockMode,
  listAccounts,
  listTransactions,
  setMockLatency,
} from "@/api/resources";
import { useApiQuery } from "@/api/useApi";
import { accounts, transactions } from "@/data/mock";

const jsonResponse = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

describe("apiFetch / ApiError 归一化", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("500 + FastAPI detail JSON：detail 作为错误消息，GET 自动重试 1 次", async () => {
    const fetchMock = vi.fn().mockImplementation(() =>
      Promise.resolve(jsonResponse(500, { detail: "服务器内部错误" })),
    );
    vi.stubGlobal("fetch", fetchMock);

    const err: unknown = await apiFetch("/transactions").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    const apiErr = err as ApiError;
    expect(apiErr.status).toBe(500);
    expect(apiErr.message).toBe("服务器内部错误");
    expect(fetchMock).toHaveBeenCalledTimes(2); // 首次 + 重试 1 次
  });

  it("FastAPI 422 校验数组：拼接 msg，明细进 details", async () => {
    const detail = [
      { loc: ["body", "amountCents"], msg: "field required", type: "missing" },
      { loc: ["body", "date"], msg: "invalid date", type: "value_error" },
    ];
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(422, { detail })));

    const err: unknown = await apiFetch("/transactions", { method: "POST", body: {} }).catch(
      (e: unknown) => e,
    );
    expect(err).toBeInstanceOf(ApiError);
    const apiErr = err as ApiError;
    expect(apiErr.status).toBe(422);
    expect(apiErr.message).toBe("field required；invalid date");
    expect(apiErr.details).toEqual(detail);
  });

  it("网络异常：fetch 抛 TypeError 归一化为「网络异常」ApiError", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    const err: unknown = await apiFetch("/accounts").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    const apiErr = err as ApiError;
    expect(apiErr.status).toBe(0);
    expect(apiErr.code).toBe("NETWORK");
    expect(apiErr.message).toContain("网络异常");
  });

  it("401：抛出登录过期 ApiError，且不重试", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(401, { detail: "Unauthorized" }));
    vi.stubGlobal("fetch", fetchMock);

    const err: unknown = await apiFetch("/accounts").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    const apiErr = err as ApiError;
    expect(apiErr.status).toBe(401);
    expect(apiErr.message).toContain("登录已过期");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("useApiQuery 三态", () => {
  it("成功：loading → data", async () => {
    const { result } = renderHook(() => useApiQuery(() => Promise.resolve("ok"), []));
    expect(result.current.loading).toBe(true);

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data).toBe("ok");
    expect(result.current.error).toBeNull();
  });

  it("失败后 retry：error 携带 ApiError → 重取成功", async () => {
    const fn = vi.fn().mockRejectedValueOnce(new Error("boom")).mockResolvedValue("recovered");
    const { result } = renderHook(() => useApiQuery(fn, []));

    await waitFor(() => expect(result.current.error).not.toBeNull());
    expect(result.current.error).toBeInstanceOf(ApiError);
    expect(result.current.data).toBeNull();

    await act(async () => {
      result.current.retry();
    });
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data).toBe("recovered");
    expect(result.current.error).toBeNull();
    expect(fn).toHaveBeenCalledTimes(2);
  });
});

describe("resources（mock 模式）", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_USE_MOCKS", "true"); // mock 仅由显式开关启用
    setMockLatency(0); // 注入零延迟，测试不必 fake timers
  });

  afterAll(() => {
    vi.unstubAllEnvs();
  });

  it("VITE_USE_MOCKS=true 时为 mock 模式，其余情况一律 http", () => {
    expect(isMockMode()).toBe(true);
    vi.stubEnv("VITE_USE_MOCKS", "false");
    expect(isMockMode()).toBe(false);
  });

  it("listTransactions 返回 mock 交易，filter 生效", async () => {
    await expect(listTransactions()).resolves.toEqual(transactions);

    const expenses = await listTransactions({ type: "expense", month: "2026-07" });
    expect(expenses.length).toBeGreaterThan(0);
    for (const t of expenses) {
      expect(t.type).toBe("expense");
      expect(t.date.startsWith("2026-07")).toBe(true);
    }

    const hit = await listTransactions({ keyword: "海底捞" });
    expect(hit).toHaveLength(1);
    expect(hit[0].payee).toBe("海底捞");
  });

  it("listAccounts 返回 mock 账户", async () => {
    await expect(listAccounts()).resolves.toEqual(accounts);
  });

  it("getImportBatch：存在返回批次，不存在抛 404 ApiError", async () => {
    const batch = await getImportBatch("b-002");
    expect(batch.id).toBe("b-002");

    const err: unknown = await getImportBatch("b-404").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(404);
  });

  it("createTransaction：按复式规则回显借贷平衡的交易", async () => {
    const txn = await createTransaction({
      type: "expense",
      payee: "测试商户",
      note: "",
      categoryId: "c-food",
      accountId: "a-cmb",
      amountCents: 12_34,
      currency: "CNY",
      date: "2026-08-04T10:00:00",
    });
    const debit = txn.lines.reduce((s, l) => s + l.debitCents, 0);
    const credit = txn.lines.reduce((s, l) => s + l.creditCents, 0);
    expect(debit).toBe(credit);
    expect(debit).toBe(12_34);
    expect(txn.source).toBe("manual");
  });

  it("confirmImportCandidates：候选置为 confirmed，不改原 mock 数据", async () => {
    const next = await confirmImportCandidates("b-003", ["cd-1", "cd-2"]);
    expect(next.candidates.find((c) => c.id === "cd-1")?.status).toBe("confirmed");
    expect(next.candidates.find((c) => c.id === "cd-2")?.status).toBe("confirmed");
    expect(next.candidates.find((c) => c.id === "cd-3")?.status).toBe("pending");
    // 原 mock 数据保持只读
    const original = (await getImportBatch("b-003")).candidates.find((c) => c.id === "cd-1");
    expect(original?.status).toBe("pending");
  });
});
