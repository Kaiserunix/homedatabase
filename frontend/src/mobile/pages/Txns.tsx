import { useMemo, useState, type CSSProperties } from "react";
import { ArrowLeftRight, Receipt } from "lucide-react";
import { Amount, Badge, Card, EmptyState } from "@/components/ui";
import { Dialog, DialogContent, DialogHeader } from "@/components/dialog";
import { Tabs, TabsList, TabsTrigger } from "@/components/tabs";
import { Select, SelectContent, SelectItem, SelectTrigger } from "@/components/select";
import { TxnDetailContent } from "@/components/TxnDetailContent";
import { TxnListSkeleton } from "@/components/skeleton";
import { ErrorState } from "@/components/error";
import { dataIcon } from "@/components/icons";
import { ledgerDateKey, ledgerMonthKey } from "@/lib/ledger-date";
import { baseCurrencyOf } from "@/lib/aggregate";
import { sumCents } from "@/lib/decimal";
import { useApiQuery } from "@/api/useApi";
import {
  listAccounts, listCategories, listImportBatches, listMembers, listSplits, listTransactions,
} from "@/api/resources";
import type { Category, Transaction } from "@/data/types";
import { PageHeader } from "../components/PageHeader";

const amountKind = (t: Transaction): "expense" | "income" | "neutral" =>
  t.type === "expense" ? "expense" : t.type === "income" || t.type === "refund" ? "income" : "neutral";

function TxnRow({ t, cats, onClick }: { t: Transaction; cats: Category[]; onClick: () => void }) {
  const cat = cats.find((c) => c.id === t.categoryId);
  const Icon = t.type === "transfer" ? ArrowLeftRight : dataIcon(cat?.icon);
  return (
    <button
      onClick={onClick}
      className="flex w-full items-center gap-3 py-3 text-left transition active:bg-slate-900/[0.03]"
    >
      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-accent-soft text-accent">
        <Icon className="h-5 w-5" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-1.5">
          <span className="truncate text-sm font-medium text-ink">{t.payee}</span>
          {t.type === "refund" && <Badge tone="warn">退款</Badge>}
        </span>
        <span className="block truncate text-xs text-ink-faint">
          {t.note || (t.type === "transfer" ? "转账" : cat?.name)}
        </span>
      </span>
      <Amount cents={t.amountCents} kind={amountKind(t)} currency={t.currency} className="shrink-0 text-sm" />
    </button>
  );
}

type Filter = "all" | Transaction["type"];
const filters: { key: Filter; label: string }[] = [
  { key: "all", label: "全部" },
  { key: "expense", label: "支出" },
  { key: "income", label: "收入" },
  { key: "transfer", label: "转账" },
  { key: "refund", label: "退款" },
];

const monthLabel = (m: string) => `${Number(m.slice(0, 4))}年${Number(m.slice(5, 7))}月`;

/** 日分组标题：ledgerDateKey 产出的 "YYYY-MM-DD" 键已是上海口径，直接拆键格式化，不再过 Date */
const dayLabel = (day: string) => {
  const [, m, d] = day.split("-");
  return `${Number(m)}月${Number(d)}日`;
};

export default function TxnsPage() {
  const [filter, setFilter] = useState<Filter>("all");
  // "all" = 全部月份；具体值形如 2026-07
  const [month, setMonth] = useState("all");
  const [selected, setSelected] = useState<Transaction | null>(null);
  // 一次取齐：流水 + 详情弹窗所需的名称解析数据（账户/分类/成员/批次/分摊）
  const { data, loading, error, retry } = useApiQuery(
    () =>
      Promise.all([
        listTransactions(), listAccounts(), listCategories(), listMembers(),
        listImportBatches(), listSplits(),
      ]).then(
        ([txns, accts, cats, members, batches, splitList]) =>
          ({ txns, accts, cats, members, batches, splitList }),
      ),
    [],
  );
  const txns = data?.txns ?? [];

  // 可选月份由流水数据推导（新→旧），不再写死；归属按上海账期口径
  const months = useMemo(
    () => [...new Set(txns.map((t) => ledgerMonthKey(t.date)))].sort((a, b) => b.localeCompare(a)),
    [txns],
  );

  const groups = useMemo(() => {
    const list = txns
      .filter((t) => (filter === "all" || t.type === filter) && (month === "all" || ledgerMonthKey(t.date) === month))
      .sort((a, b) => b.date.localeCompare(a.date));
    const map = new Map<string, Transaction[]>();
    for (const t of list) {
      const day = ledgerDateKey(t.date);
      if (!map.has(day)) map.set(day, []);
      map.get(day)!.push(t);
    }
    return [...map.entries()];
  }, [filter, month, txns]);

  return (
    <div className="px-4">
      <PageHeader title="流水" />

      {/* 筛选 */}
      <div className="mb-4 flex items-center gap-2">
        <Tabs value={filter} onValueChange={(v) => setFilter(v as Filter)} className="min-w-0 flex-1">
          <TabsList className="w-full">
            {filters.map((f) => (
              <TabsTrigger key={f.key} value={f.key} className="flex-1 px-1">
                {f.label}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
        <Select value={month} onValueChange={setMonth}>
          <SelectTrigger className="w-32 shrink-0" />
          <SelectContent>
            <SelectItem value="all">全部月份</SelectItem>
            {months.map((m) => (
              <SelectItem key={m} value={m}>
                {monthLabel(m)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* 按日期分组：骨架 / 错误 / 列表互斥渲染 */}
      {loading ? (
        <TxnListSkeleton />
      ) : error ? (
        <ErrorState
          title="流水加载失败"
          desc={error.message}
          onRetry={retry}
        />
      ) : groups.length === 0 ? (
        <EmptyState
          icon={<Receipt className="h-8 w-8" />}
          title="没有符合条件的流水"
          hint="换个筛选条件或月份试试"
        />
      ) : (
        // 筛选条件变化时以 key 重挂载列表，逐项重新进场提示内容已更新
        <div key={`${filter}-${month}`} className="stagger space-y-4">
          {groups.map(([day, list], gi) => {
            // 当日净支出 = 支出 − 退款（退款按自身账期日归组，是反向支出而非收入；净值允许为零或负数）；
            // 按基础币 baseAmountCents 合计、以 baseCurrency 展示，基础币不一致由 baseCurrencyOf 抛错防御
            const dayNetExpense = {
              cents:
                sumCents(list.filter((t) => t.type === "expense").map((t) => t.baseAmountCents)) -
                sumCents(list.filter((t) => t.type === "refund").map((t) => t.baseAmountCents)),
              currency: baseCurrencyOf(list),
            };
            const hasExpenseOrRefund = list.some((t) => t.type === "expense" || t.type === "refund");
            return (
              <div key={day} style={{ "--stagger-i": gi } as CSSProperties}>
                <div className="mb-1 flex items-center justify-between px-1">
                  <span className="text-xs font-medium text-ink-soft">{dayLabel(day)}</span>
                  {hasExpenseOrRefund && (
                    <span className="text-xs text-ink-faint">
                      当日净支出 <Amount cents={dayNetExpense.cents} currency={dayNetExpense.currency} kind="expense" />
                    </span>
                  )}
                </div>
                <Card className="divide-y divide-slate-900/5 px-4">
                  {list.map((t) => (
                    <TxnRow key={t.id} t={t} cats={data?.cats ?? []} onClick={() => setSelected(t)} />
                  ))}
                </Card>
              </div>
            );
          })}
        </div>
      )}

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
