/**
 * 契约测试（openapi.yaml，fetch 打桩，非端到端）
 *
 * 本文件以 vi.stubGlobal("fetch", ...) 打桩 HTTP 层，断言前端 API 客户端
 * 发出的请求与仓库根 openapi.yaml 的契约一致（路径、方法、请求体、
 * Cookie 会话语义），以及非 2xx 响应被如实抛出（绝不吞成假成功）。
 * 全程 mock 模式关闭（VITE_USE_MOCKS 置空），不连真实后端——
 * 这些不是端到端测试，真实联调行为需以后端联跑另行验证。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/api/client";
import {
  confirmImportCandidates,
  createCategory,
  createTransaction,
  exportReport,
  fetchMe,
  listAccounts,
  listCategories,
  login,
  logout,
  patchImportCandidate,
  respondToSplit,
  updateCategory,
  uploadImport,
} from "@/api/resources";
import type { Member } from "@/data/types";

const member: Member = {
  id: "m-dad",
  username: "dad",
  name: "爸爸",
  identity: "dad",
  isAdmin: true,
  color: "#3b82f6",
};

const jsonResponse = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

type FetchMock = ReturnType<typeof vi.fn>;

/** 取 fetch 打桩的第 N 次调用（默认最后一次），返回 { url, init } */
function callOf(fetchMock: FetchMock, index = -1): { url: string; init: RequestInit } {
  const calls = fetchMock.mock.calls as [unknown, RequestInit?][];
  const call = index < 0 ? calls[calls.length + index] : calls[index];
  expect(call, "fetch 应被调用").toBeTruthy();
  return { url: String(call[0]), init: (call[1] ?? {}) as RequestInit };
}

const headersOf = (init: RequestInit) => (init.headers ?? {}) as Record<string, string>;

