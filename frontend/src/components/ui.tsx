import * as React from "react";
import { cn } from "@/lib/utils";
import { centsSign, formatCentsGrouped } from "@/lib/decimal";

/** 基础视觉组件：按钮、卡片、徽章等。风格统一：白色、克制玻璃感。 */

export const Button = React.forwardRef<
  HTMLButtonElement,
  React.ButtonHTMLAttributes<HTMLButtonElement> & {
    variant?: "primary" | "secondary" | "ghost" | "danger";
    size?: "sm" | "md" | "lg";
  }
>(function Button({ className, variant = "primary", size = "md", ...props }, ref) {
  return (
    <button
      ref={ref}
      className={cn(
        "inline-flex items-center justify-center gap-1.5 rounded-xl font-medium transition active:scale-[0.98] disabled:pointer-events-none disabled:opacity-50",
        size === "sm" && "h-8 px-3 text-xs",
        size === "md" && "h-10 px-4 text-sm",
        size === "lg" && "h-12 px-5 text-base",
        variant === "primary" &&
          "bg-gradient-to-b from-accent to-accent-strong text-white shadow-[inset_0_1px_0_rgba(255,255,255,0.22),0_4px_14px_-4px_rgb(var(--accent)/0.5)] hover:brightness-[1.04]",
        variant === "secondary" && "glass text-ink hover:bg-white/90",
        variant === "ghost" && "text-ink-soft hover:bg-slate-900/5",
        variant === "danger" && "bg-danger/10 text-danger hover:bg-danger/15",
        className,
      )}
      {...props}
    />
  );
});

export function Card({
  className,
  strong,
  ...props
}: React.HTMLAttributes<HTMLDivElement> & { strong?: boolean }) {
  return (
    <div
      className={cn(strong ? "glass-strong" : "glass", "rounded-2xl", className)}
      {...props}
    />
  );
}

export function Badge({
  className,
  tone = "neutral",
  ...props
}: React.HTMLAttributes<HTMLSpanElement> & {
  tone?: "neutral" | "accent" | "danger" | "warn" | "ok";
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset",
        tone === "neutral" && "bg-slate-900/5 text-ink-soft ring-slate-900/10",
        tone === "accent" && "bg-accent-soft text-accent-ink ring-accent/15",
        tone === "danger" && "bg-danger/10 text-danger ring-danger/20",
        tone === "warn" && "bg-warn/10 text-warn ring-warn/20",
        tone === "ok" && "bg-emerald-500/10 text-emerald-700 ring-emerald-600/20",
        className,
      )}
      {...props}
    />
  );
}

export function SectionTitle({
  title,
  action,
  className,
}: {
  title: string;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("mb-2 flex items-center justify-between", className)}>
      <h2 className="text-sm font-semibold text-ink">{title}</h2>
      {action}
    </div>
  );
}

/** 金额显示：支出红、收入青、转账中性。金额用等宽数字，精确十进制格式化（不经浮点）。 */
export function Amount({
  cents,
  kind = "neutral",
  currency = "CNY",
  className,
  signed,
}: {
  cents: number | bigint;
  kind?: "expense" | "income" | "neutral";
  currency?: string;
  className?: string;
  signed?: boolean;
}) {
  const s = formatCentsGrouped(cents).replace(/^-/, "");
  const sym = currency === "CNY" ? "¥" : currency + " ";
  const sign = centsSign(cents);
  // signed：正数 +、负数 -、0 不加符号；kind 语义优先（expense 恒 -、income 按符号 +/-）
  const prefix =
    kind === "expense"
      ? "-"
      : kind === "income"
        ? sign < 0
          ? "-"
          : "+"
        : sign < 0
          ? "-"
          : signed && sign > 0
            ? "+"
            : "";
  return (
    <span
      className={cn(
        "font-num tabular-nums",
        kind === "expense" && "text-ink",
        kind === "income" && "text-accent",
        kind === "neutral" && "text-ink",
        className,
      )}
    >
      {prefix}
      {sym}
      {s}
    </span>
  );
}

export function EmptyState({
  icon,
  title,
  hint,
  action,
}: {
  icon?: React.ReactNode;
  title: string;
  hint?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center gap-2 py-10 text-center">
      {icon && <div className="text-ink-faint">{icon}</div>}
      <p className="text-sm font-medium text-ink-soft">{title}</p>
      {hint && <p className="max-w-60 text-xs text-ink-faint">{hint}</p>}
      {action}
    </div>
  );
}

/** 圆形成员头像（首字） */
export function MemberAvatar({ name, color, size = "md" }: { name: string; color: string; size?: "sm" | "md" }) {
  return (
    <span
      className={cn(
        "inline-flex items-center justify-center rounded-full text-white",
        color,
        size === "sm" ? "h-6 w-6 text-[10px]" : "h-9 w-9 text-sm",
      )}
    >
      {name.slice(0, 1)}
    </span>
  );
}
