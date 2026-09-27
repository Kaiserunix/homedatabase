import { expect, test, type Page } from "@playwright/test";

/**
 * 真实后端冒烟（非 mock）：
 * - playwright.config.ts 在专用端口 5202 启动 Vite 并强制 VITE_USE_MOCKS=false
 *   （或用 HOMEDATABASE_E2E_BASE_URL 指向外部实例，同样先做真实 API 预检）。
 * - 缺密码即抛错让 e2e 命令失败，绝不允许全 skip 假绿。
 * - 先预检 /api/health 与匿名 /api/auth/me=401，再密码登录。
 * - 全文件串行；只读导航与结构断言，默认不在真实后端写用户账本
 *   （真实写流程已有独立临时库浏览器证据）。
 * 双端路由均为 HashRouter：index.html=手机端，admin.html=桌面管理台。
 */

const username = process.env.HOMEDATABASE_E2E_USERNAME ?? "son";
const password = process.env.HOMEDATABASE_E2E_PASSWORD ?? process.env.VITE_E2E_PASSWORD ?? "";

if (!password) {
  throw new Error(
    "缺少 HOMEDATABASE_E2E_PASSWORD（或 VITE_E2E_PASSWORD）：真实后端冒烟拒绝以全 skip 假绿退出。",
  );
}

test.describe.configure({ mode: "serial" });

/** 密码登录：未登录会被守卫跳到 /login，登录成功后离开登录页并回到来源页 */
async function loginWithPassword(page: Page, entry: string) {
  await page.goto(entry);
  await expect(page).toHaveURL(/#\/login/);
  await page.getByPlaceholder("dad / mom / son").fill(username);
  await page.getByRole("button", { name: "密码登录" }).click();
  await page.getByPlaceholder("密码").fill(password);
  // 必须真实发出 POST /api/auth/login 且响应成功：mock 页面不会发出该请求，
  // 外部 HOMEDATABASE_E2E_BASE_URL 若指向 mock 前端，这里会因等不到请求而超时失败
  const loginResponse = page.waitForResponse(
    (r) => r.url().includes("/api/auth/login") && r.request().method() === "POST" && r.ok(),
  );
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await loginResponse;
  // 登录失败会停留在 /login 并显示错误；成功则跳回来源页
  await expect(page).not.toHaveURL(/#\/login/);
}

test("预检：/api/health 可用且匿名 /api/auth/me 返回 401", async ({ request }) => {
  const health = await request.get("/api/health");
  expect(health.ok(), "/api/health 必须可用（后端未启动或不达）").toBeTruthy();
  const me = await request.get("/api/auth/me");
  expect(me.status(), "匿名 /api/auth/me 必须是 401，否则不是真实会话后端").toBe(401);
});

test.describe("手机端（390x844）", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("密码登录 → 首页概览 → 流水页类型筛选 → 记一笔表单", async ({ page }) => {
    await loginWithPassword(page, "/");
    await expect(page.getByRole("heading", { name: "家庭账本" })).toBeVisible();

    // 流水页：类型 Tab 存在（不断言任何固定流水内容）
    await page.getByRole("link", { name: "流水", exact: true }).click();
    await expect(page).toHaveURL(/#\/txns$/);
    await expect(page.getByRole("tab", { name: "收入" })).toBeVisible();
    await page.getByRole("tab", { name: "收入" }).click();

    // 记一笔表单打开（不提交，避免在真实后端写入数据）
    await page.getByRole("link", { name: "记一笔" }).click();
    await expect(page).toHaveURL(/#\/add$/);
    await expect(page.getByPlaceholder("0.00")).toBeVisible();
  });
});

test.describe("桌面端（1440x900）", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("密码登录 → 仪表盘统计卡 → 分类管理 → 备份页", async ({ page }) => {
    await loginWithPassword(page, "/admin.html");
    await expect(page.getByRole("heading", { name: "仪表盘" })).toBeVisible();
    for (const label of ["总资产", "总负债", "本月收入", "本月净支出"]) {
      await expect(page.getByText(label, { exact: true })).toBeVisible();
    }

    // 分类管理页（两级分类列表 + 新增入口）
    await page.getByRole("link", { name: "分类管理" }).click();
    await expect(page).toHaveURL(/admin\.html#\/categories$/);
    await expect(page.getByRole("heading", { name: "分类管理" })).toBeVisible();

    // 备份页：如实展示后端返回的存放位置
    await page.getByRole("link", { name: "备份与恢复" }).click();
    await expect(page).toHaveURL(/admin\.html#\/backups$/);
    await expect(page.getByRole("heading", { name: "备份与恢复" })).toBeVisible();
  });

  test("外观页点「靛蓝」→ html[data-theme=indigo]", async ({ page }) => {
    await loginWithPassword(page, "/admin.html");
    await page.getByRole("link", { name: "外观" }).click();
    await expect(page).toHaveURL(/admin\.html#\/theme$/);
    await expect(page.locator("html")).toHaveAttribute("data-theme", "teal");
    await page.getByRole("button", { name: "靛蓝" }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "indigo");
  });
});
