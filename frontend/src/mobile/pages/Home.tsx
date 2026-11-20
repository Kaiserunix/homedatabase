import { useMemo, useState, type CSSProperties, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { ArrowLeftRight, CalendarClock, ChevronRight, Inbox, Receipt, Users } from "lucide-react";
import { Amount, Button, Card, EmptyState, SectionTitle } from "@/components/ui";
import { Dialog, DialogContent, DialogHeader } from "@/components/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger } from "@/components/select";
import { TxnDetailContent } from "@/components/TxnDetailContent";
import { Skeleton, TxnListSkeleton } from "@/components/skeleton";
import { ErrorState } from "@/components/error";
import { dataIcon } from "@/components/icons";
import { InstallPrompt } from "@/mobile/components/InstallPrompt";
import {
  formatCompactMonthLabel, selectAvailableLedgerMonths,
  selectLatestActiveMonth, selectLedgerOverview,
} from "@/domain/ledger-insights";
import { fmtDate } from "@/lib/utils";
import { currentLedgerMonthKey } from "@/lib/ledger-date";
import { useApiQuery } from "@/api/useApi";
import {
  listAccounts, listCategories, listImportBatches, listMembers, listSplits, listTransactions,
} from "@/api/resources";
import { useAuth } from "@/auth/AuthContext";
import type { Category, Transaction } from "@/data/types";

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

export default function HomePage() {
  const { member } = useAuth();
  const memberId = member?.id ?? null;
  const currentMonth = currentLedgerMonthKey();
  const [selectedMonth, setSelectedMonth] = useState(currentMonth);
  const [selected, setSelected] = useState<Transaction | null>(null);

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

  const overview = useMemo(
    () =>
      data
        ? selectLedgerOverview({
            txns: data.txns,
            accts: data.accts,
            cats: data.cats,
            month: selectedMonth,
            recentLimit: 6,
          })
        : null,
    [data, selectedMonth],
  );

  const availableMonths = useMemo(
    () => selectAvailableLedgerMonths(data?.txns ?? [], currentMonth),
    [data?.txns, currentMonth],
  );
  const latestActiveMonth = useMemo(
    () => selectLatestActiveMonth(data?.txns ?? []),
    [data?.txns],
  );

  const isCurrentMonth = selectedMonth === currentMonth;
  const showEmptyCurrentMonthHint =
    isCurrentMonth &&
    overview?.monthTxnCount === 0 &&
    latestActiveMonth !== null &&
    latestActiveMonth !== currentMonth;

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
      <div className="mb-4 flex items-center justify-between gap-2">
        <h1 className="text-xl font-semibold text-ink">家庭账本</h1>
        {availableMonths.length > 1 && (
          <Select value={selectedMonth} onValueChange={setSelectedMonth}>
            <SelectTrigger className="h-8 w-36 text-xs" aria-label="选择统计账期月" />
            <SelectContent>
              {availableMonths.map((m) => (
                <SelectItem key={m} value={m}>
                  {formatCompactMonthLabel(m)}
                  {m === currentMonth ? "（本月）" : ""}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </div>

      {loading ? (
        <>
          <AssetCardSkeleton />
          <SectionTitle title="最近流水" />
          <TxnListSkeleton rows={6} />
        </>
      ) : error ? (
        <ErrorState title="首页数据加载失败" desc={error.message} onRetry={retry} />
      ) : overview ? (
        <>
          {/* 总资产概览 */}
          <Card strong className="relative mb-5 overflow-hidden p-5">
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
              cents={overview.totalAssetCents}
              kind="neutral"
              className="mt-1 block text-[2rem] font-semibold leading-tight tracking-tight"
            />
            <div className="mt-4 grid grid-cols-2 gap-3 border-t border-slate-900/5 pt-3">
              <div>
                <p className="text-xs text-ink-faint">{isCurrentMonth ? "本月收入" : "所选月收入"}</p>
                <Amount
                  cents={overview.monthIncomeCents}
                  kind="income"
                  currency={overview.baseCurrency}
                  className="mt-0.5 block text-base font-medium tracking-tight"
                />
              </div>
              <div>
                <p className="text-xs text-ink-faint">{isCurrentMonth ? "本月净支出" : "所选月净支出"}</p>
                <Amount
                  cents={overview.monthNetExpenseCents}
                  kind="expense"
                  currency={overview.baseCurrency}
                  className="mt-0.5 block text-base font-medium tracking-tight"
                />
              </div>
            </div>
          </Card>

          {showEmptyCurrentMonthHint && latestActiveMonth && (
            <Card className="mb-5 flex items-center justify-between gap-2 px-3.5 py-3">
              <div className="flex items-center gap-2 text-xs text-ink-soft">
                <CalendarClock className="h-4 w-4 shrink-0 text-accent" />
                <span>本月暂无收支，最近记录在 {formatCompactMonthLabel(latestActiveMonth)}</span>
              </div>
              <Button size="sm" variant="secondary" onClick={() => setSelectedMonth(latestActiveMonth)}>
                查看
              </Button>
            </Card>
          )}

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
          {overview.recentTxns.length === 0 ? (
            <Card className="p-4">
              <EmptyState
                icon={<Receipt className="h-8 w-8" />}
                title="还没有记账记录"
                hint="点击底部「记一笔」或在「我的 → 导入账单」导入流水"
                action={
                  <Link
                    to="/add"
                    className="glass inline-flex h-8 items-center rounded-xl px-3 text-xs font-medium text-ink transition hover:bg-white/90"
                  >
                    记第一笔
                  </Link>
                }
              />
            </Card>
          ) : (
            <>
              <Card className="stagger divide-y divide-slate-900/5 px-4">
                {overview.recentTxns.map((t, i) => (
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
          )}
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
