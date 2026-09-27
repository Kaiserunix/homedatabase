import { useState } from "react";
import { Bot, Download, FileCheck2, TrendingUp } from "lucide-react";
import { Badge, Button, Card } from "@/components/ui";
import { Select, SelectContent, SelectItem, SelectTrigger } from "@/components/select";
import { Skeleton } from "@/components/skeleton";
import { ErrorState } from "@/components/error";
import { useApiQuery } from "@/api/useApi";
import { exportReport, listReportTiers, type ReportRange } from "@/api/resources";
import { ApiError } from "@/api/client";
import type { ReportTier } from "@/data/types";
import {
  currentLedgerDateKey, currentLedgerMonthKey, ledgerMonthRange,
  shiftLedgerDateKey, shiftLedgerMonthKey,
} from "@/lib/ledger-date";
import { AdminHeader } from "../components/AdminHeader";

/** tier 备注；tier 5 是一个含五张工作表与图表的完整 XLSX（递进工作表语义），不是五个独立文件 */
const tierNotes: Record<number, string> = {
  3: "含借贷分录明细，借贷必平衡",
  4: "与交易明细勾稽，差额为 0",
  5: "一个 XLSX 内含五张工作表与图表，逐级递进汇总",
};

type Period = "this-month" | "last-month" | "last-90" | "custom";

/** 期间选择 → 导出范围（真实日期，上海口径；自定义需起止齐全且 from ≤ to，否则返回 null） */
function rangeOf(period: Period, customFrom: string, customTo: string): ReportRange | null {
  if (period === "this-month") {
    return { from: `${currentLedgerMonthKey()}-01`, to: currentLedgerDateKey() };
  }
  if (period === "last-month") {
    return ledgerMonthRange(shiftLedgerMonthKey(currentLedgerMonthKey(), -1));
  }
  if (period === "last-90") {
    const today = currentLedgerDateKey();
    return { from: shiftLedgerDateKey(today, -89), to: today };
  }
  if (!customFrom || !customTo || customFrom > customTo) return null;
  return { from: customFrom, to: customTo };
}

const periodLabel: Record<Period, string> = {
  "this-month": "本月",
  "last-month": "上月",
  "last-90": "近 90 天",
  custom: "自定义",
};

/** 层级卡骨架：与下方报表卡片结构大致对齐 */
function TierCardsSkeleton({ count = 5 }: { count?: number }) {
  return (
    <div className="space-y-3" aria-busy="true" aria-label="加载中">
      {Array.from({ length: count }, (_, i) => (
        <Card key={i} className="flex items-center gap-4 p-4">
          <Skeleton className="h-8 w-10 shrink-0" />
          <div className="min-w-0 flex-1 space-y-2">
            <Skeleton className="h-4 w-40" />
            <Skeleton className="h-3 w-3/4" />
          </div>
          <Skeleton className="h-9 w-28 shrink-0 rounded-xl" />
        </Card>
      ))}
    </div>
  );
}

export default function ReportsPage() {
  const [period, setPeriod] = useState<Period>("this-month");
  const [customFrom, setCustomFrom] = useState("");
  const [customTo, setCustomTo] = useState("");
  const [generated, setGenerated] = useState<Record<number, boolean>>({});
  const [busyTier, setBusyTier] = useState<number | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);
  const { data: tiers, loading, error, retry } = useApiQuery(() => listReportTiers(), []);

  const range = rangeOf(period, customFrom, customTo);

  const exportTier = async (tier: ReportTier) => {
    if (!range || busyTier !== null) return;
    setBusyTier(tier);
    setExportError(null);
    try {
      // 文件由后端生成，前端原样下载 Blob，不在浏览器重算账目
      const blob = await exportReport(tier, range);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `homedatabase-tier${tier}-${range.from}_${range.to}.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      setGenerated((g) => ({ ...g, [tier]: true }));
    } catch (e) {
      setExportError(e instanceof ApiError ? e.message : "导出失败，请稍后重试");
    } finally {
      setBusyTier(null);
    }
  };

  return (
    <>
      <AdminHeader title="报表导出" desc="XLSX 五个层级，从简单阅读到专业分析；文件由后端生成" />

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Select value={period} onValueChange={(v) => setPeriod(v as Period)}>
          <SelectTrigger className="w-44" />
          <SelectContent>
            {(Object.keys(periodLabel) as Period[]).map((p) => (
              <SelectItem key={p} value={p}>{periodLabel[p]}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        {period === "custom" && (
          <>
            <input
              type="date"
              value={customFrom}
              onChange={(e) => setCustomFrom(e.target.value)}
              className="glass h-10 rounded-xl px-3 text-sm text-ink outline-none"
              aria-label="开始日期"
            />
            <span className="text-xs text-ink-faint">至</span>
            <input
              type="date"
              value={customTo}
              onChange={(e) => setCustomTo(e.target.value)}
              className="glass h-10 rounded-xl px-3 text-sm text-ink outline-none"
              aria-label="结束日期"
            />
          </>
        )}
        {range && (
          <span className="text-xs text-ink-faint">
            导出范围 {range.from} ~ {range.to}
          </span>
        )}
      </div>

      {exportError && (
        <p role="alert" className="mb-4 rounded-xl bg-danger/10 px-3 py-2 text-xs text-danger">
          {exportError}
        </p>
      )}

      {loading ? (
        <TierCardsSkeleton />
      ) : error ? (
        <Card>
          <ErrorState title="报表层级加载失败" desc={error.message} onRetry={retry} />
        </Card>
      ) : (
        <div className="space-y-3">
          {(tiers ?? []).map((t) => (
            <Card key={t.tier} className="flex items-center gap-4 p-4">
              <span className="w-10 shrink-0 text-center font-num text-3xl font-semibold tabular-nums text-accent">
                {t.tier}
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <p className="text-sm font-semibold text-ink">{t.name}</p>
                  <Badge>{t.audience}</Badge>
                </div>
                <p className="mt-1 text-sm text-ink-soft">{t.description}</p>
                {tierNotes[t.tier] && <p className="mt-1 text-xs text-ink-faint">{tierNotes[t.tier]}</p>}
              </div>
              <Button
                variant="secondary"
                className="shrink-0"
                disabled={!range || busyTier !== null}
                onClick={() => void exportTier(t.tier)}
              >
                {busyTier === t.tier ? (
                  "导出中…"
                ) : generated[t.tier] ? (
                  <>
                    <FileCheck2 className="h-4 w-4 text-accent" /> 再次导出
                  </>
                ) : (
                  <>
                    <Download className="h-4 w-4" /> 导出 XLSX
                  </>
                )}
              </Button>
            </Card>
          ))}
        </div>
      )}

      <Card className="mt-4 p-4">
        <p className="text-sm font-semibold text-ink">导出说明</p>
        <p className="mt-1 text-xs leading-5 text-ink-soft">
          报表由后端按所选期间即时生成并直接下载；层级越高内容越丰富，第 5 层是一个包含五张工作表与图表的完整
          XLSX 文件，逐级递进汇总，无需分别导出。
        </p>
        <div className="mt-3 space-y-2 border-t border-slate-900/5 pt-3">
          {[
            { icon: Bot, label: "AI 问账本" },
            { icon: TrendingUp, label: "预算与预测" },
          ].map(({ icon: Icon, label }) => (
            <div key={label} className="flex items-center gap-2 opacity-50">
              <Icon className="h-4 w-4 text-ink-faint" />
              <span className="flex-1 text-sm text-ink-soft">{label}</span>
              <Badge>即将推出</Badge>
            </div>
          ))}
        </div>
      </Card>
    </>
  );
}
