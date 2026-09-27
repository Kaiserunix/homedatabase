import { NavLink } from "react-router-dom";
import { ArrowLeftRight, Home, Plus, Receipt, UserRound } from "lucide-react";
import { cn } from "@/lib/utils";
import { useApiQuery } from "@/api/useApi";
import { listSplits } from "@/api/resources";
import { useAuth } from "@/auth/AuthContext";

const items = [
  { to: "/", label: "首页", icon: Home },
  { to: "/txns", label: "流水", icon: Receipt },
  { to: "/splits", label: "分摊", icon: ArrowLeftRight },
  { to: "/me", label: "我的", icon: UserRound },
];

export function BottomNav() {
  const { member } = useAuth();
  const memberId = member?.id ?? null;
  // 待我处理的分摊角标：未登录时不发请求（守卫会跳登录页）
  const { data: splitList } = useApiQuery(
    () => (memberId ? listSplits() : Promise.resolve([])),
    [memberId],
  );
  const pendingSplits = (splitList ?? []).filter(
    (s) =>
      s.status !== "settled" &&
      s.shares.some((sh) => sh.memberId === memberId && sh.status === "pending"),
  ).length;

  return (
    <nav className="glass-strong fixed inset-x-0 bottom-0 z-40 mx-auto max-w-md border-x-0 border-b-0 pb-[env(safe-area-inset-bottom)]">
      <div className="relative grid grid-cols-5">
        {[items[0], items[1]].map(({ to, label, icon: Icon }) => (
          <NavItem key={to} to={to} label={label} Icon={Icon} />
        ))}
        <div className="relative">
          <NavLink
            to="/add"
            aria-label="记一笔"
            className="absolute -top-6 left-1/2 flex h-14 w-14 -translate-x-1/2 items-center justify-center rounded-full bg-gradient-to-b from-accent to-accent-strong text-white shadow-[inset_0_1px_0_rgba(255,255,255,0.25),0_8px_20px_-6px_rgb(var(--accent)/0.55)] ring-4 ring-white/80 transition [animation-delay:120ms] hover:shadow-[0_10px_26px_-6px_rgb(var(--accent)/0.65)] active:scale-95 motion-safe:animate-pop"
          >
            <Plus className="h-7 w-7" />
          </NavLink>
        </div>
        <NavItem to="/splits" label="分摊" Icon={ArrowLeftRight} badge={pendingSplits} />
        <NavItem to="/me" label="我的" Icon={UserRound} />
      </div>
    </nav>
  );
}

function NavItem({
  to, label, Icon, badge,
}: {
  to: string; label: string; Icon: typeof Home; badge?: number;
}) {
  return (
    <NavLink to={to} end={to === "/"} className="relative flex flex-col items-center gap-0.5 py-2.5">
      {({ isActive }) => (
        <>
          <Icon
            key={isActive ? "on" : "off"}
            className={cn("h-5 w-5", isActive ? "text-accent motion-safe:animate-pop" : "text-ink-faint")}
          />
          <span className={cn("text-[10px]", isActive ? "font-medium text-accent" : "text-ink-faint")}>{label}</span>
          {!!badge && (
            <span
              key={badge}
              className="absolute right-[calc(50%-1.1rem)] top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-danger px-1 text-[9px] font-semibold text-white motion-safe:animate-pop"
            >
              {badge}
            </span>
          )}
        </>
      )}
    </NavLink>
  );
}
