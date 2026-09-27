/**
 * 登录页（手机端 / 管理端共用）：Passkey 优先，密码兜底。
 * 成功后将 AuthSession 写入 AuthContext 并跳回来源页（location.state.from）。
 * 所有错误（含 WebAuthn 取消、后端 401/403）原样内联展示，不做假成功。
 */
import { useState } from "react";
import type { FormEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { ChevronDown, Fingerprint, KeyRound } from "lucide-react";
import { loginWithPasskey, loginWithPassword } from "@/api/auth";
import { useAuth } from "@/auth/AuthContext";
import { Button, Card } from "@/components/ui";
import { Particles } from "@/components/Particles";
import { cn } from "@/lib/utils";

const inputCls =
  "glass h-10 w-full rounded-xl px-3 text-sm text-ink outline-none placeholder:text-ink-faint";

const errorText = (err: unknown) =>
  err instanceof Error ? err.message : "操作失败，请重试";

export function LoginPage() {
  const { signIn } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from ?? "/";

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [pending, setPending] = useState<"passkey" | "password" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const finish = (session: Parameters<typeof signIn>[0]) => {
    signIn(session);
    navigate(from, { replace: true });
  };

  const onPasskey = async () => {
    setError(null);
    setPending("passkey");
    try {
      finish(await loginWithPasskey(username.trim()));
    } catch (err) {
      setError(errorText(err));
    } finally {
      setPending(null);
    }
  };

  const onPassword = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setPending("password");
    try {
      finish(await loginWithPassword(username.trim(), password));
    } catch (err) {
      setError(errorText(err));
    } finally {
      setPending(null);
    }
  };

  const busy = pending !== null;

  return (
    <div className="relative flex min-h-dvh items-center justify-center px-4">
      <Particles />
      <Card strong className="w-full max-w-sm space-y-5 p-6">
        <div className="space-y-1 text-center">
          <h1 className="text-lg font-semibold text-ink">家庭账本</h1>
          <p className="text-xs text-ink-faint">登录后同步全家的账目</p>
        </div>

        <label className="block space-y-1.5">
          <span className="text-xs font-medium text-ink-soft">用户名</span>
          <input
            className={inputCls}
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="dad / mom / son"
            autoComplete="username webauthn"
            autoFocus
          />
        </label>

        <div className="space-y-2">
          <Button
            className="w-full"
            size="lg"
            disabled={!username.trim() || busy}
            onClick={onPasskey}
          >
            <Fingerprint className="h-4 w-4" />
            {pending === "passkey" ? "正在验证…" : "使用 Passkey 登录"}
          </Button>
          <p className="text-center text-[11px] leading-relaxed text-ink-faint">
            Passkey 需要 HTTPS 或 localhost 环境，并使用支持系统生物识别的浏览器。
          </p>
        </div>

        <div className="space-y-2">
          <button
            type="button"
            className="flex w-full items-center justify-center gap-1 text-xs text-ink-soft hover:text-ink"
            onClick={() => setShowPassword((v) => !v)}
            aria-expanded={showPassword}
          >
            <KeyRound className="h-3.5 w-3.5" />
            密码登录
            <ChevronDown
              className={cn("h-3.5 w-3.5 transition-transform", showPassword && "rotate-180")}
            />
          </button>
          {showPassword && (
            <form onSubmit={onPassword} className="space-y-2">
              <input
                className={inputCls}
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="密码"
                autoComplete="current-password"
              />
              <Button
                type="submit"
                variant="secondary"
                className="w-full"
                disabled={!username.trim() || !password || busy}
              >
                {pending === "password" ? "正在登录…" : "登录"}
              </Button>
            </form>
          )}
        </div>

        {error && (
          <p role="alert" className="rounded-xl bg-danger/10 px-3 py-2 text-xs text-danger">
            {error}
          </p>
        )}
      </Card>
    </div>
  );
}
