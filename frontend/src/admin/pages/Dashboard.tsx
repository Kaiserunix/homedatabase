import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowLeftRight, ChevronRight, CreditCard, DatabaseBackup, FileUp,
  Landmark, Receipt, ShoppingBag, TrendingUp,
} from "lucide-react";
import { AdminHeader } from "../components/AdminHeader";
import { Amount, Badge, Button, Card, EmptyState, SectionTitle } from "@/components/ui";
import { Select, SelectContent, SelectItem, SelectTrigger } from "@/components/select";
import { ErrorState } from "@/components/error";
import { Skeleton, StatCardsSkeleton, TxnListSkeleton } from "@/components/skeleton";
import { CashflowInsightCard } from "@/components/insights/CashflowInsightCard";
import { dataIcon } from "@/components/icons";
import {
  formatCompactMonthLabel, formatSpacedMonthLabel, mainAccountName,
  selectAvailableLedgerMonths, selectLatestActiveMonth, selectLedgerOverview,
} from "@/domain/ledger-insights";
import { fmtDate } from "@/lib/utils";
import { currentLedgerMonthKey } from "@/lib/ledger-date";
import { useApiQuery } from "@/api/useApi";
import {
  listAccounts, listBackups, listCategories, listImportBatches, listSplits, listTransactions,
} from "@/api/resources";
import type { Account, BackupRecord, Category, ImportBatch, Split, Transaction } from "@/data/types";

const typeMeta: Record<Transaction["type"], { label: string; tone: "neutral" | "accent" | "warn" }> = {
  expense: { label: "支出", tone: "neutral" },
  income: { label: "收入", tone: "accent" },
  transfer: { label: "转账", tone: "neutral" },
  refund: { label: "退款", tone: "warn" },
};

const locName: Record<string, string> = {
  tencent: "腾讯云",
  local: "本地",
  overseas: "海外密文仓库",
};

interface DashboardData {
  txns: Transaction[];
  accts: Account[];
  cats: Category[];
  batches: ImportBatch[];
  splitList: Split[];
  backupList: BackupRecord[];
}

/** 「按分类支出 + 待办」两卡位的骨架 */
function MidCardsSkeleton() {
  return (
    <div className="mt-4 grid gap-4 xl:grid-cols-2" aria-busy="true" aria-label="加载中">
      {[0, 1].map((i) => (
        <Card key={i} className="space-y-3 p-5">
          <Skeleton className="h-4 w-24" />
          {[0, 1, 2].map((j) => (
            <div key={j} className="flex items-center gap-3">
              <Skeleton className="h-8 w-8 shrink-0 rounded-lg" />
              <div className="min-w-0 flex-1 space-y-1.5">
                <Skeleton className="h-3.5 w-2/5" />
                <Skeleton className="h-3 w-3/5" />
              </div>
              <Skeleton className="h-5 w-12 shrink-0 rounded-full" />
            </div>
          ))}
        </Card>
      ))}
    </div>
  );
}

