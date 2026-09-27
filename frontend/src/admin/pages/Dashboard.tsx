import { Link } from "react-router-dom";
import {
  ArrowLeftRight, ChevronRight, CreditCard, DatabaseBackup, FileUp,
  Landmark, ShoppingBag, TrendingUp,
} from "lucide-react";
import { AdminHeader } from "../components/AdminHeader";
import { Amount, Badge, Card, SectionTitle } from "@/components/ui";
import { ErrorState } from "@/components/error";
import { Skeleton, StatCardsSkeleton, TxnListSkeleton } from "@/components/skeleton";
import { dataIcon } from "@/components/icons";
import { fmtDate } from "@/lib/utils";
import { sumCents } from "@/lib/decimal";
import { sumBaseAmounts, baseCurrencyOf } from "@/lib/aggregate";
import { currentLedgerMonthKey, ledgerMonthKey } from "@/lib/ledger-date";
import { accountDisplayName } from "@/lib/privacy";
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

/** 交易涉及的第一个真实账户名（损益行 accountId=null 跳过；private:counterparty 只显示脱敏占位） */
function mainAccountName(t: Transaction, accts: Account[]): string {
  const line = t.lines.find((l) => l.accountId !== null);
  return line?.accountId ? accountDisplayName(line.accountId, accts, () => "—") : "—";
}

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

function DashboardContent({ data }: { data: DashboardData }) {
  const { txns, accts, cats, batches, splitList, backupList } = data;
  const categoryName = (id: string | null) =>
    id === null ? "未分类" : (cats.find((c) => c.id === id)?.name ?? "未分类");

  // 统计由 listTransactions()+listAccounts() 前端聚合（总资产/负债只计 CNY 账户，
  // 外币不折算、另行展示）；如后端提供聚合接口（如 GET /dashboard/summary），此处以下渲染不变
  const totalAssetCents = sumCents(
    accts.filter((a) => a.kind === "asset" && a.currency === "CNY").map((a) => a.balanceCents),
  );
  const totalLiabilityCents = sumCents(
    accts.filter((a) => a.kind === "liability" && a.currency === "CNY").map((a) => a.balanceCents),
  );
  // 「本月」= 当前上海自然月（Asia/Shanghai 口径，禁止对原始 ISO slice/startsWith）
  const month = currentLedgerMonthKey();
  const [y, m] = month.split("-");
  const monthLabel = `${y} 年 ${Number(m)} 月`;
  // 跨交易统计一律按基础币 baseAmountCents 合计、以 baseCurrency 展示（bigint 超界保护）
  const monthIncome = sumBaseAmounts(
    txns.filter((t) => t.type === "income" && ledgerMonthKey(t.date) === month),
  );
  // 本月净支出 = 支出 − 退款（退款按自身账期归月，是反向支出而非收入；净值允许为零或负数，如实显示）
  const monthExpenseGross = sumBaseAmounts(
    txns.filter((t) => t.type === "expense" && ledgerMonthKey(t.date) === month),
  );
  const monthRefund = sumBaseAmounts(
    txns.filter((t) => t.type === "refund" && ledgerMonthKey(t.date) === month),
  );
  const monthNetExpense = {
    cents: monthExpenseGross.cents - monthRefund.cents,
    currency: baseCurrencyOf(txns.filter((t) => ledgerMonthKey(t.date) === month)),
  };

  // 按分类净支出：退款的 categoryId 与原支出一致，直接按其 categoryId 冲减
  const expenseByCat = cats
    .filter((c) => c.kind === "expense" && c.active)
    .map((c) => ({
      cat: c,
      total:
        sumCents(
          txns.filter((t) => t.type === "expense" && t.categoryId === c.id).map((t) => t.baseAmountCents),
        ) -
        sumCents(
          txns.filter((t) => t.type === "refund" && t.categoryId === c.id).map((t) => t.baseAmountCents),
        ),
    }))
    .filter((x) => x.total > 0n)
    // bigint 比较排序（不做 number 减法，避免超界失真）
    .sort((a, b) => (a.total === b.total ? 0 : b.total > a.total ? 1 : -1));
  const maxCat = expenseByCat[0]?.total ?? 1n;

  const reviewingBatches = batches.filter((b) => b.status === "reviewing");
  const openSplits = splitList.filter((s) => s.status === "open");
  const latestBackup = backupList[0];

  const recent = [...txns].sort((a, b) => b.date.localeCompare(a.date)).slice(0, 8);

  const statCards = [
    { label: "总资产", cents: totalAssetCents, currency: "CNY", icon: Landmark, note: "人民币资产账户合计（外币另计）" },
    { label: "总负债", cents: totalLiabilityCents, currency: "CNY", icon: CreditCard, note: "含房贷" },
    { label: "本月收入", cents: monthIncome.cents, currency: monthIncome.currency, icon: TrendingUp, note: monthLabel },
    { label: "本月净支出", cents: monthNetExpense.cents, currency: monthNetExpense.currency, icon: ShoppingBag, note: monthLabel },
  ];

  return (
    <>
      {/* 统计卡 */}
      <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
        {statCards.map(({ label, cents, currency, icon: Icon, note }) => (
          <Card key={label} className="p-5 transition-shadow hover:shadow-pop">
            <div className="flex items-center gap-2 text-xs text-ink-faint">
              <span className="flex h-6 w-6 items-center justify-center rounded-md bg-accent-soft text-accent-ink">
                <Icon className="h-3.5 w-3.5" />
              </span>
              {label}
            </div>
            <p className="mt-2 text-[1.35rem] font-medium leading-tight tracking-tight">
              <Amount
                cents={cents}
                currency={currency}
                kind={label === "本月收入" ? "income" : label === "本月净支出" ? "expense" : "neutral"}
              />
            </p>
            <p className="mt-1 text-xs text-ink-faint">{note}</p>
          </Card>
        ))}
      </div>

      {/* 分类支出 + 待办 */}
      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <Card className="p-5">
          <SectionTitle title="按分类支出" />
          <div className="space-y-3">
            {expenseByCat.map(({ cat, total }) => {
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
                      // 仅绘图比例允许有界近似：比值 ≤1，先放大 1e4 再整除，结果必然安全
                      style={{ width: `${Math.max(4, Number((total * 10000n) / maxCat) / 100)}%` }}
                    />
                  </div>
                  <span className="w-24 shrink-0 text-right text-sm">
                    <Amount cents={total} />
                  </span>
                </div>
              );
            })}
          </div>
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
            {recent.map((t) => {
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
      </Card>
    </>
  );
}

export default function DashboardPage() {
  // 统计与最近交易由 listTransactions()+listAccounts() 前端聚合；上线后换后端聚合接口
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

  return (
    <div>
      <AdminHeader title="仪表盘" desc="家庭财务总览" />
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
        <DashboardContent data={data} />
      ) : null}
    </div>
  );
}
