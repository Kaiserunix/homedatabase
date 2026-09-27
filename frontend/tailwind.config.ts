import type { Config } from "tailwindcss";

// 设计基调：白色、克制的玻璃感。不做深色模式。
export default {
  content: ["./index.html", "./admin.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          DEFAULT: "#0f172a", // 主文字
          soft: "#475569", // 次要文字
          faint: "#94a3b8", // 弱提示
        },
        accent: {
          // 由 CSS 变量驱动（src/index.css 的 [data-theme] 定义），支持外观设置切换
          DEFAULT: "rgb(var(--accent) / <alpha-value>)",
          soft: "rgb(var(--accent-soft) / <alpha-value>)",
          faint: "rgb(var(--accent-faint) / <alpha-value>)",
          strong: "rgb(var(--accent-strong) / <alpha-value>)", // 悬停深色
          ink: "rgb(var(--accent-ink) / <alpha-value>)", // 浅底上的深字
        },
        danger: "#e11d48",
        warn: "#d97706",
      },
      fontFamily: {
        sans: [
          "-apple-system", "BlinkMacSystemFont", "Segoe UI", "PingFang SC",
          "Hiragino Sans GB", "Microsoft YaHei", "sans-serif",
        ],
        num: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
      boxShadow: {
        glass: "0 1px 2px rgba(15,23,42,0.04), 0 8px 24px -12px rgba(15,23,42,0.12)",
        pop: "0 4px 6px -2px rgba(15,23,42,0.05), 0 16px 40px -16px rgba(15,23,42,0.18)",
      },
      borderRadius: {
        xl2: "1.25rem",
      },
      // 动效：时长克制在 150–300ms。关键帧定义在 src/index.css 的
      // (prefers-reduced-motion: no-preference) 媒体查询里；
      // 使用这些工具类时一律加 motion-safe: 前缀（如 motion-safe:animate-fade-up），
      // Radix 状态类则叠加 data-[state=...] 变体。
      animation: {
        "fade-in": "fade-in 200ms ease-out",
        "fade-up": "fade-up 250ms cubic-bezier(0.16, 1, 0.3, 1)",
        "fade-out": "fade-out 200ms ease-in forwards",
        "sheet-up": "sheet-up 300ms cubic-bezier(0.16, 1, 0.3, 1)",
        "sheet-down": "sheet-down 200ms ease-in forwards",
        pop: "pop 250ms cubic-bezier(0.34, 1.56, 0.64, 1) both",
        overlay: "overlay-in 200ms ease-out",
        "overlay-out": "overlay-out 150ms ease-in forwards",
        "dialog-in": "dialog-in 200ms cubic-bezier(0.16, 1, 0.3, 1)",
        "dialog-out": "dialog-out 150ms ease-in forwards",
        "collapse-out": "collapse-out 250ms ease-in forwards",
        "slide-in-right": "slide-in-right 250ms cubic-bezier(0.16, 1, 0.3, 1)",
        "slide-in-left": "slide-in-left 250ms cubic-bezier(0.16, 1, 0.3, 1)",
        "fade-swap": "fade-swap 200ms ease-out",
        "value-pop": "value-pop 250ms ease-out",
        "select-in": "select-in 150ms ease-out",
      },
    },
  },
  plugins: [],
} satisfies Config;
