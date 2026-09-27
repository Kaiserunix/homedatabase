import { useMemo, useState, type CSSProperties, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { ArrowLeftRight, ChevronRight, Inbox, Users } from "lucide-react";
import { Amount, Card, SectionTitle } from "@/components/ui";
import { Dialog, DialogContent, DialogHeader } from "@/components/dialog";
import { TxnDetailContent } from "@/components/TxnDetailContent";
import { Skeleton, TxnListSkeleton } from "@/components/skeleton";
import { ErrorState } from "@/components/error";
import { dataIcon } from "@/components/icons";
import { InstallPrompt } from "@/mobile/components/InstallPrompt";
import { fmtDate } from "@/lib/utils";
import { currentLedgerMonthKey, ledgerMonthKey } from "@/lib/ledger-date";
import { sumCents } from "@/lib/decimal";
import { baseCurrencyOf } from "@/lib/aggregate";
import { useApiQuery } from "@/api/useApi";
import {
  listAccounts, listCategories, listImportBatches, listMembers, listSplits, listTransactions,
} from "@/api/resources";
import { useAuth } from "@/auth/AuthContext";
import type { Account, Category, Transaction } from "@/data/types";

const amountKind = (t: Transaction): "expense" | "income" | "neutral" =>
  t.type === "expense" ? "expense" : t.type === "income" || t.type === "refund" ? "income" : "neutral";

/** 流水行：分类图标圆底 + 商户/备注 + 日期 + 右侧金额 */
function TxnRow({ t, cats, onClick, style }: { t: Transaction; cats: Category[]; onClick: () => void; style?: CSSProperties }) {
  const cat = cats.find((c) => c.id === t.categoryId);
  const Icon = t.type === "transfer" ? ArrowLeftRight : dataIcon(cat?.icon);
  return (
    <button
      onClick={onClick}
      style={style}
      className="flex w-full items-center gap-3 py-3 text-left transition active:bg-slate-900/[0.03]"
    >
      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-accent-soft text-accent">
        <Icon className="h-5 w-5" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium text-ink">{t.payee}</span>
        <span className="block truncate text-xs text-ink-faint">
          {t.note || (t.type === "transfer" ? "转账" : cat?.name)} · {fmtDate(t.date)}
        </span>
      </span>
      <Amount cents={t.amountCents} kind={amountKind(t)} currency={t.currency} className="shrink-0 text-sm" />
    </button>
  );
}

function TodoCard({ to, icon, title, desc, style }: { to: string; icon: ReactNode; title: string; desc: string; style?: CSSProperties }) {
  return (
    <Link to={to} className="block" style={style}>
      <Card className="flex items-center gap-3 p-3.5 transition hover:bg-white/90">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-warn/10 text-warn">
          {icon}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-medium text-ink">{title}</span>
          <span className="block text-xs text-ink-faint">{desc}</span>
        </span>
        <ChevronRight className="h-4 w-4 shrink-0 text-ink-faint" />
      </Card>
    </Link>
  );
}

/** 总资产大卡骨架：沿用 strong Card 外框 */
function AssetCardSkeleton() {
  return (
    <Card strong className="mb-5 space-y-3 p-5" aria-busy="true" aria-label="加载中">
      <Skeleton className="h-3 w-24" />
      <Skeleton className="h-8 w-40" />
      <div className="grid grid-cols-2 gap-3 border-t border-slate-900/5 pt-3">
        <div className="space-y-2">
          <Skeleton className="h-3 w-14" />
          <Skeleton className="h-4 w-20" />
        </div>
        <div className="space-y-2">
          <Skeleton className="h-3 w-14" />
          <Skeleton className="h-4 w-20" />
        </div>
      </div>
    </Card>
  );
}

/**
 * 首页统计由交易/账户数据前端聚合（上线后应由后端聚合接口如 GET /stats/summary 替代）。
 * 总资产只含人民币账户、不做外币折算，与原 mock 近似值口径一致；负债为负数余额之和。
 * 跨交易收支一律按基础币 baseAmountCents 合计、以 baseCurrency 展示；
 * 基础币不一致由 baseCurrencyOf 抛错防御，不静默混加。
 */
function aggregateStats(txns: Transaction[], accts: Account[]) {
  const totalAssetCents = sumCents(
    accts.filter((a) => a.kind === "asset" && a.currency === "CNY").map((a) => a.balanceCents),
  );
  // “本月”= 当前上海自然月（当前月无交易时收支为 0）；月份归属按上海账期口径
  const month = currentLedgerMonthKey();
  const inMonth = txns.filter((t) => ledgerMonthKey(t.date) === month);
  const baseCurrency = baseCurrencyOf(inMonth);
  const sum = (type: Transaction["type"]) =>
    sumCents(inMonth.filter((t) => t.type === type).map((t) => t.baseAmountCents));
  // 净支出 = 支出 − 退款（退款是反向支出，不计入收入；净值允许为零或负数，如实显示）
  return { totalAssetCents, monthIncomeCents: sum("income"), monthNetExpenseCents: sum("expense") - sum("refund"), baseCurrency };
}

export default function HomePage() {
  const { member } = useAuth();
  const memberId = member?.id ?? null;
  const [selected, setSelected] = useState<Transaction | null>(null);
  // 无聚合接口：组合现有资源函数一次取齐，stats 由前端聚合
  const { data, loading, error, retry } = useApiQuery(
    () =>
      Promise.all([
        listTransactions(), listAccounts(), listSplits(), listImportBatches(),
        listCategories(), listMembers(),
      ]).then(
        ([txns, accts, splitList, batches, cats, members]) =>
          ({ txns, accts, splitList, batches, cats, members }),
      ),
    [],
  );

  const stats = useMemo(() => (data ? aggregateStats(data.txns, data.accts) : null), [data]);
  const recent = useMemo(
    () => (data ? [...data.txns].sort((a, b) => b.date.localeCompare(a.date)).slice(0, 6) : []),
    [data],
  );

  // 待办：有我待接受的分摊 / 有待确认入账的导入候选
  const pendingSplitCount = (data?.splitList ?? []).filter(
    (s) => s.status === "open" && s.shares.some((sh) => sh.memberId === memberId && sh.status === "pending"),
  ).length;
  const pendingImportCount = (data?.batches ?? [])
    .filter((b) => b.status === "reviewing")
    .reduce((n, b) => n + b.candidates.filter((c) => c.status === "pending").length, 0);

  const hour = new Date().getHours();
  const greet = hour < 6 ? "夜深了" : hour < 12 ? "早上好" : hour < 18 ? "下午好" : "晚上好";

  return (
    <div className="px-4 pt-[calc(1rem+env(safe-area-inset-top))]">
      <InstallPrompt />
      <p className="text-sm text-ink-faint">{greet}</p>
      <h1 className="mb-4 text-xl font-semibold text-ink">家庭账本</h1>

      {/* 总资产 / 待办 / 最近流水：骨架 / 错误 / 内容互斥渲染 */}
      {loading ? (
        <>
          <AssetCardSkeleton />
          <SectionTitle title="最近流水" />
          <TxnListSkeleton rows={6} />
        </>
      ) : error ? (
        <ErrorState title="首页数据加载失败" desc={error.message} onRetry={retry} />
      ) : stats ? (
        <>
      {/* 总资产概览 */}
      <Card strong className="relative mb-5 overflow-hidden p-5">
        {/* 装饰光斑：极淡，跟随主题色 */}
        <div
          aria-hidden
          className="pointer-events-none absolute -right-10 -top-14 h-40 w-40 rounded-full bg-accent/10 blur-2xl"
        />
        <div
          aria-hidden
          className="pointer-events-none absolute -bottom-16 -left-8 h-32 w-32 rounded-full bg-accent/[0.07] blur-2xl"
        />
        <p className="text-xs tracking-wide text-ink-faint">总资产（负债另计）</p>
        <Amount
          cents={stats.totalAssetCents}
          kind="neutral"
          className="mt-1 block text-[2rem] font-semibold leading-tight tracking-tight"
        />
        <div className="mt-4 grid grid-cols-2 gap-3 border-t border-slate-900/5 pt-3">
          <div>
            <p className="text-xs text-ink-faint">本月收入</p>
            <Amount cents={stats.monthIncomeCents} kind="income" currency={stats.baseCurrency} className="mt-0.5 block text-base font-medium tracking-tight" />
          </div>
          <div>
            <p className="text-xs text-ink-faint">本月净支出</p>
            <Amount cents={stats.monthNetExpenseCents} kind="expense" currency={stats.baseCurrency} className="mt-0.5 block text-base font-medium tracking-tight" />
          </div>
        </div>
      </Card>

      {/* 待办 */}
      {(pendingSplitCount > 0 || pendingImportCount > 0) && (
        <div className="mb-5">
          <SectionTitle title="待办" />
          <div className="stagger space-y-2">
            {pendingSplitCount > 0 && (
              <TodoCard
                to="/splits"
                icon={<Users className="h-4 w-4" />}
                title={`${pendingSplitCount} 笔分摊待你接受`}
                desc="接受后才会生效"
                style={{ "--stagger-i": 0 } as CSSProperties}
              />
            )}
            {pendingImportCount > 0 && (
              <TodoCard
                to="/me/import"
                icon={<Inbox className="h-4 w-4" />}
                title={`${pendingImportCount} 条导入账目待确认`}
                desc="确认后才会入账"
                style={{ "--stagger-i": 1 } as CSSProperties}
              />
            )}
          </div>
        </div>
      )}

      {/* 最近流水 */}
      <SectionTitle title="最近流水" />
      <Card className="stagger divide-y divide-slate-900/5 px-4">
        {recent.map((t, i) => (
          <TxnRow
            key={t.id}
            t={t}
            cats={data?.cats ?? []}
            onClick={() => setSelected(t)}
            style={{ "--stagger-i": i } as CSSProperties}
          />
        ))}
      </Card>
      <Link
        to="/txns"
        className="mt-3 flex items-center justify-center gap-1 py-2 text-sm font-medium text-accent"
      >
        查看全部流水
        <ChevronRight className="h-4 w-4" />
      </Link>
        </>
      ) : null}

      <Dialog open={!!selected} onOpenChange={(o) => !o && setSelected(null)}>
        <DialogContent sheet="bottom" className="overflow-y-auto">
          {selected && (
            <>
              <DialogHeader title={selected.payee} />
              <TxnDetailContent
                txn={selected}
                accounts={data?.accts ?? []}
                categories={data?.cats ?? []}
                members={data?.members ?? []}
                batches={data?.batches ?? []}
                splits={data?.splitList ?? []}
                transactions={data?.txns ?? []}
              />
            </>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
