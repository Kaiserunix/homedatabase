/**
 * 会话过期处理：页面打开后任何 API 返回 401，AuthProvider 必须转为 anon，
 * 由路由守卫跳登录页并保留返回路径（state.from），而不是只在页面内显示错误。
 */
import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { AuthProvider, RequireAuth } from "@/auth/AuthContext";
import { listAccounts } from "@/api/resources";
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

/** 登录页探针：显示守卫传来的返回路径 */
function LoginProbe() {
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from ?? "";
  return <div>登录页 from={from}</div>;
}

function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<LoginProbe />} />
        <Route
          path="/txns"
          element={
            <RequireAuth>
              <div>受保护页</div>
            </RequireAuth>
          }
        />
      </Routes>
    </AuthProvider>
  );
}

describe("401 全局处理：会话转 anon 并跳登录页", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_USE_MOCKS", ""); // 关闭 mock，走 http 分支
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  it("已登录后任一 API 返回 401：跳 /login 且 state.from 保留原路径", async () => {
    const fetchMock = vi.fn().mockImplementation((input: unknown) => {
      const url = String(input);
      if (url.includes("/auth/me")) return Promise.resolve(jsonResponse(200, member));
      // 会话过期：后续任意请求 401
      return Promise.resolve(jsonResponse(401, { detail: "Unauthorized" }));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter initialEntries={["/txns"]}>
        <App />
      </MemoryRouter>,
    );

    // 启动 fetchMe 成功 → 受保护页渲染
    await screen.findByText("受保护页");

    // 页面打开后某个取数返回 401
    await act(async () => {
      await listAccounts().catch(() => {});
    });

    // 不是页面内错误，而是整局跳登录页，且保留返回路径
    await screen.findByText(/登录页/);
    expect(screen.getByText(/from=\/txns/)).toBeInTheDocument();
    expect(screen.queryByText("受保护页")).not.toBeInTheDocument();
  });
});
