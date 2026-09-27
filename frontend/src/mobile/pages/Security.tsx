import { useState, type CSSProperties } from "react";
import { KeyRound, Plus, ShieldCheck, Trash2 } from "lucide-react";
import { PageHeader } from "../components/PageHeader";
import { Button, Card, SectionTitle } from "@/components/ui";
import { Skeleton } from "@/components/skeleton";
import { ErrorState } from "@/components/error";
import { useApiQuery } from "@/api/useApi";
import { deletePasskey, listPasskeys } from "@/api/resources";
import { registerPasskey } from "@/api/auth";
import { ApiError } from "@/api/client";
import { fmtDate, fmtDateTime } from "@/lib/utils";

const errText = (e: unknown) =>
  e instanceof ApiError ? e.message : e instanceof Error ? e.message : "操作失败，请重试";

export default function SecurityPage() {
  const { data: passkeysData, loading, error, retry } = useApiQuery(() => listPasskeys(), []);
  const [deleting, setDeleting] = useState<Record<string, boolean>>({});
  const [actionError, setActionError] = useState<string | null>(null);
  // 添加 Passkey：先填设备名再唤起系统录入
  const [adding, setAdding] = useState(false);
  const [newName, setNewName] = useState("");
  const [registering, setRegistering] = useState(false);

  const passkeys = passkeysData ?? [];

  const onDelete = async (id: string, name: string) => {
    setActionError(null);
    setDeleting((m) => ({ ...m, [id]: true }));
    try {
      await deletePasskey(id);
      retry();
    } catch (e) {
      // 失败保留列表项并展示真实错误
      setActionError(`删除「${name}」失败：${errText(e)}`);
    } finally {
      setDeleting((m) => {
        const next = { ...m };
        delete next[id];
        return next;
      });
    }
  };

  const onAdd = async () => {
    const name = newName.trim();
    if (!name || registering) return;
    setActionError(null);
    setRegistering(true);
    try {
      await registerPasskey(name);
      setAdding(false);
      setNewName("");
      retry();
    } catch (e) {
      // WebAuthn 取消 / 环境不支持 / 后端校验失败：原样展示
      setActionError(errText(e));
    } finally {
      setRegistering(false);
    }
  };

  return (
    <div className="px-4">
      <PageHeader back title="安全" />

      <SectionTitle title="Passkey" />
      <Card className="p-4">
        <p className="text-xs text-ink-soft">
          Passkey 是主要登录方式，用指纹 / 面容即可登录，无需记密码。
        </p>
        {/* 列表区：骨架 / 错误 / 内容互斥渲染 */}
        {loading ? (
          <div className="mt-3 space-y-3" aria-busy="true" aria-label="加载中">
            {[0, 1].map((i) => (
              <div key={i} className="flex items-center gap-3 py-1">
                <Skeleton className="h-9 w-9 shrink-0 rounded-full" />
                <div className="min-w-0 flex-1 space-y-2">
                  <Skeleton className="h-3.5 w-2/5" />
                  <Skeleton className="h-3 w-3/5" />
                </div>
              </div>
            ))}
          </div>
        ) : error ? (
          <ErrorState title="Passkey 加载失败" desc={error.message} onRetry={retry} className="py-4" />
        ) : (
        <div className="stagger mt-3 divide-y divide-slate-900/5">
          {passkeys.map((p, i) => (
            <div
              key={p.id}
              className="flex items-center gap-3 py-2.5 first:pt-0 last:pb-0"
              style={{ "--stagger-i": i } as CSSProperties}
            >
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-accent-soft text-accent-ink">
                <KeyRound className="h-4 w-4" />
              </span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm text-ink">{p.name}</p>
                <p className="text-xs text-ink-faint">
                  {fmtDate(p.createdAt)} 创建 · 最近使用 {fmtDateTime(p.lastUsedAt)}
                </p>
              </div>
              <Button
                variant="ghost"
                size="sm"
                aria-label={`删除 ${p.name}`}
                disabled={!!deleting[p.id]}
                onClick={() => void onDelete(p.id, p.name)}
              >
                <Trash2 className="h-4 w-4 text-danger" />
              </Button>
            </div>
          ))}
          {passkeys.length === 0 && (
            <p className="py-2 text-xs text-ink-faint">还没有 Passkey，请添加一个。</p>
          )}
        </div>
        )}

        {adding ? (
          <div className="mt-3 space-y-2">
            <input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="设备名称，例如：我的 iPhone"
              autoFocus
              className="glass h-10 w-full rounded-xl px-3 text-sm text-ink outline-none placeholder:text-ink-faint"
            />
            <div className="flex gap-2">
              <Button
                variant="secondary"
                size="sm"
                className="flex-1"
                disabled={registering}
                onClick={() => {
                  setAdding(false);
                  setNewName("");
                }}
              >
                取消
              </Button>
              <Button
                size="sm"
                className="flex-1"
                disabled={!newName.trim() || registering}
                onClick={() => void onAdd()}
              >
                {registering ? "等待系统验证…" : "开始录入"}
              </Button>
            </div>
            <p className="text-[11px] text-ink-faint">
              点击「开始录入」后会调起系统指纹 / 面容验证。
            </p>
          </div>
        ) : (
          <Button variant="secondary" size="sm" className="mt-3" onClick={() => setAdding(true)}>
            <Plus className="h-4 w-4" />
            添加 Passkey
          </Button>
        )}
        {actionError && (
          <p role="alert" className="mt-2 rounded-xl bg-danger/10 px-3 py-2 text-xs text-danger">
            {actionError}
          </p>
        )}
      </Card>

      <SectionTitle title="兜底方式" className="mt-6" />
      <Card className="px-4">
        <div className="flex items-center justify-between py-3.5">
          <div>
            <p className="text-sm text-ink">密码</p>
            <p className="mt-0.5 text-xs text-ink-faint">已设置，仅作备用登录</p>
          </div>
        </div>
      </Card>

      <Card className="mt-6 flex gap-2 p-4">
        <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-accent" />
        <p className="text-xs leading-relaxed text-ink-soft">
          建议至少保留一个可用的 Passkey；丢失设备时可用密码登录后重新添加。
        </p>
      </Card>
    </div>
  );
}
