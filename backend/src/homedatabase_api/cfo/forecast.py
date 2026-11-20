"""Category budget pace engine, 3-scenario Cashflow Radar, and What-If Life Simulator.

Implements AI_ROADMAP.md §03 (Cashflow Radar: confirmed vs recurring vs historical
variable burn across baseline/conservative/stress scenarios) and §04 (Life Simulator:
multi-option plan comparison under minimum cash-floor / emergency-fund constraints).
"""

from __future__ import annotations

import calendar
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from .detectors import RecurringCharge, detect_recurring_charges
from .domain import (
    BPS_SCALE,
    CFODomainError,
    LedgerSnapshot,
    ScheduledCashflowItem,
    YearMonth,
    ratio_to_bps,
    require_int_minor,
    require_non_negative_minor,
    require_positive_minor,
    safe_divide_minor,
    scale_minor_by_bps,
)

BudgetStatus = Literal["on_track", "watch", "over_pace", "exceeded"]
ScenarioName = Literal["baseline", "conservative", "stress"]


@dataclass(frozen=True, slots=True)
class CategoryBudgetEvaluation:
    category_id: str
    category_name: str
    month: YearMonth
    monthly_limit_minor: int
    spent_to_date_minor: int
    remaining_minor: int
    utilization_bps: int
    elapsed_days: int
    total_days: int
    paid_recurring_minor: int
    upcoming_recurring_minor: int
    projected_end_of_month_minor: int
    projected_overrun_minor: int
    status: BudgetStatus
    evidence_txn_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WeeklyCashflowBucket:
    week_index: int
    starts_on: date
    ends_on: date
    confirmed_inflow_minor: int
    confirmed_outflow_minor: int
    recurring_inflow_minor: int
    recurring_outflow_minor: int
    variable_inflow_minor: int
    variable_outflow_minor: int
    net_flow_minor: int
    closing_cash_minor: int


@dataclass(frozen=True, slots=True)
class ScenarioProjection:
    scenario: ScenarioName
    variable_expense_multiplier_bps: int
    variable_income_multiplier_bps: int
    opening_cash_minor: int
    closing_cash_minor: int
    min_cash_minor: int
    min_cash_date: date
    tightest_week_index: int
    shortfall_below_floor_minor: int
    breaches_safety_floor: bool
    weekly_buckets: tuple[WeeklyCashflowBucket, ...]


@dataclass(frozen=True, slots=True)
class CashflowForecastResult:
    as_of: date
    horizon_days: int
    opening_liquid_cash_minor: int
    safety_floor_minor: int
    daily_variable_expense_minor: int
    daily_variable_income_minor: int
    confirmed_net_minor: int
    recurring_net_minor: int
    baseline: ScenarioProjection
    conservative: ScenarioProjection
    stress: ScenarioProjection
    top_sensitivity_drivers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WhatIfAdjustment:
    """User hypothesis for AI_ROADMAP.md §04 Life Simulator."""

    label: str
    direction: Literal["expense", "income"]
    amount_minor: int
    effective_on: date
    cadence: Literal["once", "monthly"] = "once"

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise CFODomainError("WhatIfAdjustment.label must be non-empty")
        if self.direction not in ("expense", "income"):
            raise CFODomainError(f"invalid adjustment direction: {self.direction!r}")
        if self.cadence not in ("once", "monthly"):
            raise CFODomainError(f"invalid adjustment cadence: {self.cadence!r}")
        require_positive_minor(self.amount_minor, field_name="adjustment.amount_minor")


@dataclass(frozen=True, slots=True)
class WhatIfPlanOption:
    option_id: str
    title: str
    adjustments: tuple[WhatIfAdjustment, ...] = ()
    defer_scheduled_ids: tuple[tuple[str, int], ...] = ()
    variable_spend_delta_bps: int = 0

    def __post_init__(self) -> None:
        if not self.option_id or not self.title.strip():
            raise CFODomainError("WhatIfPlanOption option_id and title must be non-empty")
        if self.variable_spend_delta_bps < -BPS_SCALE or self.variable_spend_delta_bps > 20_000:
            raise CFODomainError("variable_spend_delta_bps out of realistic range (-100%..+200%)")


