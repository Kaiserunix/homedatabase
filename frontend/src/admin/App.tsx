import { Suspense, lazy } from "react";
import { NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import {
  ArrowLeftRight, BookOpenCheck, FileUp, LayoutDashboard, Landmark, Lock,
  LockKeyhole, LogOut, Palette, Shapes, ShieldCheck, Smartphone, UsersRound, FileSpreadsheet, DatabaseBackup,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Particles } from "@/components/Particles";
import { RouteErrorBoundary } from "@/components/error";
import { PageSkeleton } from "@/components/skeleton";
import { AuthProvider, RequireAuth, useAuth } from "@/auth/AuthContext";
import { LoginPage } from "@/components/LoginPage";
import { useApiQuery } from "@/api/useApi";
import { listImportBatches, listSplits } from "@/api/resources";

// 路由级代码分割：页面组件懒加载，chunk 加载失败由 RouteErrorBoundary 兜底
const DashboardPage = lazy(() => import("./pages/Dashboard"));
const TxnsPage = lazy(() => import("./pages/Txns"));
const CategoriesPage = lazy(() => import("./pages/Categories"));
const AccountsPage = lazy(() => import("./pages/Accounts"));
const ImportsPage = lazy(() => import("./pages/Imports"));
const ImportDetailPage = lazy(() => import("./pages/ImportDetail"));
const SplitsPage = lazy(() => import("./pages/Splits"));
const ReportsPage = lazy(() => import("./pages/Reports"));
const BackupsPage = lazy(() => import("./pages/Backups"));
const LocksPage = lazy(() => import("./pages/Locks"));
const MembersPage = lazy(() => import("./pages/Members"));
const SecurityPage = lazy(() => import("./pages/Security"));
const AdminThemePage = lazy(() => import("./pages/Theme"));

const nav = [
  { to: "/", label: "仪表盘", icon: LayoutDashboard, end: true },
  { to: "/txns", label: "账目与分录", icon: BookOpenCheck },
  { to: "/categories", label: "分类管理", icon: Shapes },
  { to: "/accounts", label: "账户", icon: Landmark },
  { to: "/imports", label: "导入与确认", icon: FileUp },
  { to: "/splits", label: "分摊", icon: ArrowLeftRight },
  { to: "/reports", label: "报表导出", icon: FileSpreadsheet },
  { to: "/backups", label: "备份与恢复", icon: DatabaseBackup },
  { to: "/locks", label: "期间锁定", icon: Lock },
  { to: "/members", label: "成员与权限", icon: UsersRound },
  { to: "/security", label: "安全", icon: ShieldCheck },
  { to: "/theme", label: "外观", icon: Palette },
];

/** 桌面端管理界面外壳：左侧固定导航 + 内容区（仅登录管理员可见） */
function AdminShell() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const { member, signOut } = useAuth();
  // 角标实时来自 API：待确认导入批次数 + 待我处理的分摊数
  const { data: batches } = useApiQuery(() => listImportBatches(), []);
  const { data: splitList } = useApiQuery(() => listSplits(), []);
  const reviewing = (batches ?? []).filter((b) => b.status === "reviewing").length;
  const openSplits = (splitList ?? []).filter(
    (s) =>
      s.status === "open" &&
      s.shares.some((sh) => sh.memberId === member?.id && sh.status === "pending"),
  ).length;
  const badge: Record<string, number> = { "/imports": reviewing, "/splits": openSplits };

  const doSignOut = async () => {
    await signOut();
    navigate("/login", { replace: true });
  };

  return (
    <div className="flex min-h-screen">
      <Particles density={32000} max={56} />
      <aside className="glass fixed inset-y-0 left-0 z-40 flex w-60 flex-col rounded-none border-y-0 border-l-0">
        <div className="flex items-center gap-2.5 px-5 py-5">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-b from-accent to-accent-strong text-white shadow-[inset_0_1px_0_rgba(255,255,255,0.25),0_4px_10px_-3px_rgb(var(--accent)/0.5)]">
            <LockKeyhole className="h-4 w-4" />
          </span>
          <div>
            <p className="text-sm font-semibold text-ink">家庭账本</p>
            <p className="text-[11px] text-ink-faint">管理台</p>
          </div>
        </div>
        <nav className="flex-1 space-y-0.5 overflow-y-auto px-3">
          {nav.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-2.5 rounded-xl px-3 py-2 text-sm transition-all",
                  isActive
                    ? "bg-white font-medium text-ink shadow-glass ring-1 ring-slate-900/5"
                    : "text-ink-soft hover:bg-white/60 hover:text-ink",
                )
              }
            >
              <Icon className="h-4 w-4 shrink-0" />
              <span className="flex-1">{label}</span>
              {!!badge[to] && (
                <span
                  key={badge[to]}
                  className="rounded-full bg-accent-soft px-1.5 py-0.5 text-[10px] font-semibold text-accent-ink motion-safe:animate-pop"
                >
                  {badge[to]}
                </span>
              )}
            </NavLink>
          ))}
        </nav>
        <div className="space-y-0.5 border-t border-slate-900/5 p-3">
          {member && (
            <p className="px-3 pb-1 text-[11px] text-ink-faint">
              当前登录：<span className="text-ink-soft">{member.name}</span>
            </p>
          )}
          <button
            type="button"
            onClick={() => void doSignOut()}
            className="flex w-full items-center gap-2.5 rounded-xl px-3 py-2 text-sm text-ink-soft transition hover:bg-white/60 hover:text-ink"
          >
            <LogOut className="h-4 w-4" /> 退出登录
          </button>
          <a
            href="/index.html"
            className="flex items-center gap-2.5 rounded-xl px-3 py-2 text-sm text-ink-soft hover:bg-white/60"
          >
            <Smartphone className="h-4 w-4" /> 打开手机端
          </a>
        </div>
      </aside>
      <main className="ml-60 flex-1 px-8 py-6">
        <div className="mx-auto max-w-5xl">
          {/* 以 pathname 为 key，切页时容器重挂载，触发轻微上浮进场 */}
          <div key={pathname} className="motion-safe:animate-fade-up">
            <RouteErrorBoundary>
              {/* fallback 用整页骨架，chunk 到位后内容直接替换，容器 fade-up 只播一次不双跳 */}
              <Suspense fallback={<PageSkeleton />}>
                <Routes>
                <Route path="/" element={<DashboardPage />} />
                <Route path="/txns" element={<TxnsPage />} />
                <Route path="/categories" element={<CategoriesPage />} />
                <Route path="/accounts" element={<AccountsPage />} />
                <Route path="/imports" element={<ImportsPage />} />
                <Route path="/imports/:batchId" element={<ImportDetailPage />} />
                <Route path="/splits" element={<SplitsPage />} />
                <Route path="/reports" element={<ReportsPage />} />
                <Route path="/backups" element={<BackupsPage />} />
                <Route path="/locks" element={<LocksPage />} />
                <Route path="/members" element={<MembersPage />} />
                <Route path="/security" element={<SecurityPage />} />
                <Route path="/theme" element={<AdminThemePage />} />
                </Routes>
              </Suspense>
            </RouteErrorBoundary>
          </div>
        </div>
      </main>
    </div>
  );
}

export default function AdminApp() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route
          path="/*"
          element={
            <RequireAuth requireAdmin>
              <AdminShell />
            </RequireAuth>
          }
        />
      </Routes>
    </AuthProvider>
  );
}
