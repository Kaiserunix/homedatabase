import { useEffect, useState, type CSSProperties } from "react";
import { Plus, Users } from "lucide-react";
import { AdminHeader } from "@/admin/components/AdminHeader";
import { Amount, Badge, Button, Card, EmptyState, MemberAvatar } from "@/components/ui";
import { Tabs, TabsList, TabsTrigger } from "@/components/tabs";
import { Dialog, DialogClose, DialogContent, DialogHeader, DialogTrigger } from "@/components/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger } from "@/components/select";
import { Checkbox } from "@/components/checkbox";
import { Skeleton } from "@/components/skeleton";
import { ErrorState } from "@/components/error";
import { useApiQuery } from "@/api/useApi";
import {
  createSplit as apiCreateSplit, listMembers, listSplits, listTransactions,
  respondToSplit, updateSplitShare,
} from "@/api/resources";
import { ApiError } from "@/api/client";
import { useAuth } from "@/auth/AuthContext";
import { formatCents, isPositiveAmount, parseDecimalCents } from "@/lib/decimal";
import { evenShares } from "@/lib/split-shares";
import type { Split, SplitShareStatus, SplitStatus } from "@/data/types";
import { fmtDate, fmtDateTime, fmtMoney } from "@/lib/utils";

const splitStatusMeta: Record<SplitStatus, { tone: "accent" | "danger" | "ok"; label: string }> = {
  open: { tone: "accent", label: "进行中" },
  returned: { tone: "danger", label: "已退回" },
  settled: { tone: "ok", label: "已完成" },
};

const shareStatusMeta: Record<SplitShareStatus, { tone: "neutral" | "ok" | "danger"; label: string }> = {
  pending: { tone: "neutral", label: "待接受" },
  accepted: { tone: "ok", label: "已接受" },
  returned: { tone: "danger", label: "已退回" },
};

const errMsg = (e: unknown) => (e instanceof ApiError ? e.message : "操作失败，请稍后重试");

/** 409 版本冲突提示：分摊刚被改动，刷新列表后请重新审阅 */
const CONFLICT_COPY = "该分摊刚被其他人修改，已为你刷新列表，请重新审阅后再操作。";

/** 卡片骨架：与下方分摊卡片的结构大致对齐 */
function SplitCardsSkeleton({ count = 2 }: { count?: number }) {
  return (
    <div className="mt-4 space-y-4" aria-busy="true" aria-label="加载中">
      {Array.from({ length: count }, (_, i) => (
        <Card key={i} className="space-y-3 p-5">
          <div className="flex items-center justify-between gap-3">
            <Skeleton className="h-4 w-48" />
            <Skeleton className="h-5 w-14 rounded-full" />
          </div>
          <Skeleton className="h-3 w-64" />
          {[0, 1, 2].map((j) => (
            <div key={j} className="flex items-center gap-3 border-t border-slate-900/5 pt-2.5">
              <Skeleton className="h-6 w-6 shrink-0 rounded-full" />
              <Skeleton className="h-3.5 w-24" />
              <div className="flex-1" />
              <Skeleton className="h-3.5 w-16" />
              <Skeleton className="h-5 w-14 rounded-full" />
            </div>
          ))}
        </Card>
      ))}
    </div>
  );
}

