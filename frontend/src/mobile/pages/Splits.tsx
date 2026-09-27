import { useState, type CSSProperties } from "react";
import { PageHeader } from "../components/PageHeader";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/tabs";
import { Amount, Badge, Button, Card, EmptyState, MemberAvatar } from "@/components/ui";
import { Dialog, DialogClose, DialogContent, DialogHeader } from "@/components/dialog";
import { Skeleton } from "@/components/skeleton";
import { ErrorState } from "@/components/error";
import { useApiQuery } from "@/api/useApi";
import { listMembers, listSplits, respondToSplit } from "@/api/resources";
import { ApiError } from "@/api/client";
import { useAuth } from "@/auth/AuthContext";
import type { Member, Split, SplitShareStatus } from "@/data/types";
import { fmtDate, cn } from "@/lib/utils";

function ShareBadge({ status }: { status: SplitShareStatus }) {
  if (status === "accepted") return <Badge tone="ok">已接受</Badge>;
  if (status === "returned") return <Badge tone="danger">已退回</Badge>;
  return <Badge tone="neutral">待接受</Badge>;
}

/** 分摊卡骨架：标题两行 + 底部按钮条，2 张 */
function SplitCardsSkeleton() {
  return (
    <div className="space-y-3" aria-busy="true" aria-label="加载中">
      {[0, 1].map((i) => (
        <Card key={i} className="space-y-3 p-4">
          <div className="space-y-2">
            <Skeleton className="h-3.5 w-2/5" />
            <Skeleton className="h-3 w-3/5" />
          </div>
          <div className="flex items-center justify-between">
            <Skeleton className="h-6 w-24 rounded-full" />
            <Skeleton className="h-5 w-16" />
          </div>
          <div className="flex gap-2">
            <Skeleton className="h-9 flex-1" />
            <Skeleton className="h-9 flex-1" />
          </div>
        </Card>
      ))}
    </div>
  );
}

