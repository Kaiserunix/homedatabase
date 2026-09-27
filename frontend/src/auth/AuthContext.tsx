/**
 * 会话上下文：HttpOnly Cookie 会话，前端不持有令牌。
 * 启动时 fetchMe() 判定登录态：200 → authed；401 → anon（跳登录）；
 * 其他错误（网络/5xx）也按 anon 处理，但保留错误对象供页面展示。
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { ShieldBan } from "lucide-react";
import { ApiError, onSessionExpired } from "@/api/client";
import { fetchMe, logout } from "@/api/resources";
import type { AuthSession, Member } from "@/data/types";
import { PageSkeleton } from "@/components/skeleton";
import { ErrorState } from "@/components/error";

export interface AuthState {
  status: "loading" | "authed" | "anon";
  member: Member | null;
  expiresAt: string | null;
  /** fetchMe 的非 401 错误（如网络异常），供页面提示；401 属正常未登录，不记 */
  error: ApiError | null;
}

export interface AuthContextValue extends AuthState {
  /** 登录成功（密码或 Passkey 校验通过）后写入会话 */
  signIn: (session: AuthSession) => void;
  /** 调后端登出（清 Cookie）后回到 anon；登出请求失败也照常清空本地态 */
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({
    status: "loading",
    member: null,
    expiresAt: null,
    error: null,
  });

  useEffect(() => {
    let alive = true;
    fetchMe()
      .then((member) => {
        if (!alive) return;
        setState({ status: "authed", member, expiresAt: null, error: null });
      })
      .catch((err: unknown) => {
        if (!alive) return;
        // 401 = 未登录，属正常分支；其余错误保留下来便于展示真实原因
        const error = err instanceof ApiError && err.status === 401 ? null : (err as ApiError);
        setState({ status: "anon", member: null, expiresAt: null, error });
      });
    return () => {
      alive = false;
    };
  }, []);

  const signIn = useCallback((session: AuthSession) => {
    setState({ status: "authed", member: session.member, expiresAt: session.expiresAt, error: null });
  }, []);

  // 页面打开后任何 API 返回 401：全局会话立即转 anon，RequireAuth 随之跳登录页
  // （state.from 保留当前路径，登录后原路返回）
  useEffect(
    () =>
      onSessionExpired(() => {
        setState((prev) =>
          prev.status === "authed"
            ? { status: "anon", member: null, expiresAt: null, error: null }
            : prev,
        );
      }),
    [],
  );

  const signOut = useCallback(async () => {
    try {
      await logout();
    } finally {
      setState({ status: "anon", member: null, expiresAt: null, error: null });
    }
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({ ...state, signIn, signOut }),
    [state, signIn, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth 必须在 <AuthProvider> 内使用");
  return ctx;
}

/** 路由守卫：未登录跳 /login（state.from 记录目标页，登录后跳回）；requireAdmin 时非管理员展示 403 权限错误（不跳转） */
export function RequireAuth({
  children,
  requireAdmin = false,
}: {
  children: ReactNode;
  requireAdmin?: boolean;
}) {
  const { status, member } = useAuth();
  const location = useLocation();

  if (status === "loading") return <PageSkeleton />;
  if (status === "anon") {
    return (
      <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />
    );
  }
  if (requireAdmin && !member?.isAdmin) {
    return (
      <div className="flex min-h-[50vh] items-center justify-center px-4">
        <ErrorState
          icon={<ShieldBan className="h-9 w-9" />}
          title="没有访问权限（403）"
          desc="这个页面需要管理员权限，请使用管理员账号（爸爸）登录后再试。"
        />
      </div>
    );
  }
  return <>{children}</>;
}
