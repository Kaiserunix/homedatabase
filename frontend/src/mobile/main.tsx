/// <reference types="vite/client" />
import React from "react";
import ReactDOM from "react-dom/client";
import { HashRouter } from "react-router-dom";
import MobileApp from "./App";
import "@/index.css";

// PWA：生产环境注册 Service Worker；开发环境注销并清缓存，避免干扰热更新
if ("serviceWorker" in navigator) {
  if (import.meta.env.PROD) {
    window.addEventListener("load", () => {
      navigator.serviceWorker.register("/sw.js").catch(() => {
        // 注册失败不影响正常使用
      });
    });
  } else {
    navigator.serviceWorker.getRegistrations().then((registrations) => {
      registrations.forEach((r) => r.unregister());
    });
    if ("caches" in window) {
      caches.keys().then((keys) => keys.forEach((k) => caches.delete(k)));
    }
  }
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <HashRouter>
      <MobileApp />
    </HashRouter>
  </React.StrictMode>,
);