@dataclass(frozen=True, slots=True)
class WhatIfOptionOutcome:
    option_id: str
    title: str
    opening_cash_minor: int
    ending_cash_minor: int
    delta_vs_baseline_ending_minor: int
    min_cash_minor: int
    min_cash_date: date
    safety_floor_minor: int
    max_shortfall_minor: int
    feasible_under_floor: bool
    required_monthly_reserve_minor: int


def _future_recurring_occurrences(
    charge: RecurringCharge,
    *,
    after_date: date,
    through_date: date,
) -> list[date]:
    if charge.status != "active" or after_date >= through_date:
        return []

    dates: list[date] = []
    cursor = charge.next_expected_on
    step_guard = 0
    while cursor <= through_date and step_guard < 400:
        step_guard += 1
        if cursor > after_date:
            dates.append(cursor)
        if charge.cadence == "weekly":
            cursor = cursor + timedelta(days=7)
        elif charge.cadence == "biweekly":
            cursor = cursor + timedelta(days=14)
        elif charge.cadence == "monthly":
            ym = YearMonth.from_date(cursor).add_months(1)
            day = min(
                charge.anchor_day_of_month or cursor.day, calendar.monthrange(ym.year, ym.month)[1]
            )
            cursor = date(ym.year, ym.month, day)
        elif charge.cadence == "quarterly":
            ym = YearMonth.from_date(cursor).add_months(3)
            day = min(
                charge.anchor_day_of_month or cursor.day, calendar.monthrange(ym.year, ym.month)[1]
            )
            cursor = date(ym.year, ym.month, day)
        else:
            ym = YearMonth.from_date(cursor).add_months(12)
            day = min(
                charge.anchor_day_of_month or cursor.day, calendar.monthrange(ym.year, ym.month)[1]
            )
            cursor = date(ym.year, ym.month, day)
    return dates