function DashboardContent({
  data,
  selectedMonth,
  currentMonth,
  onSelectMonth,
}: {
  data: DashboardData;
  selectedMonth: string;
  currentMonth: string;
  onSelectMonth: (month: string) => void;
}) {
  const { txns, accts, cats, batches, splitList, backupList } = data;
  const [catScope, setCatScope] = useState<"month" | "all">("month");

  const categoryName = (id: string | null) =>
    id === null ? "未分类" : (cats.find((c) => c.id === id)?.name ?? "未分类");

  const latestActiveMonth = useMemo(() => selectLatestActiveMonth(txns), [txns]);
  const overview = useMemo(
    () =>
      selectLedgerOverview({
        txns,
        accts,
        cats,
        month: selectedMonth,
        categoryScope: catScope,
        recentLimit: 8,
      }),
    [txns, accts, cats, selectedMonth, catScope],
  );

  const isCurrentMonth = selectedMonth === currentMonth;
  const monthLabel = formatSpacedMonthLabel(selectedMonth);
  const incomeLabel = isCurrentMonth ? "本月收入" : "所选月收入";
  const netExpenseLabel = isCurrentMonth ? "本月净支出" : "所选月净支出";

  const reviewingBatches = batches.filter((b) => b.status === "reviewing");
  const openSplits = splitList.filter((s) => s.status === "open");
  const latestBackup = backupList[0];

  const statCards = [
    { label: "总资产", cents: overview.totalAssetCents, currency: "CNY", icon: Landmark, note: "人民币资产账户合计（外币另计）", kind: "neutral" as const },
    { label: "总负债", cents: overview.totalLiabilityCents, currency: "CNY", icon: CreditCard, note: "含房贷", kind: "neutral" as const },
    { label: incomeLabel, cents: overview.monthIncomeCents, currency: overview.baseCurrency, icon: TrendingUp, note: monthLabel, kind: "income" as const },
    { label: netExpenseLabel, cents: overview.monthNetExpenseCents, currency: overview.baseCurrency, icon: ShoppingBag, note: monthLabel, kind: "expense" as const },
  ];

  return (
    <>
      {/* 统计卡 */}
      <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
        {statCards.map(({ label, cents, currency, icon: Icon, note, kind }) => (
          <Card key={label} className="p-5 transition-shadow hover:shadow-pop">
            <div className="flex items-center gap-2 text-xs text-ink-faint">
              <span className="flex h-6 w-6 items-center justify-center rounded-md bg-accent-soft text-accent-ink">
                <Icon className="h-3.5 w-3.5" />
              </span>
              {label}
            </div>
            <p className="mt-2 text-[1.35rem] font-medium leading-tight tracking-tight">
              <Amount cents={cents} currency={currency} kind={kind} />
            </p>
            <p className="mt-1 text-xs text-ink-faint">{note}</p>
          </Card>
        ))}
      </div>

      {/* 现金流脉搏、环比支出核对与外币账户原币余额 */}
      <CashflowInsightCard
        overview={overview}
        currentMonth={currentMonth}
        latestActiveMonth={latestActiveMonth}
        onSelectMonth={onSelectMonth}
      />

      {/* 分类支出 + 待办 */}
      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <Card className="p-5">
          <SectionTitle
            title="按分类支出"
            action={
              <div className="inline-flex rounded-lg bg-slate-900/5 p-0.5 text-xs">
                <button
                  type="button"
                  onClick={() => setCatScope("month")}
                  className={
                    catScope === "month"
                      ? "rounded-md bg-white px-2 py-0.5 font-medium text-ink shadow-sm"
                      : "px-2 py-0.5 text-ink-faint hover:text-ink"
                  }
                >
                  当期
                </button>
                <button
                  type="button"
                  onClick={() => setCatScope("all")}
                  className={
                    catScope === "all"
                      ? "rounded-md bg-white px-2 py-0.5 font-medium text-ink shadow-sm"
                      : "px-2 py-0.5 text-ink-faint hover:text-ink"
                  }
                >
                  全部
                </button>
              </div>
            }
          />
          {overview.expenseByCategory.length === 0 ? (
            <EmptyState
              title={catScope === "month" ? "该账期暂无分类支出" : "暂无支出记录"}
              hint={
                catScope === "month" && txns.some((t) => t.type === "expense")
                  ? "可切换至「全部」或选择有流水的历史账期查看"
                  : "导入账单或记一笔支出后将在此汇总"
              }
              action={
                catScope === "month" && txns.some((t) => t.type === "expense") ? (
                  <Button size="sm" variant="secondary" onClick={() => setCatScope("all")}>
                    查看全部历史分类
                  </Button>
                ) : undefined
              }
            />
          ) : (
            <div className="space-y-3">
              {overview.expenseByCategory.map(({ cat, total, barWidthPct }) => {
                const Icon = dataIcon(cat.icon);
                return (
                  <div key={cat.id} className="flex items-center gap-3">
                    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-accent-soft text-accent-ink">
                      <Icon className="h-4 w-4" />
                    </span>
                    <span className="w-20 shrink-0 text-sm text-ink">{cat.name}</span>
                    <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-900/5">
                      <div
                        className="h-full rounded-full bg-gradient-to-r from-accent/70 to-accent"
                        style={{ width: `${barWidthPct}%` }}
                      />
                    </div>
                    <span className="w-24 shrink-0 text-right text-sm">
                      <Amount cents={total} />
                    </span>
                  </div>
                );
              })}
            </div>
          )}
        </Card>

        <Card className="p-5">
          <SectionTitle title="待办" />
          <div className="space-y-1">
            <Link to="/imports" className="flex items-center gap-3 rounded-xl px-2 py-2.5 transition hover:bg-white/60">
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-accent-soft text-accent-ink">
                <FileUp className="h-4 w-4" />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-sm text-ink">待确认导入批次</span>
                <span className="block truncate text-xs text-ink-faint">
                  {reviewingBatches.map((b) => b.filename).join("、") || "无"}
                </span>
              </span>
              <Badge tone="accent">{reviewingBatches.length} 批</Badge>
              <ChevronRight className="h-4 w-4 text-ink-faint" />
            </Link>

            <Link to="/splits" className="flex items-center gap-3 rounded-xl px-2 py-2.5 transition hover:bg-white/60">
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-accent-soft text-accent-ink">
                <ArrowLeftRight className="h-4 w-4" />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-sm text-ink">待处理分摊</span>
                <span className="block truncate text-xs text-ink-faint">
                  {openSplits.map((s) => s.title).join("、") || "无"}
                </span>
              </span>
              <Badge tone="accent">{openSplits.length} 笔</Badge>
              <ChevronRight className="h-4 w-4 text-ink-faint" />
            </Link>

            {latestBackup && (
              <Link to="/backups" className="flex items-center gap-3 rounded-xl px-2 py-2.5 transition hover:bg-white/60">
                <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-accent-soft text-accent-ink">
                  <DatabaseBackup className="h-4 w-4" />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block text-sm text-ink">备份状态</span>
                  <span className="block text-xs text-ink-faint">最近备份 {fmtDate(latestBackup.createdAt)} · 已加密，{latestBackup.locations.length} 个存放位置</span>
                </span>
                <span className="flex shrink-0 gap-1">
                  {latestBackup.locations.map((l) => (
                    <Badge
                      key={l.kind}
                      tone={l.status === "ok" ? "ok" : l.status === "syncing" ? "warn" : "danger"}
                    >
                      {locName[l.kind]}
                      {l.status === "syncing" ? "·同步中" : l.status === "failed" ? "·失败" : ""}
                    </Badge>
                  ))}
                </span>
                <ChevronRight className="h-4 w-4 text-ink-faint" />
              </Link>
            )}
          </div>
        </Card>
      </div>

      {/* 最近交易 */}
      <Card className="mt-4 p-5">
        <SectionTitle
          title="最近交易"
          action={
            <Link to="/txns" className="text-xs text-accent hover:underline">
              查看全部
            </Link>
          }
        />
        {overview.recentTxns.length === 0 ? (
          <EmptyState
            icon={<Receipt className="h-8 w-8" />}
            title="账本中还没有交易记录"
            hint="前往「导入与确认」上传账单，或在「账目与分录」中手动记一笔"
            action={
              <Link
                to="/imports"
                className="glass inline-flex h-8 items-center rounded-xl px-3 text-xs font-medium text-ink transition hover:bg-white/90"
              >
                去导入账单
              </Link>
            }
          />
        ) : (
          <table className="w-full text-sm">
            <thead className="text-xs text-ink-faint">
              <tr className="border-b border-slate-900/10">
                <th className="py-2 text-left font-medium">日期</th>
                <th className="py-2 text-left font-medium">类型</th>
                <th className="py-2 text-left font-medium">商户</th>
                <th className="py-2 text-left font-medium">分类</th>
                <th className="py-2 text-left font-medium">账户</th>
                <th className="py-2 text-right font-medium">金额</th>
                <th className="py-2 text-right font-medium">来源</th>
              </tr>
            </thead>
            <tbody>
              {overview.recentTxns.map((t) => {
                const meta = typeMeta[t.type];
                return (
                  <tr key={t.id} className="border-b border-slate-900/5 last:border-0 hover:bg-white/60">
                    <td className="py-2.5 pr-2 text-ink-soft">{fmtDate(t.date)}</td>
                    <td className="py-2.5 pr-2"><Badge tone={meta.tone}>{meta.label}</Badge></td>
                    <td className="py-2.5 pr-2 text-ink">{t.payee}</td>
                    <td className="py-2.5 pr-2 text-ink-soft">
                      {t.type === "transfer" ? "—" : categoryName(t.categoryId)}
                    </td>
                    <td className="py-2.5 pr-2 text-ink-soft">{mainAccountName(t, accts)}</td>
                    <td className="py-2.5 pr-2 text-right">
                      <Amount
                        cents={t.baseAmountCents}
                        currency={t.baseCurrency}
                        kind={t.type === "expense" ? "expense" : t.type === "income" ? "income" : "neutral"}
                      />
                      {t.currency !== t.baseCurrency && (
                        <span className="block text-xs text-ink-faint">
                          原币 <Amount cents={t.amountCents} currency={t.currency} />
                        </span>
                      )}
                    </td>
                    <td className="py-2.5 text-right">
                      <Badge tone={t.source === "import" ? "accent" : "neutral"}>
                        {t.source === "import" ? "导入" : "手动"}
                      </Badge>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Card>
    </>
  );
}

export default function DashboardPage() {
  const currentMonth = currentLedgerMonthKey();
  const [selectedMonth, setSelectedMonth] = useState(currentMonth);

  const { data, loading, error, retry } = useApiQuery<DashboardData>(
    async () => {
      const [txns, accts, cats, batches, splitList, backupList] = await Promise.all([
        listTransactions(),
        listAccounts(),
        listCategories(),
        listImportBatches(),
        listSplits(),
        listBackups(),
      ]);
      return { txns, accts, cats, batches, splitList, backupList };
    },
    [],
  );

  const availableMonths = useMemo(
    () => selectAvailableLedgerMonths(data?.txns ?? [], currentMonth),
    [data?.txns, currentMonth],
  );

  return (
    <div>
      <AdminHeader
        title="仪表盘"
        desc="家庭财务总览"
        action={
          availableMonths.length > 0 ? (
            <Select value={selectedMonth} onValueChange={setSelectedMonth}>
              <SelectTrigger className="w-44" aria-label="选择统计账期月" />
              <SelectContent>
                {availableMonths.map((m) => (
                  <SelectItem key={m} value={m}>
                    {formatCompactMonthLabel(m)}
                    {m === currentMonth ? "（本月）" : ""}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : undefined
        }
      />
      {loading ? (
        <>
          <StatCardsSkeleton />
          <MidCardsSkeleton />
          <TxnListSkeleton rows={8} className="mt-4" />
        </>
      ) : error ? (
        <Card className="mt-4">
          <ErrorState title="仪表盘加载失败" desc={error.message} onRetry={retry} />
        </Card>
      ) : data ? (
        <DashboardContent
          data={data}
          selectedMonth={selectedMonth}
          currentMonth={currentMonth}
          onSelectMonth={setSelectedMonth}
        />
      ) : null}
    </div>
  );
}
