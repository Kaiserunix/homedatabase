"""Deterministic cashflow metrics, balance-sheet liquidity, and spend-change attribution.

Implements AI_ROADMAP.md §01 (Financial Memory: gross vs refunded vs split net burden)
and §02 (Ask Your Money: frequency vs ticket-size spend change decomposition with
traceable transaction evidence).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from statistics import median
from typing import Literal

from .domain import (
    AccountSnapshot,
    CFODomainError,
    LedgerSnapshot,
    TransactionSnapshot,
    YearMonth,
    ratio_to_bps,
    require_int_minor,
    safe_divide_minor,
)

ChangeDriverKind = Literal[
    "frequency_driven",
    "ticket_size_driven",
    "mixed",
    "new_spend",
    "stopped_spend",
    "stable",
]


@dataclass(frozen=True, slots=True)
class LiquiditySummary:
    """Balance-sheet snapshot in household base currency, isolating unconverted FX."""

    base_currency: str
    liquid_assets_minor: int
    non_liquid_assets_minor: int
    total_assets_minor: int
    total_liabilities_minor: int
    net_worth_minor: int
    unconverted_balances_by_currency: tuple[tuple[str, int], ...]
    debt_to_liquid_cash_bps: int | None


@dataclass(frozen=True, slots=True)
class MonthlyCashflowPoint:
    month: YearMonth
    income_minor: int
    gross_expense_minor: int
    refund_minor: int
    split_reimbursed_minor: int
    net_expense_minor: int
    effective_member_expense_minor: int
    net_savings_minor: int
    savings_rate_bps: int | None
    transfer_volume_minor: int
    transaction_count: int
    evidence_txn_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CategorySpendBreakdown:
    category_id: str | None
    category_name: str
    gross_expense_minor: int
    refund_minor: int
    split_reimbursed_minor: int
    net_expense_minor: int
    effective_member_expense_minor: int
    share_of_net_spend_bps: int | None
    expense_count: int
    refund_count: int
    avg_ticket_minor: int
    evidence_txn_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CashflowMetricsSummary:
    as_of: date
    focus_month: YearMonth
    liquidity: LiquiditySummary
    monthly_series: tuple[MonthlyCashflowPoint, ...]
    focus_month_point: MonthlyCashflowPoint
    focus_categories: tuple[CategorySpendBreakdown, ...]
    rolling_monthly_burn_minor: int
    median_monthly_burn_minor: int
    rolling_monthly_income_minor: int
    emergency_runway_months_bps: int | None
    uncategorized_expense_minor: int
    uncategorized_share_bps: int | None


@dataclass(frozen=True, slots=True)
class SpendChangeDriver:
    """Attribution of spend delta for one category or merchant between two periods."""

    key: str
    label: str
    previous_net_minor: int
    current_net_minor: int
    delta_net_minor: int
    delta_bps: int | None
    previous_count: int
    current_count: int
    previous_avg_ticket_minor: int
    current_avg_ticket_minor: int
    volume_effect_minor: int
    price_effect_minor: int
    driver_kind: ChangeDriverKind
    evidence_txn_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SpendChangeAttribution:
    baseline_label: str
    current_label: str
    previous_total_net_minor: int
    current_total_net_minor: int
    total_delta_minor: int
    total_delta_bps: int | None
    top_increases: tuple[SpendChangeDriver, ...]
    top_decreases: tuple[SpendChangeDriver, ...]


def compute_liquidity_summary(
    accounts: tuple[AccountSnapshot, ...],
    *,
    base_currency: str = "CNY",
) -> LiquiditySummary:
    liquid_assets = 0
    non_liquid_assets = 0
    total_liabilities = 0
    unconverted: dict[str, int] = defaultdict(int)

    for account in accounts:
        if account.currency != base_currency:
            net_native = (
                account.balance_minor if account.kind == "asset" else -account.balance_minor
            )
            unconverted[account.currency] += net_native
            continue

        if account.kind == "asset":
            if account.included_in_liquid_cash:
                liquid_assets += account.balance_minor
            else:
                non_liquid_assets += account.balance_minor
        else:
            total_liabilities += account.balance_minor

    total_assets = require_int_minor(liquid_assets + non_liquid_assets, field_name="total_assets")
    require_int_minor(total_liabilities, field_name="total_liabilities")
    net_worth = require_int_minor(total_assets - total_liabilities, field_name="net_worth")
    positive_debt = max(0, total_liabilities)
    debt_bps = (
        ratio_to_bps(positive_debt, liquid_assets)
        if liquid_assets > 0
        else (10_000 if positive_debt > 0 else 0)
    )

    return LiquiditySummary(
        base_currency=base_currency,
        liquid_assets_minor=liquid_assets,
        non_liquid_assets_minor=non_liquid_assets,
        total_assets_minor=total_assets,
        total_liabilities_minor=total_liabilities,
        net_worth_minor=net_worth,
        unconverted_balances_by_currency=tuple(sorted(unconverted.items())),
        debt_to_liquid_cash_bps=debt_bps,
    )


def _ordered_transactions(
    transactions: tuple[TransactionSnapshot, ...],
) -> list[TransactionSnapshot]:
    return sorted(transactions, key=lambda item: (item.occurred_at, item.id))


def build_monthly_point(
    month: YearMonth,
    transactions: list[TransactionSnapshot],
) -> MonthlyCashflowPoint:
    income = 0
    gross_expense = 0
    refund = 0
    split_reimbursed = 0
    transfer_vol = 0
    evidence: list[str] = []

    for txn in transactions:
        evidence.append(txn.id)
        if txn.type == "income":
            income += txn.base_amount_minor
        elif txn.type == "expense":
            gross_expense += txn.base_amount_minor
            split_reimbursed += txn.split_reimbursed_minor
        elif txn.type == "refund":
            refund += txn.base_amount_minor
        elif txn.type == "transfer":
            transfer_vol += txn.base_amount_minor

    net_expense = require_int_minor(gross_expense - refund, field_name="net_expense_minor")
    effective_member_expense = require_int_minor(
        gross_expense - refund - split_reimbursed,
        field_name="effective_member_expense_minor",
    )
    net_savings = require_int_minor(income - net_expense, field_name="net_savings_minor")
    savings_bps = ratio_to_bps(net_savings, income) if income > 0 else None

    return MonthlyCashflowPoint(
        month=month,
        income_minor=income,
        gross_expense_minor=gross_expense,
        refund_minor=refund,
        split_reimbursed_minor=split_reimbursed,
        net_expense_minor=net_expense,
        effective_member_expense_minor=effective_member_expense,
        net_savings_minor=net_savings,
        savings_rate_bps=savings_bps,
        transfer_volume_minor=transfer_vol,
        transaction_count=len(transactions),
        evidence_txn_ids=tuple(evidence),
    )


def compute_category_breakdown(
    snapshot: LedgerSnapshot,
    transactions: list[TransactionSnapshot],
) -> tuple[CategorySpendBreakdown, ...]:
    grouped: dict[str | None, list[TransactionSnapshot]] = defaultdict(list)
    for txn in transactions:
        if txn.type in ("expense", "refund"):
            grouped[txn.category_id].append(txn)

    raw_rows: list[tuple[str | None, int, int, int, int, int, int, tuple[str, ...]]] = []
    positive_net_sum = 0
    for cat_id, items in grouped.items():
        gross = sum(t.base_amount_minor for t in items if t.type == "expense")
        ref = sum(t.base_amount_minor for t in items if t.type == "refund")
        reimb = sum(t.split_reimbursed_minor for t in items if t.type == "expense")
        net = gross - ref
        eff = net - reimb
        exp_count = sum(1 for t in items if t.type == "expense")
        ref_count = sum(1 for t in items if t.type == "refund")
        if net > 0:
            positive_net_sum += net
        raw_rows.append(
            (cat_id, gross, ref, reimb, net, eff, exp_count, tuple(t.id for t in items))
        )

    results: list[CategorySpendBreakdown] = []
    for cat_id, gross, ref, reimb, net, eff, exp_count, evidence_ids in raw_rows:
        ref_count = len(evidence_ids) - exp_count
        avg_ticket = safe_divide_minor(max(0, net), exp_count) if exp_count > 0 else 0
        share_bps = ratio_to_bps(max(0, net), positive_net_sum) if positive_net_sum > 0 else None
        results.append(
            CategorySpendBreakdown(
                category_id=cat_id,
                category_name=snapshot.category_name(cat_id),
                gross_expense_minor=gross,
                refund_minor=ref,
                split_reimbursed_minor=reimb,
                net_expense_minor=net,
                effective_member_expense_minor=eff,
                share_of_net_spend_bps=share_bps,
                expense_count=exp_count,
                refund_count=ref_count,
                avg_ticket_minor=avg_ticket,
                evidence_txn_ids=evidence_ids,
            )
        )

    results.sort(key=lambda row: (-row.net_expense_minor, row.category_name))
    return tuple(results)


def compute_cashflow_metrics(
    snapshot: LedgerSnapshot,
    *,
    focus_month: YearMonth | None = None,
    trailing_months: int = 6,
) -> CashflowMetricsSummary:
    if trailing_months < 1 or trailing_months > 60:
        raise CFODomainError("trailing_months must be between 1 and 60")

    target_month = focus_month or YearMonth.from_date(snapshot.as_of)
    liquidity = compute_liquidity_summary(
        snapshot.accounts,
        base_currency=snapshot.base_currency,
    )

    ordered = _ordered_transactions(snapshot.transactions)
    by_month: dict[YearMonth, list[TransactionSnapshot]] = defaultdict(list)
    for txn in ordered:
        if txn.local_date <= snapshot.as_of:
            by_month[txn.year_month].append(txn)

    window_months = [
        target_month.add_months(-offset) for offset in range(trailing_months - 1, -1, -1)
    ]
    all_months = sorted(set(window_months) | set(by_month.keys()))
    monthly_series = tuple(build_monthly_point(m, by_month.get(m, [])) for m in all_months)
    point_by_month = {pt.month: pt for pt in monthly_series}
    focus_point = point_by_month[target_month]

    # Rolling burn uses completed historical months before target_month when available,
    # falling back to active months up to target_month so single-month ledgers still work.
    prior_completed = [
        point_by_month[target_month.add_months(-i)]
        for i in range(1, trailing_months + 1)
        if target_month.add_months(-i) in point_by_month
        and point_by_month[target_month.add_months(-i)].transaction_count > 0
    ]
    burn_Pool = (
        prior_completed
        if prior_completed
        else [pt for pt in monthly_series if pt.month <= target_month and pt.transaction_count > 0]
    )

    if burn_Pool:
        positive_burns = [max(0, pt.net_expense_minor) for pt in burn_Pool]
        rolling_burn = safe_divide_minor(sum(positive_burns), len(positive_burns))
        median_burn = int(round(median(positive_burns)))
        rolling_income = safe_divide_minor(sum(pt.income_minor for pt in burn_Pool), len(burn_Pool))
    else:
        rolling_burn = 0
        median_burn = 0
        rolling_income = 0

    liquid_floor = max(0, liquidity.liquid_assets_minor)
    runway_months_bps = ratio_to_bps(liquid_floor, rolling_burn) if rolling_burn > 0 else None

    focus_txns = by_month.get(target_month, [])
    categories = compute_category_breakdown(snapshot, focus_txns)
    uncat_minor = sum(
        row.net_expense_minor
        for row in categories
        if row.category_id is None and row.net_expense_minor > 0
    )
    positive_focus_spend = sum(max(0, row.net_expense_minor) for row in categories)
    uncat_bps = (
        ratio_to_bps(uncat_minor, positive_focus_spend) if positive_focus_spend > 0 else None
    )

    return CashflowMetricsSummary(
        as_of=snapshot.as_of,
        focus_month=target_month,
        liquidity=liquidity,
        monthly_series=monthly_series,
        focus_month_point=focus_point,
        focus_categories=categories,
        rolling_monthly_burn_minor=rolling_burn,
        median_monthly_burn_minor=median_burn,
        rolling_monthly_income_minor=rolling_income,
        emergency_runway_months_bps=runway_months_bps,
        uncategorized_expense_minor=uncat_minor,
        uncategorized_share_bps=uncat_bps,
    )


def _classify_driver(
    prev_net: int,
    curr_net: int,
    prev_count: int,
    curr_count: int,
    volume_effect: int,
    price_effect: int,
) -> ChangeDriverKind:
    delta = curr_net - prev_net
    if delta == 0:
        return "stable"
    if prev_count == 0 and curr_count > 0:
        return "new_spend"
    if curr_count == 0 and prev_count > 0:
        return "stopped_spend"
    abs_vol = abs(volume_effect)
    abs_price = abs(price_effect)
    if abs_vol >= 2 * max(1, abs_price):
        return "frequency_driven"
    if abs_price >= 2 * max(1, abs_vol):
        return "ticket_size_driven"
    return "mixed"


def explain_spend_change(
    snapshot: LedgerSnapshot,
    *,
    baseline_months: tuple[YearMonth, ...],
    current_months: tuple[YearMonth, ...],
    group_by: Literal["category", "payee"] = "category",
    top_n: int = 5,
) -> SpendChangeAttribution:
    """Decompose period-over-period net spend changes into frequency vs ticket-size effects.

    Uses symmetric Marshall-Edgeworth volume/price decomposition so
    ``volume_effect_minor + price_effect_minor == delta_net_minor`` up to 1 minor unit
    (adjusted deterministically into ``price_effect_minor`` for exact conservation).
    """
    if not baseline_months or not current_months:
        raise CFODomainError("baseline_months and current_months must both be non-empty")
    if top_n < 1:
        raise CFODomainError("top_n must be >= 1")

    baseline_set = set(baseline_months)
    current_set = set(current_months)
    if baseline_set & current_set:
        raise CFODomainError("baseline_months and current_months must not overlap")

    ordered = _ordered_transactions(snapshot.transactions)
    prev_buckets: dict[str, list[TransactionSnapshot]] = defaultdict(list)
    curr_buckets: dict[str, list[TransactionSnapshot]] = defaultdict(list)
    labels: dict[str, str] = {}

    for txn in ordered:
        if txn.type not in ("expense", "refund"):
            continue
        ym = txn.year_month
        if ym not in baseline_set and ym not in current_set:
            continue
        if group_by == "category":
            key = txn.category_id or "__uncategorized__"
            labels[key] = snapshot.category_name(txn.category_id)
        else:
            key = txn.payee.strip() or "未知商户"
            labels[key] = key

        if ym in baseline_set:
            prev_buckets[key].append(txn)
        else:
            curr_buckets[key].append(txn)

    all_keys = sorted(set(prev_buckets.keys()) | set(curr_buckets.keys()))
    drivers: list[SpendChangeDriver] = []
    prev_total = 0
    curr_total = 0

    base_len = len(baseline_months)
    curr_len = len(current_months)

    for key in all_keys:
        p_items = prev_buckets.get(key, [])
        c_items = curr_buckets.get(key, [])

        p_raw_net = sum(
            t.base_amount_minor if t.type == "expense" else -t.base_amount_minor for t in p_items
        )
        c_raw_net = sum(
            t.base_amount_minor if t.type == "expense" else -t.base_amount_minor for t in c_items
        )
        # Normalize to per-month average when comparing unequal multi-month windows
        p_net = safe_divide_minor(p_raw_net, base_len) if base_len > 1 else p_raw_net
        c_net = safe_divide_minor(c_raw_net, curr_len) if curr_len > 1 else c_raw_net

        prev_total += p_net
        curr_total += c_net

        p_exp_count = sum(1 for t in p_items if t.type == "expense")
        c_exp_count = sum(1 for t in c_items if t.type == "expense")

        p_avg = safe_divide_minor(max(0, p_raw_net), p_exp_count) if p_exp_count > 0 else 0
        c_avg = safe_divide_minor(max(0, c_raw_net), c_exp_count) if c_exp_count > 0 else 0

        delta = c_net - p_net
        delta_bps = ratio_to_bps(delta, p_net) if p_net > 0 else None

        if p_exp_count > 0 and c_exp_count > 0 and base_len == curr_len == 1:
            # Marshall-Edgeworth exact volume/price decomposition:
            # delta = Δcount * avg_price_mid + Δavg_price * count_mid
            vol_eff = safe_divide_minor((c_exp_count - p_exp_count) * (p_avg + c_avg), 2)
            price_eff = delta - vol_eff
        elif p_exp_count == 0:
            vol_eff = delta
            price_eff = 0
        elif c_exp_count == 0:
            vol_eff = delta
            price_eff = 0
        else:
            # Multi-month window normalized monthly rate comparison
            p_monthly_freq_x100 = safe_divide_minor(p_exp_count * 100, base_len)
            c_monthly_freq_x100 = safe_divide_minor(c_exp_count * 100, curr_len)
            vol_eff = safe_divide_minor(
                (c_monthly_freq_x100 - p_monthly_freq_x100) * (p_avg + c_avg), 200
            )
            price_eff = delta - vol_eff

        kind = _classify_driver(p_net, c_net, p_exp_count, c_exp_count, vol_eff, price_eff)
        evidence = tuple(t.id for t in (*p_items, *c_items))
        drivers.append(
            SpendChangeDriver(
                key=key,
                label=labels[key],
                previous_net_minor=p_net,
                current_net_minor=c_net,
                delta_net_minor=delta,
                delta_bps=delta_bps,
                previous_count=p_exp_count,
                current_count=c_exp_count,
                previous_avg_ticket_minor=p_avg,
                current_avg_ticket_minor=c_avg,
                volume_effect_minor=vol_eff,
                price_effect_minor=price_eff,
                driver_kind=kind,
                evidence_txn_ids=evidence,
            )
        )

    increases = sorted(
        (d for d in drivers if d.delta_net_minor > 0),
        key=lambda d: (-d.delta_net_minor, d.label),
    )[:top_n]
    decreases = sorted(
        (d for d in drivers if d.delta_net_minor < 0),
        key=lambda d: (d.delta_net_minor, d.label),
    )[:top_n]

    total_delta = curr_total - prev_total
    baseline_label = (
        str(baseline_months[0])
        if len(baseline_months) == 1
        else f"{min(baseline_months)}..{max(baseline_months)}"
    )
    current_label = (
        str(current_months[0])
        if len(current_months) == 1
        else f"{min(current_months)}..{max(current_months)}"
    )

    return SpendChangeAttribution(
        baseline_label=baseline_label,
        current_label=current_label,
        previous_total_net_minor=prev_total,
        current_total_net_minor=curr_total,
        total_delta_minor=total_delta,
        total_delta_bps=ratio_to_bps(total_delta, prev_total) if prev_total > 0 else None,
        top_increases=tuple(increases),
        top_decreases=tuple(decreases),
    )