def evaluate_category_budgets(
    snapshot: LedgerSnapshot,
    *,
    focus_month: YearMonth | None = None,
    recurring_charges: tuple[RecurringCharge, ...] | None = None,
) -> tuple[CategoryBudgetEvaluation, ...]:
    """Evaluate monthly category budgets with recurring-aware end-of-month pace projection.

    Separates fixed recurring bills from variable daily spend so paying monthly rent on
    day 1 does not falsely extrapolate 30x rent by month-end.
    """
    if not snapshot.budgets:
        return ()

    target_month = focus_month or YearMonth.from_date(snapshot.as_of)
    total_days = target_month.days_in_month()
    if snapshot.as_of < target_month.starts_on():
        elapsed_days = 0
    elif snapshot.as_of > target_month.ends_on():
        elapsed_days = total_days
    else:
        elapsed_days = snapshot.as_of.day

    recurring = (
        recurring_charges if recurring_charges is not None else detect_recurring_charges(snapshot)
    )
    recurring_txn_ids = {
        txn_id
        for item in recurring
        if item.direction == "expense" and item.status == "active"
        for txn_id in item.evidence_txn_ids
    }

    # Pre-index net spend per category in target_month up to snapshot.as_of
    cat_txns: dict[str, list[str]] = defaultdict(list)
    cat_net_minor: dict[str, int] = defaultdict(int)
    cat_paid_recurring_minor: dict[str, int] = defaultdict(int)

    # Net linked refunds first so recurring fixed bills that were refunded don't distort pace
    refunded_by_orig: dict[str, int] = defaultdict(int)
    for txn in snapshot.transactions:
        if (
            txn.type == "refund"
            and txn.refund_of_id
            and txn.local_date <= snapshot.as_of
            and txn.year_month == target_month
        ):
            refunded_by_orig[txn.refund_of_id] += txn.base_amount_minor

    for txn in sorted(snapshot.transactions, key=lambda t: (t.occurred_at, t.id)):
        if (
            txn.category_id is None
            or txn.local_date > snapshot.as_of
            or txn.year_month != target_month
        ):
            continue
        if txn.type == "expense":
            cat_txns[txn.category_id].append(txn.id)
            cat_net_minor[txn.category_id] += txn.base_amount_minor
            if txn.id in recurring_txn_ids:
                eff = max(0, txn.base_amount_minor - refunded_by_orig.get(txn.id, 0))
                cat_paid_recurring_minor[txn.category_id] += eff
        elif txn.type == "refund":
            cat_txns[txn.category_id].append(txn.id)
            cat_net_minor[txn.category_id] -= txn.base_amount_minor

    # Upcoming recurring + scheduled expenses in the remainder of target_month
    cutoff_after = max(snapshot.as_of, target_month.starts_on() - timedelta(days=1))
    upcoming_fixed_by_cat: dict[str, int] = defaultdict(int)
    if elapsed_days < total_days:
        for charge in recurring:
            if charge.direction == "expense" and charge.category_id is not None:
                occ = _future_recurring_occurrences(
                    charge,
                    after_date=cutoff_after,
                    through_date=target_month.ends_on(),
                )
                amt = (
                    charge.latest_amount_minor
                    if charge.price_hike_minor > 0
                    else charge.typical_amount_minor
                )
                upcoming_fixed_by_cat[charge.category_id] += amt * len(occ)

        for item in snapshot.scheduled_items:
            if (
                item.direction == "expense"
                and item.category_id is not None
                and cutoff_after < item.due_date <= target_month.ends_on()
            ):
                upcoming_fixed_by_cat[item.category_id] += item.amount_minor

    evaluations: list[CategoryBudgetEvaluation] = []
    remaining_days = max(0, total_days - elapsed_days)

    for target in snapshot.budgets:
        spent = max(0, cat_net_minor.get(target.category_id, 0))
        paid_rec = min(spent, cat_paid_recurring_minor.get(target.category_id, 0))
        variable_spent = max(0, spent - paid_rec)
        upcoming_rec = upcoming_fixed_by_cat.get(target.category_id, 0)

        if elapsed_days == 0:
            projected_variable = 0
        elif remaining_days == 0:
            projected_variable = variable_spent
        else:
            daily_var = safe_divide_minor(variable_spent, elapsed_days)
            projected_variable = variable_spent + daily_var * remaining_days

        projected_eom = paid_rec + projected_variable + upcoming_rec
        remaining = target.monthly_limit_minor - spent
        util_bps = ratio_to_bps(spent, target.monthly_limit_minor) or 0
        overrun = max(0, projected_eom - target.monthly_limit_minor)

        if spent > target.monthly_limit_minor:
            status: BudgetStatus = "exceeded"
        elif projected_eom > target.monthly_limit_minor:
            status = "over_pace"
        elif util_bps >= target.warning_threshold_bps:
            status = "watch"
        else:
            status = "on_track"

        evaluations.append(
            CategoryBudgetEvaluation(
                category_id=target.category_id,
                category_name=snapshot.category_name(target.category_id),
                month=target_month,
                monthly_limit_minor=target.monthly_limit_minor,
                spent_to_date_minor=spent,
                remaining_minor=remaining,
                utilization_bps=util_bps,
                elapsed_days=elapsed_days,
                total_days=total_days,
                paid_recurring_minor=paid_rec,
                upcoming_recurring_minor=upcoming_rec,
                projected_end_of_month_minor=projected_eom,
                projected_overrun_minor=overrun,
                status=status,
                evidence_txn_ids=tuple(cat_txns.get(target.category_id, ())),
            )
        )

    status_rank = {"exceeded": 0, "over_pace": 1, "watch": 2, "on_track": 3}
    evaluations.sort(key=lambda ev: (status_rank[ev.status], -ev.utilization_bps, ev.category_name))
    return tuple(evaluations)