export default function SplitsPage() {
  const { member } = useAuth();
  const meId = member?.id ?? null;
  const { data: splitsData, loading, error, retry } = useApiQuery(() => listSplits(), []);
  const { data: membersData } = useApiQuery(() => listMembers(), []);
  // 写操作成功后用返回的分摊覆盖本地（保持版本号最新，供下次 expectedVersion 使用）
  const [updated, setUpdated] = useState<Record<string, Split>>({});
  const [pending, setPending] = useState<Record<string, boolean>>({});
  const [actionError, setActionError] = useState<string | null>(null);
  const [returnTarget, setReturnTarget] = useState<Split | null>(null);
  const [reason, setReason] = useState("");
  const [closing, setClosing] = useState<Record<string, boolean>>({});
  // 409 写锁：true 期间禁止接受/退回，直到刷新最新分摊列表成功
  const [stale, setStale] = useState(false);

  const memberList: Member[] = membersData ?? [];
  const memberName = (id: string) => memberList.find((m) => m.id === id)?.name ?? "家人";
  const colorOf = (id: string) => memberList.find((m) => m.id === id)?.color ?? "bg-slate-400";

  const splits = (splitsData ?? []).map((s) => updated[s.id] ?? s);
  const todo = splits.filter(
    (s) => s.shares.some((sh) => sh.memberId === meId && sh.status === "pending"),
  );
  const mine = splits.filter((s) => s.createdBy === meId);

  /**
   * 409 冲突：立即关闭退回弹窗、清空理由、丢弃本地覆盖（含 returnTarget 旧快照），
   * 进入写锁；await 拉取最新列表，仅成功应用后解锁并提示重新审阅；
   * GET 失败诚实提示刷新失败、维持写锁，由「重试刷新」重走本函数。自身不抛异常。
   */
  const refreshAfterConflict = async () => {
    setReturnTarget(null);
    setReason("");
    setUpdated({});
    setStale(true);
    try {
      const fresh = await listSplits();
      setUpdated(Object.fromEntries(fresh.map((s) => [s.id, s])));
      setStale(false);
      setActionError("这笔分摊刚被家人修改过，列表已刷新，请重新审阅后再操作。");
    } catch {
      setActionError("这笔分摊刚被家人修改过，但刷新失败。请点击「重试刷新」，成功后再操作。");
    }
  };

  /**
   * 接受 / 退回：携带当前 expectedVersion 提交并等待结果。
   * 成功：用返回的分摊覆盖本地后播收起动画；409：关弹窗、进写锁并强制刷新；
   * 其他错误：展示真实文案，卡片原样保留。
   */
  const decide = async (split: Split, accept: boolean, note?: string): Promise<boolean> => {
    if (stale) return false;
    setActionError(null);
    setPending((p) => ({ ...p, [split.id]: true }));
    try {
      const next = await respondToSplit(split.id, {
        accept,
        expectedVersion: split.version,
        note: note ?? null,
      });
      setUpdated((m) => ({ ...m, [next.id]: next }));
      setClosing((c) => ({ ...c, [split.id]: true }));
      window.setTimeout(() => {
        setClosing((c) => {
          const next2 = { ...c };
          delete next2[split.id];
          return next2;
        });
      }, 240);
      return true;
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        await refreshAfterConflict();
      } else {
        setActionError(e instanceof ApiError ? e.message : "操作失败，请稍后重试");
      }
      return false;
    } finally {
      setPending((p) => {
        const next = { ...p };
        delete next[split.id];
        return next;
      });
    }
  };

  return (
    <div className="px-4">
      <PageHeader title="分摊" />
      {actionError && (
        <p role="alert" className="mb-3 rounded-xl bg-danger/10 px-3 py-2 text-xs text-danger">
          {actionError}
        </p>
      )}
      {stale && (
        <div className="mb-3">
          <Button variant="secondary" size="sm" className="w-full" onClick={() => void refreshAfterConflict()}>
            重试刷新
          </Button>
        </div>
      )}
      <Tabs defaultValue="todo">
        <TabsList className="grid w-full grid-cols-2">
          <TabsTrigger value="todo">待我处理</TabsTrigger>
          <TabsTrigger value="mine">我发起的</TabsTrigger>
        </TabsList>

        <TabsContent value="todo" className="stagger mt-4 space-y-3">
          {loading ? (
            <SplitCardsSkeleton />
          ) : error ? (
            <ErrorState title="分摊加载失败" desc={error.message} onRetry={retry} />
          ) : (
          <>
          {todo.length === 0 && (
            <EmptyState title="没有待处理的分摊" hint="家人发起的分摊会出现在这里" />
          )}
          {todo.map((s, i) => {
            const myShare = s.shares.find((sh) => sh.memberId === meId)!;
            return (
              <Card
                key={s.id}
                className={cn("p-4", closing[s.id] && "motion-safe:animate-collapse-out")}
                style={{ "--stagger-i": i } as CSSProperties}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-ink">{s.title}</p>
                    <p className="mt-0.5 text-xs text-ink-faint">
                      总额 <Amount cents={s.totalCents} currency={s.currency} /> · {fmtDate(s.createdAt)}
                    </p>
                  </div>
                  {s.version > 1 && <Badge tone="warn">已修改，需重新确认</Badge>}
                </div>
                <div className="mt-3 flex items-center justify-between">
                  <span className="flex items-center gap-2 text-xs text-ink-soft">
                    <MemberAvatar name={memberName(s.createdBy)} color={colorOf(s.createdBy)} size="sm" />
                    {memberName(s.createdBy)} 发起
                  </span>
                  <span className="text-right">
                    <span className="block text-[11px] text-ink-faint">我应摊</span>
                    <Amount cents={myShare.shareCents} currency={s.currency} className="text-base font-semibold" />
                  </span>
                </div>
                <div className="mt-3 flex gap-2">
                  <Button
                    className="flex-1"
                    disabled={!!pending[s.id] || !!closing[s.id] || stale}
                    onClick={() => void decide(s, true)}
                  >
                    {pending[s.id] ? "提交中…" : "接受"}
                  </Button>
                  <Button
                    variant="secondary"
                    className="flex-1"
                    disabled={!!pending[s.id] || !!closing[s.id] || stale}
                    onClick={() => {
                      setReturnTarget(s);
                      setReason("");
                    }}
                  >
                    退回
                  </Button>
                </div>
              </Card>
            );
          })}
          </>
          )}
        </TabsContent>

        <TabsContent value="mine" className="stagger mt-4 space-y-3">
          {loading ? (
            <SplitCardsSkeleton />
          ) : error ? (
            <ErrorState title="分摊加载失败" desc={error.message} onRetry={retry} />
          ) : (
          <>
          {mine.length === 0 && <EmptyState title="还没有发起过分摊" />}
          {mine.map((s, i) => {
            return (
              <Card key={s.id} className="p-4" style={{ "--stagger-i": i } as CSSProperties}>
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-ink">{s.title}</p>
                    <p className="mt-0.5 text-xs text-ink-faint">
                      总额 <Amount cents={s.totalCents} currency={s.currency} /> · {fmtDate(s.createdAt)} 发起
                    </p>
                  </div>
                  {s.version > 1 && <Badge tone="warn">v{s.version} 已修改</Badge>}
                </div>
                <div className="mt-3 space-y-2">
                  {s.shares.map((sh) => (
                    <div key={sh.memberId}>
                      <div className="flex items-center gap-2">
                        <MemberAvatar name={memberName(sh.memberId)} color={colorOf(sh.memberId)} size="sm" />
                        <span className="text-sm text-ink">{memberName(sh.memberId)}</span>
                        <Amount cents={sh.shareCents} currency={s.currency} className="ml-auto text-xs text-ink-soft" />
                        <ShareBadge status={sh.status} />
                      </div>
                      {sh.status === "returned" && sh.note && (
                        <p className="ml-8 mt-1 text-xs text-danger">退回理由:{sh.note}</p>
                      )}
                    </div>
                  ))}
                </div>
                <p className="mt-3 border-t border-slate-900/5 pt-2 text-[11px] text-ink-faint">
                  分摊需对方接受后才生效。
                </p>
              </Card>
            );
          })}
          </>
          )}
        </TabsContent>
      </Tabs>

      <Dialog open={!!returnTarget} onOpenChange={(o) => !o && setReturnTarget(null)}>
        <DialogContent sheet="bottom">
          <DialogHeader
            title="退回分摊"
            desc={
              returnTarget
                ? `「${returnTarget.title}」将退回给 ${memberName(returnTarget.createdBy)}，请填写退回理由。`
                : undefined
            }
          />
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            rows={3}
            placeholder="例如：这笔费用我不参与"
            className="glass w-full rounded-xl p-3 text-sm text-ink outline-none placeholder:text-ink-faint"
          />
          <div className="mt-4 flex gap-2">
            <DialogClose asChild>
              <Button variant="secondary" className="flex-1">
                取消
              </Button>
            </DialogClose>
            <Button
              className="flex-1"
              disabled={!reason.trim() || stale || (returnTarget ? !!pending[returnTarget.id] : false)}
              onClick={() => {
                if (!returnTarget) return;
                const target = returnTarget;
                void decide(target, false, reason.trim()).then((ok) => {
                  // 仅成功才关闭对话框：失败时 decide 已展示错误，卡片保留
                  if (ok) setReturnTarget(null);
                });
              }}
            >
              确认退回
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
