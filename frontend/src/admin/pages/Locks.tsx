import { useEffect, useState } from "react";
import { Lock, Plus, Unlock } from "lucide-react";
import { AdminHeader } from "../components/AdminHeader";
import { Badge, Button, Card, EmptyState } from "@/components/ui";
import { Dialog, DialogContent, DialogHeader } from "@/components/dialog";
import { Skeleton } from "@/components/skeleton";
import { ErrorState } from "@/components/error";
import { useApiQuery } from "@/api/useApi";
import {
  createPeriodLock, deletePeriodLock, listMembers, listPeriodLocks,
} from "@/api/resources";
import { ApiError } from "@/api/client";
import type { PeriodLock } from "@/data/types";
import { fmtDateTime } from "@/lib/utils";
import { currentLedgerMonthKey, ledgerMonthRange, shiftLedgerMonthKey } from "@/lib/ledger-date";

const inputCls = "glass h-10 w-full rounded-xl px-3 text-sm text-ink outline-none placeholder:text-ink-faint";

const errMsg = (e: unknown) => (e instanceof ApiError ? e.message : "操作失败，请稍后重试");

/** 表格骨架 */
function LocksSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-3 px-4 py-3" aria-busy="true" aria-label="加载中">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="flex items-center gap-4">
          <Skeleton className="h-3.5 w-44 shrink-0" />
          <Skeleton className="h-3.5 w-32 shrink-0" />
          <Skeleton className="h-3.5 w-20 shrink-0" />
          <div className="flex-1" />
          <Skeleton className="h-8 w-20 shrink-0 rounded-xl" />
        </div>
      ))}
    </div>
  );
}

