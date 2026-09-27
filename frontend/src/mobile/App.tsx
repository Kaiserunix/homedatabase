import { Suspense, lazy, useEffect, useRef } from "react";
import { Routes, Route, useLocation, useNavigationType } from "react-router-dom";
import { BottomNav } from "./components/BottomNav";
import { AuthProvider, RequireAuth } from "@/auth/AuthContext";
import { LoginPage } from "@/components/LoginPage";
import { Particles } from "@/components/Particles";
import { RouteErrorBoundary } from "@/components/error";
import { PageSkeleton } from "@/components/skeleton";

// 路由级代码分割：页面组件懒加载，chunk 加载失败由 RouteErrorBoundary 兜底
const HomePage = lazy(() => import("./pages/Home"));
const AddPage = lazy(() => import("./pages/Add"));
const TxnsPage = lazy(() => import("./pages/Txns"));
const SplitsPage = lazy(() => import("./pages/Splits"));
const MePage = lazy(() => import("./pages/Me"));
const ImportPage = lazy(() => import("./pages/Import"));
const AccountsPage = lazy(() => import("./pages/Accounts"));
const SecurityPage = lazy(() => import("./pages/Security"));
const ThemePage = lazy(() => import("./pages/Theme"));

/** 路径深度："/me/import" → 2，"/txns" → 1 */
const depthOf = (pathname: string) => pathname.split("/").filter(Boolean).length;

/** 手机端外壳：单列窄屏 + 底部导航（记一笔为中央凸起按钮） */
export default function MobileApp() {
  const { pathname } = useLocation();
  const navType = useNavigationType();
  // 记一笔全屏沉浸；登录页不显示底部导航
  const hideNav = pathname === "/add" || pathname === "/login";

  // 页面层级方向感：进入更深层级右滑进入，返回浅层左滑进入，同级仅轻微淡入
  const prevDepth = useRef(depthOf(pathname));
  const depth = depthOf(pathname);
  let enterCls = "motion-safe:animate-fade-in";
  if (depth > prevDepth.current) enterCls = "motion-safe:animate-slide-in-right";
  else if (navType === "POP" && depth < prevDepth.current) enterCls = "motion-safe:animate-slide-in-left";
  useEffect(() => {
    prevDepth.current = depth;
  }, [depth]);

  return (
    <AuthProvider>
      <div className="mx-auto min-h-dvh max-w-md">
        <Particles />
        <main className={hideNav ? "" : "pb-[calc(5rem+env(safe-area-inset-bottom))]"}>
          {/* 以 pathname 为 key，切页时容器重挂载，按层级方向选择进场动画 */}
          <div key={pathname} className={enterCls}>
            <RouteErrorBoundary>
              {/* fallback 与页面同形（骨架屏），chunk 到位后由页面自身 stagger 进场接管，避免双跳动 */}
              <Suspense fallback={<PageSkeleton />}>
                <Routes>
                  <Route path="/login" element={<LoginPage />} />
                  <Route path="/" element={<RequireAuth><HomePage /></RequireAuth>} />
                  <Route path="/add" element={<RequireAuth><AddPage /></RequireAuth>} />
                  <Route path="/txns" element={<RequireAuth><TxnsPage /></RequireAuth>} />
                  <Route path="/splits" element={<RequireAuth><SplitsPage /></RequireAuth>} />
                  <Route path="/me" element={<RequireAuth><MePage /></RequireAuth>} />
                  <Route path="/me/import" element={<RequireAuth><ImportPage /></RequireAuth>} />
                  <Route path="/me/accounts" element={<RequireAuth><AccountsPage /></RequireAuth>} />
                  <Route path="/me/security" element={<RequireAuth><SecurityPage /></RequireAuth>} />
                  <Route path="/me/theme" element={<RequireAuth><ThemePage /></RequireAuth>} />
                </Routes>
              </Suspense>
            </RouteErrorBoundary>
          </div>
        </main>
        {!hideNav && <BottomNav />}
      </div>
    </AuthProvider>
  );
}