def _opening_liquid_cash(snapshot: LedgerSnapshot) -> int:
    return sum(
        acct.balance_minor
        for acct in snapshot.accounts
        if acct.currency == snapshot.base_currency
        and acct.kind == "asset"
        and acct.included_in_liquid_cash
    )


def _historical_daily_variable_rates(
    snapshot: LedgerSnapshot,
    recurring: tuple[RecurringCharge, ...],
    *,
    lookback_days: int = 90,
) -> tuple[int, int]:
    """Estimate daily non-recurring variable expense and variable income in minor units."""
    if lookback_days <= 0:
        raise CFODomainError("lookback_days must be > 0")

    recurring_ids = {
        txn_id for item in recurring if item.status == "active" for txn_id in item.evidence_txn_ids
    }
    window_start = snapshot.as_of - timedelta(days=lookback_days - 1)
    hist_txns = [
        txn for txn in snapshot.transactions if window_start <= txn.local_date <= snapshot.as_of
    ]
    if not hist_txns:
        return 0, 0

    earliest_seen = min(txn.local_date for txn in hist_txns)
    effective_span = max(14, (snapshot.as_of - earliest_seen).days + 1)
    span_days = min(lookback_days, effective_span)

    var_expense = 0
    var_income = 0
    for txn in hist_txns:
        if txn.id in recurring_ids:
            continue
        if txn.type == "expense":
            var_expense += txn.base_amount_minor
        elif txn.type == "refund":
            var_expense -= txn.base_amount_minor
        elif txn.type == "income":
            var_income += txn.base_amount_minor

    daily_exp = safe_divide_minor(max(0, var_expense), span_days)
    daily_inc = safe_divide_minor(max(0, var_income), span_days)
    return daily_exp, daily_inc


def _simulate_horizon(
    *,
    scenario: ScenarioName,
    as_of: date,
    horizon_days: int,
    opening_cash_minor: int,
    safety_floor_minor: int,
    confirmed_by_day: dict[date, tuple[int, int]],
    recurring_by_day: dict[date, tuple[int, int]],
    daily_variable_expense_minor: int,
    daily_variable_income_minor: int,
    variable_expense_multiplier_bps: int,
    variable_income_multiplier_bps: int,
) -> ScenarioProjection:
    adj_var_exp = scale_minor_by_bps(daily_variable_expense_minor, variable_expense_multiplier_bps)
    adj_var_inc = scale_minor_by_bps(daily_variable_income_minor, variable_income_multiplier_bps)

    running_cash = opening_cash_minor
    min_cash = opening_cash_minor
    min_cash_date = as_of

    weekly_buckets: list[WeeklyCashflowBucket] = []
    total_weeks = (horizon_days + 6) // 7

    for week_idx in range(total_weeks):
        start_offset = week_idx * 7 + 1
        end_offset = min(horizon_days, (week_idx + 1) * 7)
        w_start = as_of + timedelta(days=start_offset)
        w_end = as_of + timedelta(days=end_offset)

        conf_in = conf_out = rec_in = rec_out = var_in = var_out = 0
        for offset in range(start_offset, end_offset + 1):
            day = as_of + timedelta(days=offset)
            c_in, c_out = confirmed_by_day.get(day, (0, 0))
            r_in, r_out = recurring_by_day.get(day, (0, 0))

            conf_in += c_in
            conf_out += c_out
            rec_in += r_in
            rec_out += r_out
            var_in += adj_var_inc
            var_out += adj_var_exp

            day_net = (c_in + r_in + adj_var_inc) - (c_out + r_out + adj_var_exp)
            running_cash = require_int_minor(running_cash + day_net, field_name="running_cash")
            if running_cash < min_cash:
                min_cash = running_cash
                min_cash_date = day

        w_net = (conf_in + rec_in + var_in) - (conf_out + rec_out + var_out)
        weekly_buckets.append(
            WeeklyCashflowBucket(
                week_index=week_idx + 1,
                starts_on=w_start,
                ends_on=w_end,
                confirmed_inflow_minor=conf_in,
                confirmed_outflow_minor=conf_out,
                recurring_inflow_minor=rec_in,
                recurring_outflow_minor=rec_out,
                variable_inflow_minor=var_in,
                variable_outflow_minor=var_out,
                net_flow_minor=w_net,
                closing_cash_minor=running_cash,
            )
        )

    tightest_week = (
        min(weekly_buckets, key=lambda b: (b.closing_cash_minor, b.week_index)).week_index
        if weekly_buckets
        else 1
    )
    shortfall = max(0, safety_floor_minor - min_cash)

    return ScenarioProjection(
        scenario=scenario,
        variable_expense_multiplier_bps=variable_expense_multiplier_bps,
        variable_income_multiplier_bps=variable_income_multiplier_bps,
        opening_cash_minor=opening_cash_minor,
        closing_cash_minor=running_cash,
        min_cash_minor=min_cash,
        min_cash_date=min_cash_date,
        tightest_week_index=tightest_week,
        shortfall_below_floor_minor=shortfall,
        breaches_safety_floor=min_cash < safety_floor_minor,
        weekly_buckets=tuple(weekly_buckets),
    )