export default function LocksPage() {
  const { data, loading, error, retry } = useApiQuery(
    async () => {
      const [locks, memberList] = await Promise.all([listPeriodLocks(), listMembers()]);
      return { locks, memberList };
    },
    [],
  );
  const [createOpen, setCreateOpen] = useState(false);
  const [unlockTarget, setUnlockTarget] = useState<PeriodLock | null>(null);

  const locks = data?.locks ?? [];
  const memberName = (id: string) => data?.memberList.find((m) => m.id === id)?.name ?? id;

  return (
    <div>
      <AdminHeader
        title="期间锁定"
        desc="锁定的期间内不可新增或修改账目，用于月度核对封账"
        action={
          <Button onClick={() => setCreateOpen(true)}>
            <Plus className="h-4 w-4" /> 新增锁定
          </Button>
        }
      />

      <Card className="overflow-hidden">
        {loading ? (
          <LocksSkeleton />
        ) : error ? (
          <ErrorState title="期间锁定加载失败" desc={error.message} onRetry={retry} />
        ) : locks.length === 0 ? (
          <EmptyState
            icon={<Lock className="h-8 w-8" />}
            title="暂无锁定的期间"
            hint="月度核对完成后，可锁定该月防止误改"
          />
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-900/10 text-left text-xs text-ink-faint">
                <th className="px-4 py-3 font-medium">锁定期间</th>
                <th className="px-4 py-3 font-medium">理由</th>
                <th className="px-4 py-3 font-medium">锁定人</th>
                <th className="px-4 py-3 font-medium">锁定时间</th>
                <th className="px-4 py-3 text-right font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              {locks.map((l) => (
                <tr key={l.id} className="border-b border-slate-900/5 last:border-0">
                  <td className="px-4 py-3">
                    <Badge tone="warn">
                      {l.startsOn} ~ {l.endsOn}
                    </Badge>
                  </td>
                  <td className="px-4 py-3 text-ink">{l.reason}</td>
                  <td className="px-4 py-3 text-ink-soft">{memberName(l.lockedBy)}</td>
                  <td className="px-4 py-3 text-ink-soft">{fmtDateTime(l.createdAt)}</td>
                  <td className="px-4 py-3 text-right">
                    <Button variant="danger" size="sm" onClick={() => setUnlockTarget(l)}>
                      <Unlock className="h-3.5 w-3.5" /> 解除锁定
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <CreateLockDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        onCreated={retry}
      />
      <UnlockDialog
        lock={unlockTarget}
        onClose={() => setUnlockTarget(null)}
        onDeleted={retry}
      />
    </div>
  );
}

/** 新增锁定：日期区间 + 理由（默认「月度核对完成」） */
function CreateLockDialog({
  open, onOpenChange, onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: () => void;
}) {
  const [startsOn, setStartsOn] = useState("");
  const [endsOn, setEndsOn] = useState("");
  const [reason, setReason] = useState("月度核对完成");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 打开时默认上一个自然月（上海口径完整区间）
  useEffect(() => {
    if (!open) return;
    const lastMonth = ledgerMonthRange(shiftLedgerMonthKey(currentLedgerMonthKey(), -1));
    setStartsOn(lastMonth.from);
    setEndsOn(lastMonth.to);
    setReason("月度核对完成");
    setError(null);
  }, [open]);

  const save = async () => {
    if (!startsOn || !endsOn) {
      setError("请选择起止日期");
      return;
    }
    if (startsOn > endsOn) {
      setError("开始日期不能晚于结束日期");
      return;
    }
    if (!reason.trim()) {
      setError("请填写锁定理由");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await createPeriodLock({ startsOn, endsOn, reason: reason.trim() });
      onOpenChange(false);
      onCreated();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader title="新增期间锁定" desc="锁定期间内的账目将不可新增或修改" />
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <label className="block">
              <span className="mb-1 block text-xs text-ink-faint">开始日期</span>
              <input
                type="date"
                value={startsOn}
                onChange={(e) => setStartsOn(e.target.value)}
                className={inputCls}
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-xs text-ink-faint">结束日期</span>
              <input
                type="date"
                value={endsOn}
                onChange={(e) => setEndsOn(e.target.value)}
                className={inputCls}
              />
            </label>
          </div>
          <label className="block">
            <span className="mb-1 block text-xs text-ink-faint">理由</span>
            <input
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="月度核对完成"
              className={inputCls}
            />
          </label>
          {error && <p role="alert" className="text-xs text-danger">{error}</p>}
          <div className="flex justify-end gap-2 pt-1">
            <Button variant="secondary" onClick={() => onOpenChange(false)}>取消</Button>
            <Button disabled={busy} onClick={() => void save()}>{busy ? "提交中…" : "锁定"}</Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

/** 解除锁定：必须填写理由（写入审计日志） */
function UnlockDialog({
  lock, onClose, onDeleted,
}: {
  lock: PeriodLock | null;
  onClose: () => void;
  onDeleted: () => void;
}) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setReason("");
    setError(null);
  }, [lock]);

  if (!lock) return null;

  const confirm = async () => {
    if (!reason.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await deletePeriodLock(lock.id, reason.trim());
      onClose();
      onDeleted();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader
          title="解除期间锁定"
          desc={`${lock.startsOn} ~ ${lock.endsOn}（${lock.reason}）`}
        />
        <div className="space-y-3">
          <p className="text-xs text-ink-soft">解除后该期间的账目恢复可编辑，操作会记录审计日志。</p>
          <label className="block">
            <span className="mb-1 block text-xs text-ink-faint">解除理由（必填）</span>
            <textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={2}
              placeholder="例如：补录遗漏的小票"
              className="glass w-full rounded-xl px-3 py-2 text-sm text-ink outline-none placeholder:text-ink-faint"
            />
          </label>
          {error && <p role="alert" className="text-xs text-danger">{error}</p>}
          <div className="flex justify-end gap-2 pt-1">
            <Button variant="secondary" onClick={onClose}>取消</Button>
            <Button variant="danger" disabled={busy || !reason.trim()} onClick={() => void confirm()}>
              {busy ? "解除中…" : "确认解除"}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
