import { useState } from "react";
import { Fingerprint, ShieldCheck, Trash2 } from "lucide-react";
import { Button, Card } from "@/components/ui";
import { Dialog, DialogContent, DialogHeader } from "@/components/dialog";
import { Skeleton } from "@/components/skeleton";
import { ErrorState } from "@/components/error";
import { useApiQuery } from "@/api/useApi";
import { deletePasskey, listPasskeys } from "@/api/resources";
import { registerPasskey } from "@/api/auth";
import { ApiError } from "@/api/client";
import { fmtDate, fmtDateTime } from "@/lib/utils";
import { AdminHeader } from "../components/AdminHeader";

const inputCls = "glass h-10 w-full rounded-xl px-3 text-sm text-ink outline-none placeholder:text-ink-faint";

const errMsg = (e: unknown) =>
  e instanceof ApiError ? e.message : e instanceof Error ? e.message : "操作失败，请稍后重试";

/** Passkey 列表行骨架 */
function PasskeyRowsSkeleton({ rows = 2 }: { rows?: number }) {
  return (
    <div className="space-y-2.5" aria-busy="true" aria-label="加载中">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="flex items-center gap-3">
          <Skeleton className="h-8 w-8 shrink-0 rounded-lg" />
          <div className="min-w-0 flex-1 space-y-1.5">
            <Skeleton className="h-3.5 w-32" />
            <Skeleton className="h-3 w-48" />
          </div>
          <Skeleton className="h-7 w-7 shrink-0 rounded-lg" />
        </div>
      ))}
    </div>
  );
}

export default function SecurityPage() {
  const { data: keys, loading, error, retry } = useApiQuery(() => listPasskeys(), []);
  const [deleteBusyId, setDeleteBusyId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [registerOpen, setRegisterOpen] = useState(false);
  const [registerName, setRegisterName] = useState("");
  const [registerBusy, setRegisterBusy] = useState(false);
  const [registerError, setRegisterError] = useState<string | null>(null);

  const doDelete = async (id: string) => {
    setDeleteBusyId(id);
    setActionError(null);
    try {
      await deletePasskey(id);
      retry();
    } catch (e) {
      setActionError(errMsg(e));
    } finally {
      setDeleteBusyId(null);
    }
  };

  const doRegister = async () => {
    const name = registerName.trim();
    if (!name) {
      setRegisterError("请给这个 Passkey 起个名字");
      return;
    }
    setRegisterBusy(true);
    setRegisterError(null);
    try {
      await registerPasskey(name);
      setRegisterOpen(false);
      setRegisterName("");
      retry();
    } catch (e) {
      // WebAuthn 取消 / 环境不支持 / 后端错误都如实展示
      setRegisterError(errMsg(e));
    } finally {
      setRegisterBusy(false);
    }
  };

  return (
    <>
      <AdminHeader title="安全" desc="Passkey 是主要登录方式，私钥不出设备" />

      <div className="grid grid-cols-2 gap-4">
        <Card className="p-4">
          <p className="mb-3 text-sm font-semibold text-ink">Passkey</p>
          {loading ? (
            <PasskeyRowsSkeleton />
          ) : error ? (
            <ErrorState title="Passkey 加载失败" desc={error.message} onRetry={retry} />
          ) : (
            <div className="space-y-2.5">
              {(keys ?? []).length === 0 && (
                <p className="py-2 text-xs text-ink-faint">还没有注册 Passkey，建议尽快添加。</p>
              )}
              {(keys ?? []).map((k) => (
                <div key={k.id} className="flex items-center gap-3">
                  <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-accent-soft text-accent">
                    <Fingerprint className="h-4 w-4" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm text-ink">{k.name}</p>
                    <p className="text-xs text-ink-faint">
                      创建于 {fmtDate(k.createdAt)} · 最近使用 {fmtDateTime(k.lastUsedAt)}
                    </p>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={deleteBusyId === k.id}
                    aria-label={`删除 ${k.name}`}
                    onClick={() => void doDelete(k.id)}
                  >
                    <Trash2 className="h-4 w-4 text-ink-faint" />
                  </Button>
                </div>
              ))}
            </div>
          )}
          {actionError && (
            <p role="alert" className="mt-2 rounded-xl bg-danger/10 px-3 py-2 text-xs text-danger">
              {actionError}
            </p>
          )}
          <Button className="mt-3 w-full" onClick={() => { setRegisterOpen(true); setRegisterError(null); }}>
            注册新 Passkey
          </Button>
          <p className="mt-2 text-xs leading-5 text-ink-faint">
            基于 WebAuthn 标准，私钥不出设备，登录与敏感操作都需设备生物识别确认。
            删除后该设备将无法再用此 Passkey 登录，请确认还有其他登录方式。
          </p>
        </Card>

        <Card className="p-4">
          <p className="mb-3 text-sm font-semibold text-ink">关于登录安全</p>
          <ul className="space-y-2 text-xs leading-5 text-ink-soft">
            <li className="flex gap-2">
              <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-accent" />
              登录会话由服务端 HttpOnly Cookie 维持，页面不保存任何令牌；离开或会话过期后需重新验证。
            </li>
            <li className="flex gap-2">
              <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-accent" />
              Passkey 需要 HTTPS 或 localhost 环境，注册与登录都会唤起系统的生物识别。
            </li>
            <li className="flex gap-2">
              <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-accent" />
              密码登录仅作兜底；如遗忘登录方式，请联系管理员在服务端重置。
            </li>
          </ul>
        </Card>
      </div>

      {/* 注册新 Passkey */}
      <Dialog open={registerOpen} onOpenChange={setRegisterOpen}>
        <DialogContent>
          <DialogHeader title="注册新 Passkey" desc="将唤起系统生物识别，完成后与本账户绑定" />
          <div className="space-y-3">
            <label className="block">
              <span className="mb-1 block text-xs text-ink-faint">名称（用于区分设备）</span>
              <input
                value={registerName}
                onChange={(e) => setRegisterName(e.target.value)}
                placeholder="例如：家里台式机"
                className={inputCls}
                autoFocus
              />
            </label>
            {registerError && (
              <p role="alert" className="rounded-xl bg-danger/10 px-3 py-2 text-xs text-danger">
                {registerError}
              </p>
            )}
            <div className="flex justify-end gap-2 pt-1">
              <Button variant="secondary" onClick={() => setRegisterOpen(false)}>取消</Button>
              <Button disabled={registerBusy} onClick={() => void doRegister()}>
                {registerBusy ? "等待设备验证…" : "开始注册"}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