def forecast_cashflow(
    snapshot: LedgerSnapshot,
    *,
    horizon_days: int = 90,
    safety_floor_minor: int = 0,
    lookback_days: int = 90,
    recurring_charges: tuple[RecurringCharge, ...] | None = None,
) -> CashflowForecastResult:
    """Run 3-scenario (baseline / conservative / stress) cashflow radar projection."""
    if not 7 <= horizon_days <= 366:
        raise CFODomainError("horizon_days must be between 7 and 366")
    require_non_negative_minor(safety_floor_minor, field_name="safety_floor_minor")

    recurring = (
        recurring_charges if recurring_charges is not None else detect_recurring_charges(snapshot)
    )
    opening_cash = _opening_liquid_cash(snapshot)
    through_date = snapshot.as_of + timedelta(days=horizon_days)

    confirmed_by_day: dict[date, list[int]] = defaultdict(lambda: [0, 0])
    confirmed_out_items: list[ScheduledCashflowItem] = []
    for item in snapshot.scheduled_items:
        if snapshot.as_of < item.due_date <= through_date:
            if item.direction == "income":
                confirmed_by_day[item.due_date][0] += item.amount_minor
            else:
                confirmed_by_day[item.due_date][1] += item.amount_minor
                confirmed_out_items.append(item)

    recurring_by_day: dict[date, list[int]] = defaultdict(lambda: [0, 0])
    recurring_totals: list[tuple[int, str]] = []
    for charge in recurring:
        occ = _future_recurring_occurrences(
            charge,
            after_date=snapshot.as_of,
            through_date=through_date,
        )
        if not occ:
            continue
        amt = (
            charge.latest_amount_minor
            if charge.price_hike_minor > 0
            else charge.typical_amount_minor
        )
        for day in occ:
            if charge.direction == "income":
                recurring_by_day[day][0] += amt
            else:
                recurring_by_day[day][1] += amt
        if charge.direction == "expense":
            recurring_totals.append((amt * len(occ), f"周期支出：{charge.display_payee}"))

    daily_var_exp, daily_var_inc = _historical_daily_variable_rates(
        snapshot, recurring, lookback_days=lookback_days
    )

    conf_map = {k: (v[0], v[1]) for k, v in confirmed_by_day.items()}
    rec_map = {k: (v[0], v[1]) for k, v in recurring_by_day.items()}

    baseline = _simulate_horizon(
        scenario="baseline",
        as_of=snapshot.as_of,
        horizon_days=horizon_days,
        opening_cash_minor=opening_cash,
        safety_floor_minor=safety_floor_minor,
        confirmed_by_day=conf_map,
        recurring_by_day=rec_map,
        daily_variable_expense_minor=daily_var_exp,
        daily_variable_income_minor=daily_var_inc,
        variable_expense_multiplier_bps=10_000,
        variable_income_multiplier_bps=10_000,
    )
    conservative = _simulate_horizon(
        scenario="conservative",
        as_of=snapshot.as_of,
        horizon_days=horizon_days,
        opening_cash_minor=opening_cash,
        safety_floor_minor=safety_floor_minor,
        confirmed_by_day=conf_map,
        recurring_by_day=rec_map,
        daily_variable_expense_minor=daily_var_exp,
        daily_variable_income_minor=daily_var_inc,
        variable_expense_multiplier_bps=11_500,
        variable_income_multiplier_bps=9_500,
    )
    stress = _simulate_horizon(
        scenario="stress",
        as_of=snapshot.as_of,
        horizon_days=horizon_days,
        opening_cash_minor=opening_cash,
        safety_floor_minor=safety_floor_minor,
        confirmed_by_day=conf_map,
        recurring_by_day=rec_map,
        daily_variable_expense_minor=daily_var_exp,
        daily_variable_income_minor=daily_var_inc,
        variable_expense_multiplier_bps=13_000,
        variable_income_multiplier_bps=0,
    )

    # Rank largest sensitivity drivers over the forecast horizon
    driver_candidates: list[tuple[int, str]] = []
    if daily_var_exp > 0:
        driver_candidates.append((daily_var_exp * horizon_days, "日常弹性消费基线"))
    for item in confirmed_out_items:
        driver_candidates.append((item.amount_minor, f"已确认安排：{item.label}"))
    driver_candidates.extend(recurring_totals)
    driver_candidates.sort(key=lambda pair: (-pair[0], pair[1]))

    confirmed_net = sum(v[0] - v[1] for v in conf_map.values())
    recurring_net = sum(v[0] - v[1] for v in rec_map.values())

    return CashflowForecastResult(
        as_of=snapshot.as_of,
        horizon_days=horizon_days,
        opening_liquid_cash_minor=opening_cash,
        safety_floor_minor=safety_floor_minor,
        daily_variable_expense_minor=daily_var_exp,
        daily_variable_income_minor=daily_var_inc,
        confirmed_net_minor=confirmed_net,
        recurring_net_minor=recurring_net,
        baseline=baseline,
        conservative=conservative,
        stress=stress,
        top_sensitivity_drivers=tuple(label for _, label in driver_candidates[:3]),
    )