export default function SplitsPage() {
  const { member } = useAuth();
  const myId = member?.id ?? "";
  const { data, loading, error, retry } = useApiQuery(
    async () => {
      const [splitList, txns, memberList] = await Promise.all([
        listSplits(),
        listTransactions(),
        listMembers(),
      ]);
      return { splitList, txns, memberList };
    },
    [],
  );
  // 份额状态以服务端响应为准；列表刷新（含 409 强制刷新）后整体重置
  const [items, setItems] = useState<Split[]>([]);
  useEffect(() => {
    if (data) setItems(data.splitList);
  }, [data]);
  const [tab, setTab] = useState("all");
  const [actionError, setActionError] = useState<string | null>(null);
  const [busySplitId, setBusySplitId] = useState<string | null>(null);
  const [returnTarget, setReturnTarget] = useState<{ splitId: string; memberId: string } | null>(null);
  const [returnNote, setReturnNote] = useState("");
  const [returnError, setReturnError] = useState<string | null>(null);
  const [editTarget, setEditTarget] = useState<{ splitId: string; memberId: string } | null>(null);
  const [editAmount, setEditAmount] = useState("");
  const [editError, setEditError] = useState<string | null>(null);
  const [newOpen, setNewOpen] = useState(false);
  const [newTxnId, setNewTxnId] = useState<string | null>(null);
  const [newMembers, setNewMembers] = useState<string[]>([]);
  const [createBusy, setCreateBusy] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  // 409 写锁：true 期间禁止一切分摊写操作，直到刷新最新列表成功
  const [writeLocked, setWriteLocked] = useState(false);

  const txns = data?.txns ?? [];
  const members = data?.memberList ?? [];
  const memberName = (id: string) => members.find((m) => m.id === id)?.name ?? id;
  const expenseTxns = txns.filter((t) => t.type === "expense");
  const filtered = tab === "all" ? items : items.filter((s) => s.status === tab);
  const selTxn = expenseTxns.find((t) => t.id === newTxnId) ?? null;

  /**
   * 409 冲突：立即关闭退回/改金额/新建三类弹窗并丢弃对应旧快照与表单、进入写锁；
   * await 拉取最新分摊列表，仅成功才解锁并提示已刷新；
   * GET 失败则诚实提示刷新失败、维持写锁，由「重试刷新」重走本函数。
   */
  const refreshAfterConflict = async () => {
    setReturnTarget(null);
    setEditTarget(null);
    setReturnNote("");
    setEditAmount("");
    setReturnError(null);
    setEditError(null);
    setNewOpen(false);
    setNewTxnId(null);
    setNewMembers([]);
    setCreateError(null);
    setWriteLocked(true);
    try {
      setItems(await listSplits());
      setWriteLocked(false);
      setActionError(CONFLICT_COPY);
    } catch {
      setActionError("该分摊刚被其他人修改，但刷新最新列表失败。请点击「重试刷新」，成功后再重新操作。");
    }
  };

  const patchSplit = (id: string, next: Split) =>
    setItems((list) => list.map((s) => (s.id === id ? next : s)));

  /** 写操作统一错误处理：任何失败保留页面状态并展示真实错误；409 强制刷新（失败保持锁定） */
  const handleWriteError = async (e: unknown, setLocal?: (msg: string | null) => void) => {
    if (e instanceof ApiError && e.status === 409) {
      await refreshAfterConflict();
      return;
    }
    (setLocal ?? setActionError)(errMsg(e));
  };

  const acceptShare = async (split: Split, memberId: string) => {
    if (busySplitId || writeLocked) return;
    setBusySplitId(split.id);
    setActionError(null);
    try {
      patchSplit(split.id, await respondToSplit(split.id, { accept: true, expectedVersion: split.version }));
    } catch (e) {
      await handleWriteError(e);
    } finally {
      setBusySplitId(null);
    }
  };

  const confirmReturn = async () => {
    if (!returnTarget || !returnNote.trim() || busySplitId || writeLocked) return;
    const split = items.find((s) => s.id === returnTarget.splitId);
    if (!split) return;
    setBusySplitId(split.id);
    setReturnError(null);
    try {
      patchSplit(
        split.id,
        await respondToSplit(split.id, {
          accept: false,
          expectedVersion: split.version,
          note: returnNote.trim(),
        }),
      );
      setReturnTarget(null);
    } catch (e) {
      await handleWriteError(e, setReturnError);
    } finally {
      setBusySplitId(null);
    }
  };

  const confirmEdit = async () => {
    if (!editTarget || busySplitId || writeLocked) return;
    // 精确十进制解析：无效/超界金额行内报错，绝不发出请求
    const parsed = parseDecimalCents(editAmount);
    if (!parsed.ok || parsed.cents <= 0) {
      setEditError("请输入有效金额（大于 0，最多两位小数）");
      return;
    }
    const cents = parsed.cents;
    const split = items.find((s) => s.id === editTarget.splitId);
    if (!split) return;
    setBusySplitId(split.id);
    setEditError(null);
    try {
      patchSplit(
        split.id,
        await updateSplitShare(split.id, editTarget.memberId, {
          shareCents: cents,
          expectedVersion: split.version,
        }),
      );
      setEditTarget(null);
    } catch (e) {
      await handleWriteError(e, setEditError);
    } finally {
      setBusySplitId(null);
    }
  };

  const toggleNewMember = (id: string, on: boolean) =>
    setNewMembers((list) => (on ? [...list, id] : list.filter((x) => x !== id)));

  const createSplit = async () => {
    if (!selTxn || newMembers.length === 0 || createBusy || writeLocked) return;
    // 均分先整除、余数按稳定成员顺序（后端成员列表顺序）每人加 1 分，总和精确等于原交易
    const orderedIds = members.filter((m) => newMembers.includes(m.id)).map((m) => m.id);
    const shares = evenShares(selTxn.amountCents, orderedIds);
    setCreateBusy(true);
    setCreateError(null);
    try {
      // 标题由后端从原交易账面名称派生，前端不自拟展示标题
      const created = await apiCreateSplit({
        txnId: selTxn.id,
        totalCents: selTxn.amountCents,
        shares,
      });
      setItems((list) => [created, ...list]);
      setNewOpen(false);
      setNewTxnId(null);
      setTab("all");
    } catch (e) {
      // 后端在账目已有分摊时返回 409：与其余写路径同一冲突状态机
      if (e instanceof ApiError && e.status === 409) {
        await refreshAfterConflict();
      } else {
        setCreateError(errMsg(e));
      }
    } finally {
      setCreateBusy(false);
    }
  };

  return (
    <div>
      <AdminHeader title="分摊" desc="分摊需对方接受；退回后由发起人修改，关键变更需重新确认。" />

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList>
          <TabsTrigger value="all">全部</TabsTrigger>
          <TabsTrigger value="open">进行中</TabsTrigger>
          <TabsTrigger value="returned">已退回</TabsTrigger>
          <TabsTrigger value="settled">已完成</TabsTrigger>
        </TabsList>
      </Tabs>

      {actionError && (
        <p role="alert" className="mt-4 rounded-xl bg-danger/10 px-3 py-2 text-xs text-danger">
          {actionError}
        </p>
      )}
      {writeLocked && (
        <div className="mt-4">
          <Button variant="secondary" size="sm" onClick={() => void refreshAfterConflict()}>
            重试刷新
          </Button>
        </div>
      )}

      {loading ? (
        <SplitCardsSkeleton />
      ) : error ? (
        <Card className="mt-4">
          <ErrorState title="分摊加载失败" desc={error.message} onRetry={retry} />
        </Card>
      ) : (
        // 状态 Tab 切换时以 key 重挂载列表，逐项重新进场提示内容已更新
        <div key={tab} className="stagger mt-4 space-y-4">
          {filtered.length === 0 && (
            <Card>
              <EmptyState icon={<Users className="h-8 w-8" />} title="暂无分摊" hint="当前状态下没有分摊记录" />
            </Card>
          )}
          {filtered.map((s, si) => {
            const txn = txns.find((t) => t.id === s.txnId);
            return (
              <Card key={s.id} className="p-5" style={{ "--stagger-i": si } as CSSProperties}>
                <div className="flex items-start justify-between gap-3">
                  <div className="flex flex-wrap items-center gap-2">
                    {/* 标题来自后端（派生自原交易账面名称）；金额为原交易币 */}
                    <h3 className="text-sm font-semibold text-ink">{s.title}</h3>
                    <Amount cents={s.totalCents} currency={s.currency} />
                    {s.version > 1 && <Badge tone="warn">第 {s.version} 版 · 关键变更后需重新确认</Badge>}
                  </div>
                  <Badge tone={splitStatusMeta[s.status].tone}>{splitStatusMeta[s.status].label}</Badge>
                </div>
                <p className="mt-1 text-xs text-ink-faint">
                  发起人 {memberName(s.createdBy)} · {fmtDateTime(s.createdAt)}
                  {txn && (
                    <>
                      {" "}· 关联交易：{txn.payee}（{fmtDate(txn.date)}）
                    </>
                  )}
                </p>
                <table className="mt-3 w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs text-ink-faint">
                      <th className="py-2 pr-2 font-medium">成员</th>
                      <th className="py-2 pr-2 font-medium">应摊</th>
                      <th className="py-2 pr-2 font-medium">状态</th>
                      <th className="py-2 text-right font-medium">操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {s.shares.map((sh) => {
                      const m = members.find((x) => x.id === sh.memberId);
                      const mine = sh.memberId === myId;
                      return (
                        <tr key={sh.memberId} className="border-t border-slate-900/5">
                          <td className="py-2.5 pr-2">
                            <div className="flex items-center gap-2">
                              <MemberAvatar name={m?.name ?? sh.memberId} color={m?.color ?? "bg-slate-400"} size="sm" />
                              <span className="text-ink">{memberName(sh.memberId)}</span>
                              {mine && <span className="text-[11px] text-ink-faint">（我）</span>}
                            </div>
                          </td>
                          <td className="py-2.5 pr-2">
                            <Amount cents={sh.shareCents} currency={s.currency} />
                          </td>
                          <td className="py-2.5 pr-2">
                            <Badge tone={shareStatusMeta[sh.status].tone}>{shareStatusMeta[sh.status].label}</Badge>
                            {sh.status === "returned" && sh.note && (
                              <p className="mt-1 text-[11px] text-ink-faint">理由：{sh.note}</p>
                            )}
                          </td>
                          <td className="py-2.5">
                            <div className="flex justify-end gap-1.5">
                              {mine && sh.status === "pending" && s.status === "open" && (
                                <>
                                  <Button
                                    size="sm"
                                    disabled={busySplitId === s.id || writeLocked}
                                    onClick={() => void acceptShare(s, sh.memberId)}
                                  >
                                    接受
                                  </Button>
                                  <Button
                                    size="sm"
                                    variant="danger"
                                    disabled={busySplitId === s.id || writeLocked}
                                    onClick={() => {
                                      setReturnTarget({ splitId: s.id, memberId: sh.memberId });
                                      setReturnNote("");
                                      setReturnError(null);
                                    }}
                                  >
                                    退回
                                  </Button>
                                </>
                              )}
                              {sh.status === "returned" && s.createdBy === myId && (
                                <Button
                                  size="sm"
                                  variant="secondary"
                                  disabled={busySplitId === s.id || writeLocked}
                                  onClick={() => {
                                    setEditTarget({ splitId: s.id, memberId: sh.memberId });
                                    setEditAmount(formatCents(sh.shareCents));
                                    setEditError(null);
                                  }}
                                >
                                  修改份额
                                </Button>
                              )}
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </Card>
            );
          })}
        </div>
      )}

      <div className="mt-4 flex justify-end">
        <Dialog
          open={newOpen}
          onOpenChange={(o) => {
            setNewOpen(o);
            if (o) {
              setNewMembers(members.map((m) => m.id));
              setCreateError(null);
            }
          }}
        >
          <DialogTrigger asChild>
            <Button disabled={writeLocked}>
              <Plus className="h-4 w-4" /> 发起新分摊
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader title="发起新分摊" desc="选择一笔支出交易，金额按成员均分：先整除，余数按成员顺序每人加 1 分，总和精确等于原交易；标题由后端按账面名称生成" />
            <div className="space-y-4">
              <div>
                <p className="mb-1.5 text-xs text-ink-soft">选择交易</p>
                {/* 始终受控（空串 = 未选择，显示 placeholder），避免 controlled/uncontrolled 切换 */}
                <Select value={newTxnId ?? ""} onValueChange={setNewTxnId}>
                  <SelectTrigger placeholder="选择一笔支出交易" />
                  <SelectContent>
                    {expenseTxns.map((t) => (
                      <SelectItem key={t.id} value={t.id}>
                        {t.payee} · {fmtDate(t.date)} · {fmtMoney(t.amountCents, t.currency)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div>
                <p className="mb-1.5 text-xs text-ink-soft">参与人</p>
                <div className="space-y-2">
                  {members.map((m) => (
                    <label key={m.id} className="flex cursor-pointer items-center gap-2.5">
                      <Checkbox
                        checked={newMembers.includes(m.id)}
                        onCheckedChange={(v) => toggleNewMember(m.id, v === true)}
                      />
                      <MemberAvatar name={m.name} color={m.color} size="sm" />
                      <span className="text-sm text-ink">{m.name}</span>
                    </label>
                  ))}
                </div>
              </div>
              {selTxn && newMembers.length > 0 && (() => {
                const orderedIds = members.filter((m) => newMembers.includes(m.id)).map((m) => m.id);
                const shares = evenShares(selTxn.amountCents, orderedIds);
                const amounts = new Set(shares.map((s) => s.shareCents));
                return (
                  <div className="rounded-xl bg-accent-soft px-3 py-2 text-xs text-accent-ink">
                    <p>
                      共 {fmtMoney(selTxn.amountCents, selTxn.currency)}，{newMembers.length} 人分摊，总和精确等于原交易。
                      {amounts.size === 1
                        ? `每人正好 ${fmtMoney(shares[0].shareCents, selTxn.currency)}。`
                        : "不能整除，余数按下方成员顺序每人多摊 1 分（份额可能相差 1 分）。"}
                    </p>
                    <ul className="mt-1.5 space-y-0.5">
                      {shares.map((s) => (
                        <li key={s.memberId} className="flex justify-between gap-4">
                          <span>{memberName(s.memberId)}</span>
                          <span className="font-num">{fmtMoney(s.shareCents, selTxn.currency)}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                );
              })()}
              {createError && <p role="alert" className="text-xs text-danger">{createError}</p>}
              <div className="flex justify-end gap-2">
                <DialogClose asChild>
                  <Button variant="ghost">取消</Button>
                </DialogClose>
                <Button
                  onClick={() => void createSplit()}
                  disabled={!selTxn || newMembers.length === 0 || createBusy || writeLocked}
                >
                  {createBusy ? "提交中…" : "发起分摊"}
                </Button>
              </div>
            </div>
          </DialogContent>
        </Dialog>
      </div>

      <Dialog open={returnTarget !== null} onOpenChange={(open) => !open && setReturnTarget(null)}>
        <DialogContent>
          <DialogHeader title="退回分摊" desc="退回后由发起人修改份额" />
          <div className="space-y-4">
            <div>
              <p className="mb-1.5 text-xs text-ink-soft">退回理由</p>
              <textarea
                value={returnNote}
                onChange={(e) => setReturnNote(e.target.value)}
                rows={3}
                placeholder="请填写理由，便于发起人调整"
                className="glass w-full rounded-xl px-3 py-2 text-sm text-ink outline-none placeholder:text-ink-faint"
              />
            </div>
            {returnError && <p role="alert" className="text-xs text-danger">{returnError}</p>}
            <div className="flex justify-end gap-2">
              <DialogClose asChild>
                <Button variant="ghost">取消</Button>
              </DialogClose>
              <Button variant="danger" disabled={!returnNote.trim() || !!busySplitId || writeLocked} onClick={() => void confirmReturn()}>
                确认退回
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={editTarget !== null} onOpenChange={(open) => !open && setEditTarget(null)}>
        <DialogContent>
          <DialogHeader title="修改份额" desc="修改后所有已接受方需重新确认" />
          <div className="space-y-4">
            <div>
              {/* 金额单位为该分摊的原交易币（USD 分摊不显示「元」） */}
              <p className="mb-1.5 text-xs text-ink-soft">
                新的应摊金额（{items.find((s) => s.id === editTarget?.splitId)?.currency ?? "CNY"}）
              </p>
              <input
                type="number"
                min="0"
                step="0.01"
                value={editAmount}
                onChange={(e) => setEditAmount(e.target.value)}
                className="glass h-10 w-full rounded-xl px-3 text-sm text-ink outline-none"
              />
            </div>
            <p className="text-xs text-warn">修改后所有已接受方需重新确认</p>
            {editError && <p role="alert" className="text-xs text-danger">{editError}</p>}
            <div className="flex justify-end gap-2">
              <DialogClose asChild>
                <Button variant="ghost">取消</Button>
              </DialogClose>
              <Button disabled={!isPositiveAmount(editAmount) || !!busySplitId || writeLocked} onClick={() => void confirmEdit()}>
                保存修改
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
