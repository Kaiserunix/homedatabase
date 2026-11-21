import { CalendarClock, Coins, Scale, TrendingDown, TrendingUp } from "lucide-react";
import { Amount, Badge, Button, Card } from "@/components/ui";
import { formatCompactMonthLabel, type LedgerOverview } from "@/domain/ledger-insights";

const absBigInt = (v: bigint): bigint => (v < 0n ? -v : v);

export interface CashflowInsightCardProps {
  overview: LedgerOverview;
  currentMonth: string;
  latestActiveMonth: string | null;
  onSelectMonth: (month: string) => void;
}

/**
 * 纯展示组件：确定性现金流脉搏、环比支出核对、人民币净资产与外币账户原币余额一览。
 * 所有数值均由上层确定性 BigInt 选择器基于真实账本计算，零虚构预测、零浮点误差。
 */
export function CashflowInsightCard({
  overview,
  currentMonth,
  latestActiveMonth,
  onSelectMonth,
}: CashflowInsightCardProps) {
  const {
    month,
    prevMonth,
    baseCurrency,
    netWorthCents,
    foreignAccounts,
    monthTxnCount,
    monthRefundCents,
    monthNetCashflowCents,
    momExpenseDeltaCents,
  } = overview;

  const showEmptyCurrentMonthHint =
    month === currentMonth &&
    monthTxnCount === 0 &&
    latestActiveMonth !== null &&
    latestActiveMonth !== currentMonth;

  return (
    <div className="mt-4 space-y-3">
      {showEmptyCurrentMonthHint && (
        <Card strong className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
          <div className="flex items-center gap-2.5 text-xs text-ink-soft">
            <CalendarClock className="h-4 w-4 shrink-0 text-accent" />
            <span>
              当前自然月（{formatCompactMonthLabel(currentMonth)}）暂无收支记录；账本最近有记录的账期为{" "}
              <strong className="font-medium text-ink">
                {formatCompactMonthLabel(latestActiveMonth)}
              </strong>
              。
            </span>
          </div>
          <Button size="sm" variant="secondary" onClick={() => onSelectMonth(latestActiveMonth)}>
            切换至 {formatCompactMonthLabel(latestActiveMonth)}
          </Button>
        </Card>
      )}

      <Card className="p-4">
        <div className="grid gap-4 md:grid-cols-3">
          {/* 1. 当期现金流净结余 */}
          <div className="space-y-1">
            <div className="flex items-center gap-1.5 text-xs text-ink-faint">
              <Scale className="h-3.5 w-3.5 text-accent" />
              <span>账期收支结余（收入 − 净支出）</span>
            </div>
            <p className="flex items-center gap-1.5 text-base font-medium">
              <Badge
                tone={
                  monthNetCashflowCents > 0n
                    ? "ok"
                    : monthNetCashflowCents < 0n
                      ? "warn"
                      : "neutral"
                }
              >
                {monthNetCashflowCents > 0n
                  ? "净结余"
                  : monthNetCashflowCents < 0n
                    ? "净流出"
                    : "收支平衡"}
              </Badge>
              <Amount
                cents={absBigInt(monthNetCashflowCents)}
                currency={baseCurrency}
                className={
                  monthNetCashflowCents > 0n
                    ? "text-accent"
                    : monthNetCashflowCents < 0n
                      ? "text-ink"
                      : "text-ink-soft"
                }
              />
            </p>
            <p className="text-[11px] text-ink-faint">
              {formatCompactMonthLabel(month)}共 {monthTxnCount} 笔交易
              {monthRefundCents > 0n && (
                <>
                  {" "}· 已冲减退款 <Amount cents={monthRefundCents} currency={baseCurrency} />
                </>
              )}
            </p>
          </div>

          {/* 2. 环比上月净支出对比 */}
          <div className="space-y-1 md:border-l md:border-slate-900/5 md:pl-4">
            <div className="flex items-center gap-1.5 text-xs text-ink-faint">
              {momExpenseDeltaCents !== null && momExpenseDeltaCents <= 0n ? (
                <TrendingDown className="h-3.5 w-3.5 text-accent" />
              ) : (
                <TrendingUp className="h-3.5 w-3.5 text-ink-soft" />
              )}
              <span>较上月净支出（{formatCompactMonthLabel(prevMonth)}）</span>
            </div>
            {momExpenseDeltaCents === null ? (
              <>
                <p className="text-sm font-medium text-ink-soft">暂无上月基准</p>
                <p className="text-[11px] text-ink-faint">
                  {formatCompactMonthLabel(prevMonth)}无收支记录，不作环比推算
                </p>
              </>
            ) : momExpenseDeltaCents === 0n ? (
              <>
                <p className="text-sm font-medium text-ink">与上月持平</p>
                <p className="text-[11px] text-ink-faint">两期净支出完全一致</p>
              </>
            ) : (
              <>
                <p className="flex items-center gap-1.5 text-base font-medium">
                  <Badge tone={momExpenseDeltaCents > 0n ? "warn" : "ok"}>
                    {momExpenseDeltaCents > 0n ? "多支出" : "少支出"}
                  </Badge>
                  <Amount
                    cents={absBigInt(momExpenseDeltaCents)}
                    currency={baseCurrency}
                    className={momExpenseDeltaCents > 0n ? "text-warn" : "text-accent"}
                  />
                </p>
                <p className="text-[11px] text-ink-faint">按上海账期月确定性核算</p>
              </>
            )}
          </div>

          {/* 3. 人民币净资产 + 外币账户原币余额 */}
          <div className="space-y-1 md:border-l md:border-slate-900/5 md:pl-4">
            <div className="flex items-center gap-1.5 text-xs text-ink-faint">
              <Coins className="h-3.5 w-3.5 text-accent" />
              <span>人民币净资产（资产 − 负债）</span>
            </div>
            <p className="flex items-center gap-1.5 text-base font-medium">
              {netWorthCents < 0n && <Badge tone="neutral">净负债</Badge>}
              <Amount cents={absBigInt(netWorthCents)} currency="CNY" />
            </p>
            {foreignAccounts.length > 0 ? (
              <div className="flex flex-wrap items-center gap-1.5 pt-0.5 text-[11px] text-ink-soft">
                <span className="text-ink-faint">外币账户原币：</span>
                {foreignAccounts.map((acct) => (
                  <Badge key={acct.id} tone="neutral">
                    {acct.name}{" "}
                    <Amount
                      cents={acct.kind === "liability" ? -BigInt(acct.balanceCents) : acct.balanceCents}
                      currency={acct.currency}
                    />
                  </Badge>
                ))}
              </div>
            ) : (
              <p className="text-[11px] text-ink-faint">当前无外币账户</p>
            )}
          </div>
        </div>
      </Card>
    </div>
  );
}