def compare_what_if_plans(
    snapshot: LedgerSnapshot,
    options: tuple[WhatIfPlanOption, ...],
    *,
    horizon_days: int = 180,
    safety_floor_minor: int = 0,
    recurring_charges: tuple[RecurringCharge, ...] | None = None,
) -> tuple[WhatIfOptionOutcome, ...]:
    """Compare life-plan options (travel, renovation, deferred spend) on identical math."""
    if not options:
        return ()
    if not 7 <= horizon_days <= 730:
        raise CFODomainError("horizon_days must be between 7 and 730")
    require_non_negative_minor(safety_floor_minor, field_name="safety_floor_minor")

    recurring = (
        recurring_charges if recurring_charges is not None else detect_recurring_charges(snapshot)
    )
    opening_cash = _opening_liquid_cash(snapshot)
    through_date = snapshot.as_of + timedelta(days=horizon_days)
    daily_var_exp, daily_var_inc = _historical_daily_variable_rates(snapshot, recurring)

    rec_by_day_mut: dict[date, list[int]] = defaultdict(lambda: [0, 0])
    for charge in recurring:
        occ = _future_recurring_occurrences(
            charge, after_date=snapshot.as_of, through_date=through_date
        )
        amt = (
            charge.latest_amount_minor
            if charge.price_hike_minor > 0
            else charge.typical_amount_minor
        )
        for d in occ:
            if charge.direction == "income":
                rec_by_day_mut[d][0] += amt
            else:
                rec_by_day_mut[d][1] += amt
    rec_map = {k: (v[0], v[1]) for k, v in rec_by_day_mut.items()}

    base_conf_mut: dict[date, list[int]] = defaultdict(lambda: [0, 0])
    for item in snapshot.scheduled_items:
        if snapshot.as_of < item.due_date <= through_date:
            if item.direction == "income":
                base_conf_mut[item.due_date][0] += item.amount_minor
            else:
                base_conf_mut[item.due_date][1] += item.amount_minor

    baseline_proj = _simulate_horizon(
        scenario="baseline",
        as_of=snapshot.as_of,
        horizon_days=horizon_days,
        opening_cash_minor=opening_cash,
        safety_floor_minor=safety_floor_minor,
        confirmed_by_day={k: (v[0], v[1]) for k, v in base_conf_mut.items()},
        recurring_by_day=rec_map,
        daily_variable_expense_minor=daily_var_exp,
        daily_variable_income_minor=daily_var_inc,
        variable_expense_multiplier_bps=BPS_SCALE,
        variable_income_multiplier_bps=BPS_SCALE,
    )

    horizon_months = max(1, (horizon_days + 29) // 30)
    outcomes: list[WhatIfOptionOutcome] = []

    for option in options:
        defer_map = dict(option.defer_scheduled_ids)
        opt_conf_mut: dict[date, list[int]] = defaultdict(lambda: [0, 0])

        for item in snapshot.scheduled_items:
            shift_days = defer_map.get(item.id, 0)
            eff_date = item.due_date + timedelta(days=shift_days)
            if snapshot.as_of < eff_date <= through_date:
                if item.direction == "income":
                    opt_conf_mut[eff_date][0] += item.amount_minor
                else:
                    opt_conf_mut[eff_date][1] += item.amount_minor

        for adj in option.adjustments:
            if adj.cadence == "once":
                if snapshot.as_of < adj.effective_on <= through_date:
                    idx = 0 if adj.direction == "income" else 1
                    opt_conf_mut[adj.effective_on][idx] += adj.amount_minor
            else:
                cursor = adj.effective_on
                pref_day = adj.effective_on.day
                step = 0
                while cursor <= through_date and step < 36:
                    step += 1
                    if cursor > snapshot.as_of:
                        idx = 0 if adj.direction == "income" else 1
                        opt_conf_mut[cursor][idx] += adj.amount_minor
                    ym = YearMonth.from_date(cursor).add_months(1)
                    day = min(pref_day, calendar.monthrange(ym.year, ym.month)[1])
                    cursor = date(ym.year, ym.month, day)

        mult_bps = max(0, BPS_SCALE + option.variable_spend_delta_bps)
        proj = _simulate_horizon(
            scenario="baseline",
            as_of=snapshot.as_of,
            horizon_days=horizon_days,
            opening_cash_minor=opening_cash,
            safety_floor_minor=safety_floor_minor,
            confirmed_by_day={k: (v[0], v[1]) for k, v in opt_conf_mut.items()},
            recurring_by_day=rec_map,
            daily_variable_expense_minor=daily_var_exp,
            daily_variable_income_minor=daily_var_inc,
            variable_expense_multiplier_bps=mult_bps,
            variable_income_multiplier_bps=BPS_SCALE,
        )

        req_monthly = (
            safe_divide_minor(proj.shortfall_below_floor_minor, horizon_months)
            if proj.shortfall_below_floor_minor > 0
            else 0
        )

        outcomes.append(
            WhatIfOptionOutcome(
                option_id=option.option_id,
                title=option.title,
                opening_cash_minor=opening_cash,
                ending_cash_minor=proj.closing_cash_minor,
                delta_vs_baseline_ending_minor=proj.closing_cash_minor
                - baseline_proj.closing_cash_minor,
                min_cash_minor=proj.min_cash_minor,
                min_cash_date=proj.min_cash_date,
                safety_floor_minor=safety_floor_minor,
                max_shortfall_minor=proj.shortfall_below_floor_minor,
                feasible_under_floor=not proj.breaches_safety_floor,
                required_monthly_reserve_minor=req_monthly,
            )
        )

    return tuple(outcomes)
