import { defineConfig, devices } from "@playwright/test";

/**
 * e2e（真实后端，非 mock）：
 * - 默认在专用端口 5202 启动 Vite dev，并以 webServer.env 强制 VITE_USE_MOCKS=false，
 *   绝不复用可能开了 mock 的 5200 实例（reuseExistingServer=false）。
 * - HOMEDATABASE_E2E_BASE_URL 可指向外部已启动的前端（同源反代 /api 的实例），
 *   此时不启动本地 webServer，但 smoke 仍会对该 URL 做真实 API 预检
 *   （/api/health 200 + 匿名 /api/auth/me 401）。
 * - 缺 HOMEDATABASE_E2E_PASSWORD / VITE_E2E_PASSWORD 时，spec 加载即抛错，
 *   e2e 命令明确失败，绝不以 0 条/全 skip 假绿退出。
 */
const externalBase = process.env.HOMEDATABASE_E2E_BASE_URL;
const baseURL = externalBase ?? "http://localhost:5202";

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 30_000,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: "list",
  use: {
    baseURL,
    trace: "retain-on-failure",
  },
  webServer: externalBase
    ? undefined
    : {
        command: "npm run dev -- --port 5202 --strictPort",
        url: "http://localhost:5202",
        reuseExistingServer: false,
        timeout: 60_000,
        env: { VITE_USE_MOCKS: "false" },
      },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