describe("契约测试（openapi.yaml，fetch 打桩，非端到端）", () => {
  beforeEach(() => {
    // 与 tests/unit/api.test.ts 一致：mock 仅由显式开关启用，这里显式关闭
    vi.stubEnv("VITE_USE_MOCKS", "");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  describe("a. Cookie 登录会话（/auth/login、/auth/me、/auth/logout）", () => {
    it("login 发 POST /auth/login：JSON 请求体、credentials:include、无 Authorization 头", async () => {
      const fetchMock = vi.fn().mockResolvedValue(
        jsonResponse(200, { member, expiresAt: "2026-08-12T00:00:00Z" }),
      );
      vi.stubGlobal("fetch", fetchMock);

      const session = await login("dad", "pw");
      expect(session.member.username).toBe("dad");

      const { url, init } = callOf(fetchMock);
      expect(url).toBe("/api/auth/login");
      expect(init.method).toBe("POST");
      expect(init.credentials).toBe("include");
      expect(init.body).toBe(JSON.stringify({ username: "dad", password: "pw" }));
      expect(headersOf(init)["Content-Type"]).toBe("application/json");
      expect(headersOf(init)).not.toHaveProperty("Authorization");
    });

    it("fetchMe 发 GET /auth/me：credentials:include、无 Authorization 头", async () => {
      const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, member));
      vi.stubGlobal("fetch", fetchMock);

      const me = await fetchMe();
      expect(me.id).toBe("m-dad");

      const { url, init } = callOf(fetchMock);
      expect(url).toBe("/api/auth/me");
      expect(init.method ?? "GET").toBe("GET");
      expect(init.credentials).toBe("include");
      expect(headersOf(init)).not.toHaveProperty("Authorization");
    });

    it("logout 发 POST /auth/logout，204 空响应正常 resolve", async () => {
      const fetchMock = vi
        .fn()
        .mockResolvedValue(new Response(null, { status: 204 }));
      vi.stubGlobal("fetch", fetchMock);

      await expect(logout()).resolves.toBeUndefined();

      const { url, init } = callOf(fetchMock);
      expect(url).toBe("/api/auth/logout");
      expect(init.method).toBe("POST");
      expect(init.credentials).toBe("include");
    });
  });

  describe("b. FormData 多文件上传（POST /imports）", () => {
    it("请求体为真 FormData：两个 files 条目 + source + targetAccountId；不手动设 Content-Type", async () => {
      const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { id: "b-new" }));
      vi.stubGlobal("fetch", fetchMock);

      const files = [
        new File(["csv-1"], "bill-1.csv", { type: "text/csv" }),
        new File(["csv-2"], "bill-2.csv", { type: "text/csv" }),
      ];
      await uploadImport(files, "wechat", "a-001");

      const { url, init } = callOf(fetchMock);
      expect(url).toBe("/api/imports");
      expect(init.method).toBe("POST");
      expect(init.credentials).toBe("include");

      expect(init.body).toBeInstanceOf(FormData);
      const form = init.body as FormData;
      const sentFiles = form.getAll("files");
      expect(sentFiles).toHaveLength(2);
      expect((sentFiles[0] as File).name).toBe("bill-1.csv");
      expect((sentFiles[1] as File).name).toBe("bill-2.csv");
      expect(form.get("source")).toBe("wechat");
      expect(form.get("targetAccountId")).toBe("a-001");

      // multipart 边界必须由浏览器生成，客户端绝不设 Content-Type / Authorization
      const headers = headersOf(init);
      expect(headers["Content-Type"]).toBeUndefined();
      expect(headers).not.toHaveProperty("Authorization");
    });
  });

  describe("c. 409 版本冲突（乐观锁）", () => {
    it("PATCH 候选 409：patchImportCandidate 以 ApiError(409) 拒绝，请求体带 expectedVersion", async () => {
      const fetchMock = vi
        .fn()
        .mockResolvedValue(jsonResponse(409, { detail: "版本冲突，请刷新后重试" }));
      vi.stubGlobal("fetch", fetchMock);

      const err = await patchImportCandidate("b-1", "c-1", {
        payee: "修正后的商户",
        expectedVersion: 3,
      }).catch((e: unknown) => e);

      expect(err).toBeInstanceOf(ApiError);
      expect((err as ApiError).status).toBe(409);
      expect((err as ApiError).message).toBe("版本冲突，请刷新后重试");

      const { url, init } = callOf(fetchMock);
      expect(url).toBe("/api/import-batches/b-1/candidates/c-1");
      expect(init.method).toBe("PATCH");
      expect(JSON.parse(String(init.body))).toMatchObject({ expectedVersion: 3 });
    });

    it("POST 分摊响应 409：respondToSplit 以 ApiError(409) 拒绝，请求体带 expectedVersion", async () => {
      const fetchMock = vi
        .fn()
        .mockResolvedValue(jsonResponse(409, { detail: "版本冲突，请刷新后重试" }));
      vi.stubGlobal("fetch", fetchMock);

      const err = await respondToSplit("s-1", { accept: true, expectedVersion: 7 }).catch(
        (e: unknown) => e,
      );

      expect(err).toBeInstanceOf(ApiError);
      expect((err as ApiError).status).toBe(409);

      const { url, init } = callOf(fetchMock);
      expect(url).toBe("/api/splits/s-1/respond");
      expect(init.method).toBe("POST");
      expect(JSON.parse(String(init.body))).toMatchObject({ expectedVersion: 7 });
    });
  });

  describe("d. 401 / 403", () => {
    it("GET /auth/me 401：fetchMe 拒绝 ApiError(status=401, code=UNAUTHORIZED)", async () => {
      const fetchMock = vi
        .fn()
        .mockResolvedValue(jsonResponse(401, { detail: "Unauthorized" }));
      vi.stubGlobal("fetch", fetchMock);

      const err = await fetchMe().catch((e: unknown) => e);
      expect(err).toBeInstanceOf(ApiError);
      expect((err as ApiError).status).toBe(401);
      expect((err as ApiError).code).toBe("UNAUTHORIZED");
    });

    it("GET /accounts 403：listAccounts 拒绝 ApiError(403)，保留服务端 detail 文案", async () => {
      const fetchMock = vi
        .fn()
        .mockResolvedValue(jsonResponse(403, { detail: "没有权限查看这些账户" }));
      vi.stubGlobal("fetch", fetchMock);

      const err = await listAccounts().catch((e: unknown) => e);
      expect(err).toBeInstanceOf(ApiError);
      expect((err as ApiError).status).toBe(403);
      expect((err as ApiError).message).toBe("没有权限查看这些账户");
      expect(fetchMock).toHaveBeenCalledTimes(1); // 4xx 不重试
    });
  });

  describe("e. 不允许 catch 假成功；GET 仅幂等重试一次", () => {
    it("POST /import-batches/{id}/confirm 500：confirmImportCandidates 拒绝（不 resolve），且 POST 不重试", async () => {
      const fetchMock = vi
        .fn()
        .mockResolvedValue(jsonResponse(500, { detail: "服务器内部错误" }));
      vi.stubGlobal("fetch", fetchMock);

      const err = await confirmImportCandidates("b-1", ["c-1"]).catch((e: unknown) => e);
      expect(err).toBeInstanceOf(ApiError);
      expect((err as ApiError).status).toBe(500);
      expect(fetchMock).toHaveBeenCalledTimes(1); // POST 非幂等，绝不重试

      const { url, init } = callOf(fetchMock);
      expect(url).toBe("/api/import-batches/b-1/confirm");
      expect(init.method).toBe("POST");
    });

    it("GET /accounts 首次 500、重试 200：listAccounts 最终 resolve（GET 失败重试 1 次策略）", async () => {
      const fetchMock = vi
        .fn()
        .mockResolvedValueOnce(jsonResponse(500, { detail: "服务器内部错误" }))
        .mockResolvedValueOnce(jsonResponse(200, []));
      vi.stubGlobal("fetch", fetchMock);

      await expect(listAccounts()).resolves.toEqual([]);
      expect(fetchMock).toHaveBeenCalledTimes(2);
    });
  });

  describe("f. 报表导出为后端 Blob（GET /reports/export）", () => {
    it("非 JSON 响应 resolve 为 Blob；URL 携带 tier/from/to 查询参数", async () => {
      const fetchMock = vi.fn().mockResolvedValue(
        new Response(new Uint8Array([0x50, 0x4b, 0x03, 0x04]), {
          status: 200,
          headers: {
            "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
          },
        }),
      );
      vi.stubGlobal("fetch", fetchMock);

      const blob = await exportReport(5, { from: "2026-01-01", to: "2026-01-31" });
      // Node fetch and jsdom may expose different Blob constructors. Verify the payload.
      expect(Object.prototype.toString.call(blob)).toBe("[object Blob]");
      expect(blob.size).toBe(4);
      expect(blob.type).toBe("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet");
      expect(new Uint8Array(await blob.arrayBuffer())).toEqual(new Uint8Array([0x50, 0x4b, 0x03, 0x04]));

      const { url, init } = callOf(fetchMock);
      expect(url).toContain("/api/reports/export?");
      expect(url).toContain("tier=5&from=2026-01-01&to=2026-01-31");
      expect(init.credentials).toBe("include");
    });
  });

  describe("g. 外币候选补录汇率（needs_fx_review）", () => {
    it("PATCH 候选携带 fxRate、baseAmountCents 与 expectedVersion；resolve 返回的新候选", async () => {
      const updated = {
        id: "c-9",
        date: "2026-08-01T21:40:00",
        payee: "Amazon US",
        direction: "expense",
        amountCents: 15_99,
        currency: "USD",
        suggestedCategoryId: "c-other",
        originalCategory: "Shopping",
        targetAccountId: "a-usd",
        duplicateOfTxnId: null,
        status: "pending",
        validationStatus: "ok",
        confidence: "medium",
        fxRate: "7.20",
        baseAmountCents: 115_13,
        version: 3,
      };
      const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, updated));
      vi.stubGlobal("fetch", fetchMock);

      const next = await patchImportCandidate("b-1", "c-9", {
        fxRate: "7.20",
        baseAmountCents: 115_13,
        expectedVersion: 2,
      });

      // 成功后调用方以返回候选为准（version/validationStatus 随响应更新）
      expect(next.validationStatus).toBe("ok");
      expect(next.fxRate).toBe("7.20");
      expect(next.baseAmountCents).toBe(115_13);
      expect(next.version).toBe(3);

      const { url, init } = callOf(fetchMock);
      expect(url).toBe("/api/import-batches/b-1/candidates/c-9");
      expect(init.method).toBe("PATCH");
      expect(init.credentials).toBe("include");
      expect(JSON.parse(String(init.body))).toEqual({
        fxRate: "7.20",
        baseAmountCents: 115_13,
        expectedVersion: 2,
      });
    });

    it("PATCH 补汇率失败（422）如实拒绝，不伪造成功", async () => {
      const fetchMock = vi
        .fn()
        .mockResolvedValue(jsonResponse(422, { detail: [{ loc: ["body", "fxRate"], msg: "Input should be greater than 0", type: "greater_than" }] }));
      vi.stubGlobal("fetch", fetchMock);

      const err = await patchImportCandidate("b-1", "c-9", {
        fxRate: "0",
        baseAmountCents: 100,
        expectedVersion: 2,
      }).catch((e: unknown) => e);

      expect(err).toBeInstanceOf(ApiError);
      expect((err as ApiError).status).toBe(422);
      expect((err as ApiError).message).toContain("greater than 0");
    });
  });

  describe("h. 两级分类管理（/categories）", () => {
    it("createCategory 发 POST /categories：请求体含 name/kind/parentId", async () => {
      const created = {
        id: "c-new",
        name: "宠物",
        kind: "expense",
        parentId: "c-home",
        icon: "shapes",
        active: true,
        version: 1,
      };
      const fetchMock = vi.fn().mockResolvedValue(jsonResponse(201, created));
      vi.stubGlobal("fetch", fetchMock);

      const next = await createCategory({ name: "宠物", kind: "expense", icon: "shapes", parentId: "c-home" });
      expect(next.id).toBe("c-new");

      const { url, init } = callOf(fetchMock);
      expect(url).toBe("/api/categories");
      expect(init.method).toBe("POST");
      expect(init.credentials).toBe("include");
      expect(JSON.parse(String(init.body))).toEqual({
        name: "宠物",
        kind: "expense",
        icon: "shapes",
        parentId: "c-home",
      });
    });

    it("updateCategory 发 PATCH /categories/{id}：停用必须携带 expectedVersion", async () => {
      const fetchMock = vi.fn().mockResolvedValue(
        jsonResponse(200, {
          id: "c-food",
          name: "餐饮",
          kind: "expense",
          parentId: null,
          icon: "utensils",
          active: false,
          version: 3,
        }),
      );
      vi.stubGlobal("fetch", fetchMock);

      const next = await updateCategory("c-food", { active: false, expectedVersion: 2 });
      expect(next.active).toBe(false);

      const { url, init } = callOf(fetchMock);
      expect(url).toBe("/api/categories/c-food");
      expect(init.method).toBe("PATCH");
      expect(JSON.parse(String(init.body))).toEqual({ active: false, expectedVersion: 2 });
    });
  });

  describe("i. 退款必须携带 refundOf（POST /transactions）", () => {
    it("type=refund 的请求体包含 refundOf（原支出交易 ID）", async () => {
      const fetchMock = vi.fn().mockResolvedValue(jsonResponse(201, { id: "t-refund" }));
      vi.stubGlobal("fetch", fetchMock);

      await createTransaction({
        type: "refund",
        payee: "海底捞退款",
        note: "",
        categoryId: "c-food",
        accountId: "a-cmb",
        refundOf: "t-origin",
        amountCents: 12_50,
        currency: "CNY",
        date: "2026-08-05T10:00:00",
      });

      const { url, init } = callOf(fetchMock);
      expect(url).toBe("/api/transactions");
      expect(init.method).toBe("POST");
      expect(JSON.parse(String(init.body))).toMatchObject({
        type: "refund",
        refundOf: "t-origin",
      });
    });
  });

  describe("j. 分类列表 includeInactive 查询参数（GET /categories）", () => {
    it("默认不携带 includeInactive（active-only 口径）", async () => {
      const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, []));
      vi.stubGlobal("fetch", fetchMock);

      await expect(listCategories()).resolves.toEqual([]);
      const { url, init } = callOf(fetchMock);
      expect(url).toBe("/api/categories");
      expect(init.credentials).toBe("include");
    });

    it("includeInactive=true 时携带查询参数（管理端停用分类管理）", async () => {
      const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, []));
      vi.stubGlobal("fetch", fetchMock);

      await listCategories({ includeInactive: true });
      const { url } = callOf(fetchMock);
      expect(url).toBe("/api/categories?includeInactive=true");
    });
  });
});
