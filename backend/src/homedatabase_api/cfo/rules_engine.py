"""Pluggable Financial-Health Rules Engine and top-level AI CFO Analytical Orchestrator."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from .cashflow import (
    CashflowMetricsSummary,
    SpendChangeAttribution,
    compute_cashflow_metrics,
    explain_spend_change,
)
from .detectors import (
    DetectedAnomaly,
    DetectorConfig,
    RecurringCharge,
    detect_recurring_charges,
    detect_spending_anomalies,
)
from .domain import (
    BPS_SCALE,
    LedgerSnapshot,
    SeverityLevel,
    YearMonth,
    format_minor_units,
    ratio_to_bps,
    scale_minor_by_bps,
)
from .forecast import (
    CashflowForecastResult,
    CategoryBudgetEvaluation,
    WhatIfOptionOutcome,
    WhatIfPlanOption,
    compare_what_if_plans,
    evaluate_category_budgets,
    forecast_cashflow,
)

HealthGrade = Literal["A", "B", "C", "D"]
RuleStatus = Literal["healthy", "watch", "critical"]


@dataclass(frozen=True, slots=True)
class CFORulesConfig:
    healthy_runway_months_bps: int = 60_000
    minimum_runway_months_bps: int = 30_000
    healthy_savings_rate_bps: int = 2_000
    watch_debt_to_cash_bps: int = 5_000
    critical_debt_to_cash_bps: int = 10_000
    watch_subscription_share_bps: int = 1_500
    critical_subscription_share_bps: int = 3_000
    uncategorized_watch_bps: int = 1_500
    forecast_horizon_days: int = 90
    forecast_emergency_months_floor_bps: int = 30_000


@dataclass(frozen=True, slots=True)
class CFOAnalysisContext:
    snapshot: LedgerSnapshot
    focus_month: YearMonth
    metrics: CashflowMetricsSummary
    spend_change: SpendChangeAttribution
    recurring_charges: tuple[RecurringCharge, ...]
    anomalies: tuple[DetectedAnomaly, ...]
    budget_evaluations: tuple[CategoryBudgetEvaluation, ...]
    forecast: CashflowForecastResult
    config: CFORulesConfig


@dataclass(frozen=True, slots=True)
class RuleFinding:
    rule_id: str
    dimension: Literal["liquidity", "cashflow", "debt", "subscriptions", "budget", "hygiene"]
    status: RuleStatus
    severity: SeverityLevel
    score_delta: int
    title: str
    summary: str
    recommended_action: str | None = None
    metric_minor: int | None = None
    metric_bps: int | None = None
    evidence_txn_ids: tuple[str, ...] = ()


@runtime_checkable
class FinancialHealthRule(Protocol):
    """Pluggable pure domain rule evaluated over a read-only CFOAnalysisContext."""

    @property
    def rule_id(self) -> str: ...

    def evaluate(self, context: CFOAnalysisContext) -> RuleFinding | None: ...


@dataclass(frozen=True, slots=True)
class FinancialHealthScorecard:
    score: int
    grade: HealthGrade
    findings: tuple[RuleFinding, ...]
    actionable_steps: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CFOReport:
    as_of_iso: str
    focus_month: YearMonth
    base_currency: str
    scope_label: str
    viewer_member_id: str | None
    metrics: CashflowMetricsSummary
    spend_change: SpendChangeAttribution
    recurring_charges: tuple[RecurringCharge, ...]
    anomalies: tuple[DetectedAnomaly, ...]
    budget_evaluations: tuple[CategoryBudgetEvaluation, ...]
    forecast: CashflowForecastResult
    what_if_outcomes: tuple[WhatIfOptionOutcome, ...]
    scorecard: FinancialHealthScorecard

    def all_referenced_txn_ids(self) -> frozenset[str]:
        ids: set[str] = set()
        for pt in self.metrics.monthly_series:
            ids.update(pt.evidence_txn_ids)
        for cat in self.metrics.focus_categories:
            ids.update(cat.evidence_txn_ids)
        for drv in (*self.spend_change.top_increases, *self.spend_change.top_decreases):
            ids.update(drv.evidence_txn_ids)
        for rec in self.recurring_charges:
            ids.update(rec.evidence_txn_ids)
        for anom in self.anomalies:
            ids.update(anom.evidence_txn_ids)
        for bud in self.budget_evaluations:
            ids.update(bud.evidence_txn_ids)
        for finding in self.scorecard.findings:
            ids.update(finding.evidence_txn_ids)
        return frozenset(ids)


class EmergencyFundRunwayRule:
    rule_id = "liquidity.emergency_runway"

    def evaluate(self, context: CFOAnalysisContext) -> RuleFinding | None:
        runway_bps = context.metrics.emergency_runway_months_bps
        liquid = context.metrics.liquidity.liquid_assets_minor
        if runway_bps is None:
            if liquid <= 0 and context.snapshot.accounts:
                return RuleFinding(
                    rule_id=self.rule_id,
                    dimension="liquidity",
                    status="watch",
                    severity="warning",
                    score_delta=-10,
                    title="流动现金储备为零",
                    summary="当前可见资产账户尚无可用正余额，建议先核对期初余额或导入主账户流水。",
                    recommended_action="检查家庭主储蓄账户余额并设定应急金目标。",
                    metric_minor=liquid,
                )
            return None

        months_txt = f"{runway_bps / BPS_SCALE:.1f}"
        if runway_bps >= context.config.healthy_runway_months_bps:
            return RuleFinding(
                rule_id=self.rule_id,
                dimension="liquidity",
                status="healthy",
                severity="info",
                score_delta=0,
                title="应急金覆盖充足",
                summary=f"当前流动现金可覆盖约 {months_txt} 个月日常净支出，高于 6 个月安全线。",
                metric_minor=liquid,
                metric_bps=runway_bps,
            )
        if runway_bps >= context.config.minimum_runway_months_bps:
            return RuleFinding(
                rule_id=self.rule_id,
                dimension="liquidity",
                status="watch",
                severity="warning",
                score_delta=-10,
                title="应急金处于观察区间",
                summary=f"当前流动现金可覆盖约 {months_txt} 个月净支出，低于 6 个月目标缓冲。",
                recommended_action="建议每月优先拨备结余，逐步补齐至 6 个月固定生活费。",
                metric_minor=liquid,
                metric_bps=runway_bps,
            )
        return RuleFinding(
            rule_id=self.rule_id,
            dimension="liquidity",
            status="critical",
            severity="high",
            score_delta=-25,
            title="应急现金缓冲偏紧",
            summary=f"当前流动现金仅够覆盖约 {months_txt} 个月净支出，不足 3 个月底线。",
            recommended_action="暂停非刚需大额支出，优先建立 3 个月流动性安全垫。",
            metric_minor=liquid,
            metric_bps=runway_bps,
        )


class SavingsRateRule:
    rule_id = "cashflow.savings_rate"

    def evaluate(self, context: CFOAnalysisContext) -> RuleFinding | None:
        pt = context.metrics.focus_month_point
        if pt.transaction_count == 0:
            return None

        if pt.income_minor == 0 and pt.net_expense_minor > 0:
            return RuleFinding(
                rule_id=self.rule_id,
                dimension="cashflow",
                status="watch",
                severity="warning",
                score_delta=-12,
                title=f"{pt.month} 仅有支出未见收入入账",
                summary=(
                    f"{pt.month} 已记录净支出 {format_minor_units(pt.net_expense_minor)} "
                    f"{context.snapshot.base_currency}，但尚未录入收入流水。"
                ),
                recommended_action="确认工资或家庭收入账户是否尚未导入，避免储蓄率失真。",
                metric_minor=pt.net_savings_minor,
                evidence_txn_ids=pt.evidence_txn_ids[:10],
            )

        rate_bps = pt.savings_rate_bps
        if rate_bps is None:
            return None

        pct_txt = f"{rate_bps / 100:.1f}%"
        if rate_bps < 0:
            return RuleFinding(
                rule_id=self.rule_id,
                dimension="cashflow",
                status="critical",
                severity="high",
                score_delta=-20,
                title=f"{pt.month} 出现收支赤字",
                summary=(
                    f"{pt.month} 净结余为 {format_minor_units(pt.net_savings_minor)} "
                    f"{context.snapshot.base_currency}（储蓄率 {pct_txt}），当月入不敷出。"
                ),
                recommended_action="复盘当月涨幅最大的前三个分类，区分一次性大额支出与惯性超支。",
                metric_minor=pt.net_savings_minor,
                metric_bps=rate_bps,
                evidence_txn_ids=pt.evidence_txn_ids[:10],
            )
        if rate_bps < context.config.healthy_savings_rate_bps:
            return RuleFinding(
                rule_id=self.rule_id,
                dimension="cashflow",
                status="watch",
                severity="info",
                score_delta=-6,
                title=f"{pt.month} 储蓄率偏薄（{pct_txt}）",
                summary=f"{pt.month} 保持正结余，但储蓄率低于 20% 稳健水平。",
                recommended_action="可为高频弹性分类设定月度预算提醒，提升月度结余率。",
                metric_minor=pt.net_savings_minor,
                metric_bps=rate_bps,
            )
        return RuleFinding(
            rule_id=self.rule_id,
            dimension="cashflow",
            status="healthy",
            severity="info",
            score_delta=0,
            title=f"{pt.month} 储蓄率稳健（{pct_txt}）",
            summary=(
                f"{pt.month} 净结余 {format_minor_units(pt.net_savings_minor)} "
                f"{context.snapshot.base_currency}，收支结构健康。"
            ),
            metric_minor=pt.net_savings_minor,
            metric_bps=rate_bps,
        )


class DebtToCashRule:
    rule_id = "debt.liability_coverage"

    def evaluate(self, context: CFOAnalysisContext) -> RuleFinding | None:
        liq = context.metrics.liquidity
        debt = max(0, liq.total_liabilities_minor)
        if debt == 0:
            return None
        bps = liq.debt_to_liquid_cash_bps or 0
        pct_txt = f"{bps / 100:.1f}%"
        if bps >= context.config.critical_debt_to_cash_bps:
            return RuleFinding(
                rule_id=self.rule_id,
                dimension="debt",
                status="critical",
                severity="high",
                score_delta=-20,
                title="负债余额超过可用流动现金",
                summary=(
                    f"当前信用卡及负债余额 {format_minor_units(debt)} {liq.base_currency} "
                    f"已达流动现金的 {pct_txt}。"
                ),
                recommended_action="优先预留账单日还款资金，避免产生循环利息或逾期。",
                metric_minor=debt,
                metric_bps=bps,
            )
        if bps >= context.config.watch_debt_to_cash_bps:
            return RuleFinding(
                rule_id=self.rule_id,
                dimension="debt",
                status="watch",
                severity="warning",
                score_delta=-8,
                title="负债占流动现金比例偏高",
                summary=f"当前负债余额占可用流动现金的 {pct_txt}，需留意近期还款节点。",
                recommended_action="核对信用卡出账日与大额分摊回款进度。",
                metric_minor=debt,
                metric_bps=bps,
            )
        return None


class SubscriptionBloatRule:
    rule_id = "subscriptions.recurring_burden"

    def evaluate(self, context: CFOAnalysisContext) -> RuleFinding | None:
        active_subs = [
            r
            for r in context.recurring_charges
            if r.direction == "expense" and r.status == "active"
        ]
        if not active_subs:
            return None

        annual_total = sum(r.annualized_amount_minor for r in active_subs)
        monthly_equiv = annual_total // 12
        denom = (
            context.metrics.rolling_monthly_income_minor
            or context.metrics.rolling_monthly_burn_minor
        )
        share_bps = ratio_to_bps(monthly_equiv, denom) if denom > 0 else None
        hiked = [r for r in active_subs if r.price_hike_minor > 0]
        evidence = tuple(dict.fromkeys(tid for r in active_subs for tid in r.evidence_txn_ids[-2:]))

        if hiked:
            names = "、".join(r.display_payee for r in hiked[:3])
            return RuleFinding(
                rule_id=self.rule_id,
                dimension="subscriptions",
                status="watch",
                severity="warning",
                score_delta=-8,
                title=f"检测到周期账单涨价：{names}",
                summary=(
                    f"识别到 {len(active_subs)} 项活跃周期支出（折合年化 "
                    f"{format_minor_units(annual_total)} {context.snapshot.base_currency}），"
                    f"其中 {len(hiked)} 项最近一期扣费高于历史中位数。"
                ),
                recommended_action="检查自动续费套餐变更，清理低频使用的订阅服务。",
                metric_minor=annual_total,
                metric_bps=share_bps,
                evidence_txn_ids=evidence,
            )

        if share_bps is not None and share_bps >= context.config.critical_subscription_share_bps:
            return RuleFinding(
                rule_id=self.rule_id,
                dimension="subscriptions",
                status="critical",
                severity="high",
                score_delta=-12,
                title="固定周期支出负担较重",
                summary=(
                    f"活跃周期性支出折合月均 {format_minor_units(monthly_equiv)} "
                    f"{context.snapshot.base_currency}（占比 {share_bps / 100:.1f}%），"
                    "压缩弹性空间。"
                ),
                recommended_action="逐项审阅年度固定账单与订阅，评估降级或合并家庭共享套餐。",
                metric_minor=annual_total,
                metric_bps=share_bps,
                evidence_txn_ids=evidence,
            )
        if share_bps is not None and share_bps >= context.config.watch_subscription_share_bps:
            return RuleFinding(
                rule_id=self.rule_id,
                dimension="subscriptions",
                status="watch",
                severity="info",
                score_delta=-4,
                title=f"已识别 {len(active_subs)} 项周期性支出",
                summary=(
                    f"周期性支出折合年化 {format_minor_units(annual_total)} "
                    f"{context.snapshot.base_currency}，占常规现金流 {share_bps / 100:.1f}%。"
                ),
                recommended_action="确认周期性账单清单，开启扣费前现金流提醒。",
                metric_minor=annual_total,
                metric_bps=share_bps,
                evidence_txn_ids=evidence,
            )
        return None


class BudgetAdherenceRule:
    rule_id = "budget.category_adherence"

    def evaluate(self, context: CFOAnalysisContext) -> RuleFinding | None:
        if not context.budget_evaluations:
            return None
        exceeded = [b for b in context.budget_evaluations if b.status == "exceeded"]
        over_pace = [b for b in context.budget_evaluations if b.status == "over_pace"]

        if exceeded:
            overrun_sum = sum(
                max(0, b.spent_to_date_minor - b.monthly_limit_minor) for b in exceeded
            )
            names = "、".join(b.category_name for b in exceeded[:3])
            evidence = tuple(dict.fromkeys(tid for b in exceeded for tid in b.evidence_txn_ids[:5]))
            return RuleFinding(
                rule_id=self.rule_id,
                dimension="budget",
                status="critical",
                severity="high",
                score_delta=-15,
                title=f"{len(exceeded)} 个分类已超出月度预算（{names}）",
                summary=(
                    f"当前已累计超支 {format_minor_units(overrun_sum)} "
                    f"{context.snapshot.base_currency}。"
                ),
                recommended_action="审阅超支分类大额明细，或在家庭预算中从低优先级分类调剂额度。",
                metric_minor=overrun_sum,
                evidence_txn_ids=evidence,
            )
        if over_pace:
            proj_overrun = sum(b.projected_overrun_minor for b in over_pace)
            names = "、".join(b.category_name for b in over_pace[:3])
            evidence = tuple(
                dict.fromkeys(tid for b in over_pace for tid in b.evidence_txn_ids[:5])
            )
            return RuleFinding(
                rule_id=self.rule_id,
                dimension="budget",
                status="watch",
                severity="warning",
                score_delta=-7,
                title=f"按当前节奏预计月底超支：{names}",
                summary=(
                    f"结合剩余天数与待付周期账单，预计月底超支 "
                    f"{format_minor_units(proj_overrun)} {context.snapshot.base_currency}。"
                ),
                recommended_action="控制本月剩余天数的非刚需消费频次。",
                metric_minor=proj_overrun,
                evidence_txn_ids=evidence,
            )
        return None


class AnomalyReviewRule:
    rule_id = "hygiene.anomaly_review"

    def evaluate(self, context: CFOAnalysisContext) -> RuleFinding | None:
        dups = [a for a in context.anomalies if a.kind == "duplicate_charge"]
        high_outliers = [
            a for a in context.anomalies if a.kind != "duplicate_charge" and a.severity == "high"
        ]
        if not dups and not high_outliers:
            return None

        flagged = dups + high_outliers
        total_minor = sum(a.amount_minor for a in flagged)
        evidence = tuple(dict.fromkeys(tid for a in flagged for tid in a.evidence_txn_ids))
        if dups:
            return RuleFinding(
                rule_id=self.rule_id,
                dimension="hygiene",
                status="watch",
                severity="warning",
                score_delta=-8,
                title=f"发现 {len(dups)} 组疑似重复扣费线索",
                summary="短时间内存在同商户同金额未退款交易，可能来自重复导入或商户重复扣款。",
                recommended_action="逐笔核对原始凭证，若属误导可移入回收站，若属多扣可发起退款登记。",
                metric_minor=total_minor,
                evidence_txn_ids=evidence,
            )
        return RuleFinding(
            rule_id=self.rule_id,
            dimension="hygiene",
            status="watch",
            severity="warning",
            score_delta=-5,
            title=f"存在 {len(high_outliers)} 项显著偏离常态的支出记录",
            summary="部分单笔或分类支出远超历史基线，建议确认分类及家庭分摊状态。",
            recommended_action="检查大额账单是否需要关联共同分摊或拆分子分类。",
            metric_minor=total_minor,
            evidence_txn_ids=evidence,
        )


class LedgerHygieneRule:
    rule_id = "hygiene.categorization_and_fx"

    def evaluate(self, context: CFOAnalysisContext) -> RuleFinding | None:
        uncat_bps = context.metrics.uncategorized_share_bps or 0
        uncat_minor = context.metrics.uncategorized_expense_minor
        unconv = context.metrics.liquidity.unconverted_balances_by_currency

        if uncat_bps >= context.config.uncategorized_watch_bps and uncat_minor > 0:
            uncat_txns = next(
                (
                    c.evidence_txn_ids
                    for c in context.metrics.focus_categories
                    if c.category_id is None
                ),
                (),
            )
            return RuleFinding(
                rule_id=self.rule_id,
                dimension="hygiene",
                status="watch",
                severity="info",
                score_delta=-5,
                title="部分支出尚未补充分类",
                summary=(
                    f"当月待分类净支出 {format_minor_units(uncat_minor)} "
                    f"{context.snapshot.base_currency}（占比 {uncat_bps / 100:.1f}%）。"
                ),
                recommended_action="补齐待分类账单，提升预算追踪与月度同比解释准确度。",
                metric_minor=uncat_minor,
                metric_bps=uncat_bps,
                evidence_txn_ids=uncat_txns[:10],
            )

        if unconv:
            fx_list = "、".join(f"{cur} {format_minor_units(val)}" for cur, val in unconv)
            return RuleFinding(
                rule_id=self.rule_id,
                dimension="hygiene",
                status="healthy",
                severity="info",
                score_delta=0,
                title="外币账户余额已独立列示",
                summary=(
                    f"检测到外币原生余额（{fx_list}）；为避免无实时汇率臆测，未直接混入 "
                    f"{context.snapshot.base_currency} 现金池。"
                ),
            )
        return None


DEFAULT_HEALTH_RULES: tuple[FinancialHealthRule, ...] = (
    EmergencyFundRunwayRule(),
    SavingsRateRule(),
    DebtToCashRule(),
    SubscriptionBloatRule(),
    BudgetAdherenceRule(),
    AnomalyReviewRule(),
    LedgerHygieneRule(),
)


def _grade_for_score(score: int) -> HealthGrade:
    if score >= 85:
        return "A"
    if score >= 70:
        return "B"
    if score >= 55:
        return "C"
    return "D"


def evaluate_financial_health(
    context: CFOAnalysisContext,
    *,
    rules: tuple[FinancialHealthRule, ...] = DEFAULT_HEALTH_RULES,
) -> FinancialHealthScorecard:
    findings: list[RuleFinding] = []
    raw_score = 100
    for rule in rules:
        finding = rule.evaluate(context)
        if finding is not None:
            findings.append(finding)
            raw_score += finding.score_delta

    # Also penalize if baseline 90-day cashflow radar breaches safety floor
    if context.forecast.baseline.breaches_safety_floor:
        raw_score -= 10

    clamped_score = max(0, min(100, raw_score))
    status_order = {"critical": 0, "watch": 1, "healthy": 2}
    findings.sort(key=lambda f: (status_order[f.status], f.score_delta, f.rule_id))

    steps: list[str] = []
    for f in findings:
        if f.recommended_action and f.recommended_action not in steps:
            steps.append(f.recommended_action)
    if context.forecast.baseline.breaches_safety_floor:
        floor_msg = (
            f"未来 {context.forecast.horizon_days} 天基准现金流预计在第 "
            f"{context.forecast.baseline.tightest_week_index} 周触及低点，请提前调配流动性。"
        )
        if floor_msg not in steps:
            steps.insert(0, floor_msg)

    return FinancialHealthScorecard(
        score=clamped_score,
        grade=_grade_for_score(clamped_score),
        findings=tuple(findings),
        actionable_steps=tuple(steps[:5]),
    )


class CFOAnalyticalEngine:
    """Pure, stateless orchestrator for HomeDatabase AI CFO analytics & health rules."""

    def __init__(
        self,
        *,
        rules_config: CFORulesConfig | None = None,
        detector_config: DetectorConfig | None = None,
        rules: tuple[FinancialHealthRule, ...] = DEFAULT_HEALTH_RULES,
    ) -> None:
        self.rules_config = rules_config or CFORulesConfig()
        self.detector_config = detector_config or DetectorConfig()
        self.rules = rules

    def analyze(
        self,
        snapshot: LedgerSnapshot,
        *,
        focus_month: YearMonth | None = None,
        safety_floor_minor: int | None = None,
        what_if_options: tuple[WhatIfPlanOption, ...] = (),
        what_if_horizon_days: int = 180,
    ) -> CFOReport:
        target_month = focus_month or YearMonth.from_date(snapshot.as_of)
        metrics = compute_cashflow_metrics(snapshot, focus_month=target_month)
        spend_change = explain_spend_change(
            snapshot,
            baseline_months=(target_month.add_months(-1),),
            current_months=(target_month,),
        )
        recurring = detect_recurring_charges(snapshot, config=self.detector_config)
        anomalies = detect_spending_anomalies(
            snapshot,
            focus_month=target_month,
            recurring_charges=recurring,
            config=self.detector_config,
        )
        budgets = evaluate_category_budgets(
            snapshot,
            focus_month=target_month,
            recurring_charges=recurring,
        )

        effective_floor = (
            safety_floor_minor
            if safety_floor_minor is not None
            else scale_minor_by_bps(
                metrics.rolling_monthly_burn_minor,
                self.rules_config.forecast_emergency_months_floor_bps,
            )
        )
        forecast = forecast_cashflow(
            snapshot,
            horizon_days=self.rules_config.forecast_horizon_days,
            safety_floor_minor=effective_floor,
            recurring_charges=recurring,
        )

        context = CFOAnalysisContext(
            snapshot=snapshot,
            focus_month=target_month,
            metrics=metrics,
            spend_change=spend_change,
            recurring_charges=recurring,
            anomalies=anomalies,
            budget_evaluations=budgets,
            forecast=forecast,
            config=self.rules_config,
        )
        scorecard = evaluate_financial_health(context, rules=self.rules)

        what_if = (
            compare_what_if_plans(
                snapshot,
                what_if_options,
                horizon_days=what_if_horizon_days,
                safety_floor_minor=effective_floor,
                recurring_charges=recurring,
            )
            if what_if_options
            else ()
        )

        return CFOReport(
            as_of_iso=snapshot.as_of.isoformat(),
            focus_month=target_month,
            base_currency=snapshot.base_currency,
            scope_label=snapshot.scope_label,
            viewer_member_id=snapshot.viewer_member_id,
            metrics=metrics,
            spend_change=spend_change,
            recurring_charges=recurring,
            anomalies=anomalies,
            budget_evaluations=budgets,
            forecast=forecast,
            what_if_outcomes=what_if,
            scorecard=scorecard,
        )
