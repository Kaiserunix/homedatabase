/**
 * 分类管理页组件测试（mock 模式，非端到端）：
 * 两级层级渲染、停用/启用、新增——写操作以 API 响应更新本地列表。
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterAll, afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import CategoriesPage from "@/admin/pages/Categories";
import { setMockLatency } from "@/api/resources";
import type { Category } from "@/data/types";

const jsonResponse = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

describe("分类管理页（mock 模式）", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_USE_MOCKS", "true"); // mock 仅由显式开关启用
    setMockLatency(0);
  });

  afterAll(() => {
    vi.unstubAllEnvs();
  });

  it("渲染两级分类层级与停用状态", async () => {
    render(<CategoriesPage />);
    // 一级分类与其子分类都列出
    expect(await screen.findByText("餐饮")).toBeInTheDocument();
    expect(screen.getByText("外卖")).toBeInTheDocument();
    // mock 数据里 c-snack 零食饮料 active:false
    expect(screen.getByText("零食饮料")).toBeInTheDocument();
    const snackRow = screen.getByText("零食饮料").closest("tr")!;
    expect(within(snackRow).getByText("已停用")).toBeInTheDocument();
  });

  it("停用一级分类：调用后以响应为准切换为「已停用 / 启用」", async () => {
    const user = userEvent.setup();
    render(<CategoriesPage />);
    const row = (await screen.findByText("餐饮")).closest("tr")!;
    await user.click(within(row).getByRole("button", { name: "停用" }));

    expect(await screen.findByText("餐饮")).toBeInTheDocument();
    const updated = screen.getByText("餐饮").closest("tr")!;
    expect(within(updated).getByText("已停用")).toBeInTheDocument();
    expect(within(updated).getByRole("button", { name: "启用" })).toBeInTheDocument();
  });

  it("新增一级分类：名称必填，保存后出现在列表", async () => {
    const user = userEvent.setup();
    render(<CategoriesPage />);
    await screen.findByText("餐饮");

    await user.click(screen.getByRole("button", { name: /新增分类/ }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByPlaceholderText("例如：餐饮"), "宠物");
    await user.click(within(dialog).getByRole("button", { name: "保存" }));

    expect(await screen.findByText("宠物")).toBeInTheDocument();
  });

  it("停用的分类在管理页可见（includeInactive）并可重新启用", async () => {
    const user = userEvent.setup();
    render(<CategoriesPage />);
    // mock 里 c-snack「零食饮料」active:false；管理页必须能看到它
    const row = (await screen.findByText("零食饮料")).closest("tr")!;
    expect(within(row).getByText("已停用")).toBeInTheDocument();
    // 重新启用：以响应为准切回「启用中」
    await user.click(within(row).getByRole("button", { name: "启用" }));
    const updated = screen.getByText("零食饮料").closest("tr")!;
    expect(await within(updated).findByText("启用中")).toBeInTheDocument();
  });

  it("父分类候选只列启用中的一级分类（停用的不可选作父分类）", async () => {    const user = userEvent.setup();
    render(<CategoriesPage />);
    // 先停用「餐饮」
    const row = (await screen.findByText("餐饮")).closest("tr")!;
    await user.click(within(row).getByRole("button", { name: "停用" }));
    await within(screen.getByText("餐饮").closest("tr")!).findByRole("button", { name: "启用" });

    // 打开新增弹窗的父分类下拉：餐饮不再可选，居家等启用中的一级分类仍在
    await user.click(screen.getByRole("button", { name: /新增分类/ }));
    const dialog = await screen.findByRole("dialog");
    const combos = within(dialog).getAllByRole("combobox");
    await user.click(combos[1]); // 方向 / 父分类 / 图标 → 父分类
    const optionNames = (await screen.findAllByRole("option")).map((o) => o.textContent);
    expect(optionNames).toContain("无（作为一级分类）");
    expect(optionNames).toContain("居家");
    expect(optionNames).not.toContain("餐饮");
  });
});

describe("分类编辑 409（http fetch 打桩，契约级）", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_USE_MOCKS", ""); // 关闭 mock，走 http 分支
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  const cat = (name: string, version: number): Category => ({
    id: "c-food",
    name,
    kind: "expense",
    icon: "utensils",
    parentId: null,
    active: true,
    version,
  });

  /** 按真实路径与查询参数匹配：GET /api/categories?includeInactive=true */
  const isListCall = (input: unknown, init?: RequestInit) => {
    const u = new URL(String(input), "http://stub.local");
    return u.pathname === "/api/categories" && u.searchParams.get("includeInactive") === "true"
      && (init?.method ?? "GET") === "GET";
  };
  const isPatchCall = (input: unknown, init?: RequestInit) => {
    const u = new URL(String(input), "http://stub.local");
    return u.pathname === "/api/categories/c-food" && init?.method === "PATCH";
  };

  it("409 后关闭编辑框并刷新列表；重新打开携带最新 expectedVersion，旧状态不能直接再提交", async () => {
    let patchCalls = 0;
    const patchBodies: unknown[] = [];
    const fetchMock = vi.fn().mockImplementation((input: unknown, init?: RequestInit) => {
      if (isListCall(input, init)) {
        // 首次加载 v1；409 冲突后返回他人改过的 v2
        return Promise.resolve(jsonResponse(200, [patchCalls === 0 ? cat("餐饮", 1) : cat("餐饮（他人已改）", 2)]));
      }
      if (isPatchCall(input, init)) {
        patchCalls += 1;
        patchBodies.push(JSON.parse(String(init?.body)));
        if (patchCalls === 1) {
          return Promise.resolve(jsonResponse(409, { detail: "版本冲突，请刷新后重试" }));
        }
        return Promise.resolve(jsonResponse(200, cat("餐饮（我改）", 3)));
      }
      return Promise.resolve(jsonResponse(404, { detail: "not found" }));
    });
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    render(<CategoriesPage />);

    // 打开编辑框并改名保存 → 409
    const row = (await screen.findByText("餐饮")).closest("tr")!;
    await user.click(within(row).getByRole("button", { name: "编辑" }));
    const dialog = await screen.findByRole("dialog");
    const nameInput = within(dialog).getByDisplayValue("餐饮");
    await user.clear(nameInput);
    await user.type(nameInput, "餐饮改");
    await user.click(within(dialog).getByRole("button", { name: "保存" }));

    // 409：编辑框必须关闭（旧版本表单被丢弃）、提示重新核对、列表刷新为他人版本
    await screen.findByText(/已为你加载最新列表/);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await screen.findByText("餐饮（他人已改）");

    // 重新打开编辑框再保存：携带的是刷新后的 expectedVersion=2，而不是旧的 1
    const row2 = screen.getByText("餐饮（他人已改）").closest("tr")!;
    await user.click(within(row2).getByRole("button", { name: "编辑" }));
    const dialog2 = await screen.findByRole("dialog");
    expect(within(dialog2).getByDisplayValue("餐饮（他人已改）")).toBeInTheDocument();
    await user.click(within(dialog2).getByRole("button", { name: "保存" }));

    await waitFor(() => expect(patchCalls).toBe(2));
    expect(patchBodies[0]).toMatchObject({ expectedVersion: 1 });
    expect(patchBodies[1]).toMatchObject({ expectedVersion: 2 });
  });

  it("409 后刷新 GET 连续失败：旧弹窗立即关闭、全部写操作锁定、文案不声称已刷新；重试成功后携带 v2", async () => {
    let patchCalls = 0;
    const patchBodies: unknown[] = [];
    let loaded = false;
    let refreshShouldFail = true;
    const fetchMock = vi.fn().mockImplementation((input: unknown, init?: RequestInit) => {
      if (isListCall(input, init)) {
        if (!loaded) {
          loaded = true;
          return Promise.resolve(jsonResponse(200, [cat("餐饮", 1)]));
        }
        // 冲突刷新在标志位置下前一律失败（覆盖 apiFetch 的 GET 自动重试：两次 500）
        return Promise.resolve(
          refreshShouldFail
            ? jsonResponse(500, { detail: "服务器内部错误" })
            : jsonResponse(200, [cat("餐饮（他人已改）", 2)]),
        );
      }
      if (isPatchCall(input, init)) {
        patchCalls += 1;
        patchBodies.push(JSON.parse(String(init?.body)));
        return Promise.resolve(
          patchCalls === 1
            ? jsonResponse(409, { detail: "版本冲突，请刷新后重试" })
            : jsonResponse(200, cat("餐饮（我改）", 3)),
        );
      }
      return Promise.resolve(jsonResponse(404, { detail: "not found" }));
    });
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    render(<CategoriesPage />);

    // 打开编辑框并保存 → PATCH v1 409 → 刷新 GET 连续 500
    const row = (await screen.findByText("餐饮")).closest("tr")!;
    await user.click(within(row).getByRole("button", { name: "编辑" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "保存" }));

    // 旧弹窗立即关闭；诚实提示「冲突且刷新失败」，绝不声称已刷新
    await screen.findByText(/刷新最新列表失败/);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.queryByText(/已为你加载最新列表/)).not.toBeInTheDocument();

    // 写锁生效：新增/编辑/停用全部禁用，旧行不可写
    expect(screen.getByRole("button", { name: /新增分类/ })).toBeDisabled();
    const lockedRow = screen.getByText("餐饮").closest("tr")!;
    expect(within(lockedRow).getByRole("button", { name: "编辑" })).toBeDisabled();
    expect(within(lockedRow).getByRole("button", { name: "停用" })).toBeDisabled();
    expect(patchCalls).toBe(1); // 锁定期间不会再发出 PATCH

    // 重试刷新成功：解锁并提示已刷新
    refreshShouldFail = false;
    await user.click(screen.getByRole("button", { name: "重试刷新" }));
    await screen.findByText(/已为你加载最新列表/);
    await screen.findByText("餐饮（他人已改）");

    // 重开编辑框再保存：携带刷新后的 expectedVersion=2
    const row2 = screen.getByText("餐饮（他人已改）").closest("tr")!;
    await user.click(within(row2).getByRole("button", { name: "编辑" }));
    const dialog2 = await screen.findByRole("dialog");
    await user.click(within(dialog2).getByRole("button", { name: "保存" }));
    await waitFor(() => expect(patchCalls).toBe(2));
    expect(patchBodies[1]).toMatchObject({ expectedVersion: 2 });
  });

  it("新增 POST 409：addForm 同步重置，重开不携带旧 name/parentId（父分类已停用也不残留）", async () => {
    let postCalls = 0;
    let loaded = false;
    const fetchMock = vi.fn().mockImplementation((input: unknown, init?: RequestInit) => {
      const u = new URL(String(input), "http://stub.local");
      const method = init?.method ?? "GET";
      if (u.pathname === "/api/categories" && method === "GET") {
        if (!loaded) {
          loaded = true;
          return Promise.resolve(jsonResponse(200, [cat("餐饮", 1)]));
        }
        // 冲突后：餐饮已被他人停用（v2）
        return Promise.resolve(jsonResponse(200, [{ ...cat("餐饮", 2), active: false }]));
      }
      if (u.pathname === "/api/categories" && method === "POST") {
        postCalls += 1;
        return Promise.resolve(jsonResponse(409, { detail: "版本冲突，请刷新后重试" }));
      }
      return Promise.resolve(jsonResponse(404, { detail: "not found" }));
    });
    vi.stubGlobal("fetch", fetchMock);

    const user = userEvent.setup();
    render(<CategoriesPage />);

    // 新增：填名称并选父分类「餐饮」→ POST 409
    await screen.findByText("餐饮");
    await user.click(screen.getByRole("button", { name: /新增分类/ }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByPlaceholderText("例如：餐饮"), "宠物");
    const combos = within(dialog).getAllByRole("combobox");
    await user.click(combos[1]); // 父分类
    await user.click(await screen.findByRole("option", { name: "餐饮" }));
    await user.click(within(dialog).getByRole("button", { name: "保存" }));

    // 409：弹窗关闭、刷新成功（餐饮已停用）、解锁提示
    await screen.findByText(/已为你加载最新列表/);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(postCalls).toBe(1);

    // 重开新增框：名称已清空、父分类回到「无」，且已停用的餐饮不在候选中
    await user.click(screen.getByRole("button", { name: /新增分类/ }));
    const dialog2 = await screen.findByRole("dialog");
    expect(within(dialog2).getByPlaceholderText("例如：餐饮")).toHaveValue("");
    const combos2 = within(dialog2).getAllByRole("combobox");
    expect(within(combos2[1]).getByText("无（作为一级分类）")).toBeInTheDocument();
    await user.click(combos2[1]);
    const optionNames = (await screen.findAllByRole("option")).map((o) => o.textContent);
    expect(optionNames).not.toContain("餐饮");
  });
});
