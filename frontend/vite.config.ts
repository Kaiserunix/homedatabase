import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "path";

// 双入口：index.html = 手机端 PWA；admin.html = 桌面端管理界面
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": resolve(__dirname, "src") },
  },
  build: {
    rollupOptions: {
      input: {
        main: resolve(__dirname, "index.html"),
        admin: resolve(__dirname, "admin.html"),
      },
      output: {
        // vendor 分包：id 统一为正斜杠后按 node_modules 包名匹配（兼容 Windows 反斜杠路径）
        manualChunks(id) {
          const p = id.replace(/\\/g, "/");
          if (!p.includes("node_modules")) return;
          if (/node_modules\/(react|react-dom|react-router|react-router-dom|scheduler|@remix-run\/router)\//.test(p)) {
            return "vendor-react";
          }
          if (p.includes("node_modules/@radix-ui/")) return "vendor-radix";
          if (p.includes("node_modules/lucide-react/")) return "vendor-lucide";
        },
      },
    },
  },
  server: {
    port: 5200,
    strictPort: true,
    // 本地开发保持生产同源语义：/api 反代到后端，Cookie/Passkey Origin 不走跨域
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});
