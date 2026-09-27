import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { resolve } from "path";

// 单测配置：与 vite.config.ts 保持同一套插件与 @ 别名；e2e 归 playwright 管
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": resolve(__dirname, "src") },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./tests/setup.ts"],
    include: ["tests/unit/**/*.test.{ts,tsx}"],
    // jsdom 默认 about:blank 是不透明源，localStorage 不可用；给个正常源
    environmentOptions: {
      jsdom: { url: "http://localhost:5200/" },
    },
    coverage: {
      provider: "v8",
      include: ["src/**/*.{ts,tsx}"],
    },
  },
});
