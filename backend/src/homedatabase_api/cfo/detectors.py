"""Recurring subscription/bill detection and spending anomaly detection.

Implements AI_ROADMAP.md §03 (Cashflow Radar: candidate recurring bills, duplicate-charge
clues, category spikes, and statistical outliers) using exact minor-unit arithmetic and
source-transaction evidence IDs.
"""

from __future__ import annotations

import calendar
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median
from typing import Literal

from .domain import (
    BPS_SCALE,
    LedgerSnapshot,
    SeverityLevel,
    TransactionSnapshot,
    YearMonth,
    ratio_to_bps,
    safe_divide_minor,
    scale_minor_by_bps,
)

RecurringCadence = Literal["weekly", "biweekly", "monthly", "quarterly", "yearly"]
RecurringStatus = Literal["active", "stale"]
AnomalyKind = Literal[
    "duplicate_charge",
    "large_outlier",
    "category_spike",
    "new_high_merchant",
    "uncategorized_drift",
]

_PAYEE_PREFIX_RE = re.compile(
    r"^(?:微信支付|支付宝|财付通|云闪付|快捷支付|银联|pos消费)[\s\-—:：/]*",
    re.IGNORECASE,
)
_PAYEE_TO_RE = re.compile(r"^向\s*(.+?)\s*(?:付款|转账|消费)$")
_PAYEE_SUFFIX_RE = re.compile(
    r"[\s\-—_/]*(?:自动续费|自动扣款|免密支付|连续包月|连续包年|代扣|"
    r"\d{4}[-/年]\d{1,2}(?:[-/月]\d{1,2}日?)?|\d{1,2}/\d{1,2}|#[A-Za-z0-9_-]{3,})\s*$",
    re.IGNORECASE,
)
_WHITESPACE_RE = re.compile(r"\s+")


def canonicalize_payee(raw_payee: str) -> str:
    """Normalize payment channel noise while preserving merchant identity."""
    text = raw_payee.strip()
    if not text:
        return ""
    text = _PAYEE_PREFIX_RE.sub("", text).strip()
    to_match = _PAYEE_TO_RE.match(text)
    if to_match:
        text = to_match.group(1).strip()
    # Apply suffix stripping up to twice (e.g. "Netflix 自动续费 2026-08")
    for _ in range(2):
        cleaned = _PAYEE_SUFFIX_RE.sub("", text).strip()
        if cleaned:
            text = cleaned
    text = _WHITESPACE_RE.sub(" ", text)
    return text.casefold()


@dataclass(frozen=True, slots=True)
class DetectorConfig:
    min_weekly_occurrences: int = 3
    min_monthly_occurrences: int = 3
    min_quarterly_occurrences: int = 2
    min_yearly_occurrences: int = 2
    max_recurring_jitter_bps: int = 2_500
    price_hike_threshold_bps: int = 500
    price_hike_min_minor: int = 100
    duplicate_window_hours: int = 48
    min_duplicate_amount_minor: int = 500
    high_severity_duplicate_minor: int = 50_000
    min_outlier_history_count: int = 4
    min_outlier_amount_minor: int = 30_000
    outlier_median_multiple_bps: int = 30_000
    category_spike_bps: int = 5_000
    min_category_spike_delta_minor: int = 20_000
    new_merchant_high_minor: int = 100_000
    uncategorized_warning_bps: int = 1_500
    min_uncategorized_minor: int = 10_000


@dataclass(frozen=True, slots=True)
class RecurringCharge:
    canonical_payee: str
    display_payee: str
    category_id: str | None
    category_name: str
    direction: Literal["expense", "income"]
    cadence: RecurringCadence
    status: RecurringStatus
    occurrences: int
    typical_amount_minor: int
    latest_amount_minor: int
    annualized_amount_minor: int
    amount_jitter_bps: int
    price_hike_minor: int
    price_hike_bps: int | None
    anchor_day_of_month: int | None
    last_occurred_on: date
    next_expected_on: date
    confidence_bps: int
    evidence_txn_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DetectedAnomaly:
    kind: AnomalyKind
    severity: SeverityLevel
    title: str
    rationale: str
    amount_minor: int
    baseline_minor: int | None
    deviation_bps: int | None
    occurred_on: date
    category_id: str | None
    evidence_txn_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _EffectiveExpense:
    txn: TransactionSnapshot
    net_base_minor: int
    canonical_payee: str


def _effective_transactions(
    transactions: tuple[TransactionSnapshot, ...],
) -> tuple[list[_EffectiveExpense], list[TransactionSnapshot]]:
    """Net linked refunds against their original expense transactions."""
    refunded_by_original: dict[str, int] = defaultdict(int)
    for txn in transactions:
        if txn.type == "refund" and txn.refund_of_id:
            refunded_by_original[txn.refund_of_id] += txn.base_amount_minor

    ordered = sorted(transactions, key=lambda item: (item.occurred_at, item.id))
    expenses: list[_EffectiveExpense] = []
    incomes: list[TransactionSnapshot] = []
    for txn in ordered:
        if txn.has_hidden_counterparty:
            continue
        if txn.type == "expense":
            remaining = txn.base_amount_minor - refunded_by_original.get(txn.id, 0)
            if remaining > 0:
                expenses.append(
                    _EffectiveExpense(
                        txn=txn,
                        net_base_minor=remaining,
                        canonical_payee=canonicalize_payee(txn.payee),
                    )
                )
        elif txn.type == "income":
            incomes.append(txn)
    return expenses, incomes


def _add_calendar_months(anchor_date: date, months: int, preferred_day: int) -> date:
    ym = YearMonth.from_date(anchor_date).add_months(months)
    clamped_day = min(preferred_day, calendar.monthrange(ym.year, ym.month)[1])
    return date(ym.year, ym.month, clamped_day)


def _infer_cadence(
    dates: list[date],
    config: DetectorConfig,
) -> tuple[RecurringCadence, int, int | None] | None:
    """Return (cadence, nominal_days, anchor_day_of_month) if intervals match a regular rhythm."""
    count = len(dates)
    if count < 2:
        return None
    gaps = [(dates[idx] - dates[idx - 1]).days for idx in range(1, count)]
    if any(gap <= 0 for gap in gaps):
        return None
    med_gap = float(median(gaps))

    if 6 <= med_gap <= 8 and count >= config.min_weekly_occurrences:
        if all(4 <= g <= 10 for g in gaps):
            return ("weekly", 7, None)

    if 13 <= med_gap <= 16 and count >= config.min_weekly_occurrences:
        if all(10 <= g <= 19 for g in gaps):
            return ("biweekly", 14, None)

    # Monthly check: either calendar month steps == 1 or 26..35 day intervals
    month_steps = [
        (dates[idx].year - dates[idx - 1].year) * 12 + (dates[idx].month - dates[idx - 1].month)
        for idx in range(1, count)
    ]
    if count >= config.min_monthly_occurrences and (
        all(step == 1 and 20 <= gap <= 40 for step, gap in zip(month_steps, gaps, strict=True))
        or (26 <= med_gap <= 34 and all(22 <= g <= 38 for g in gaps))
    ):
        anchor_day = int(round(median(d.day for d in dates)))
        return ("monthly", 30, anchor_day)

    if count >= config.min_quarterly_occurrences and (
        all(step == 3 and 75 <= gap <= 105 for step, gap in zip(month_steps, gaps, strict=True))
        or (84 <= med_gap <= 97 and all(75 <= g <= 105 for g in gaps))
    ):
        anchor_day = int(round(median(d.day for d in dates)))
        return ("quarterly", 91, anchor_day)

    if count >= config.min_yearly_occurrences and (
        all(step == 12 and 345 <= gap <= 385 for step, gap in zip(month_steps, gaps, strict=True))
        or (355 <= med_gap <= 375 and all(345 <= g <= 385 for g in gaps))
    ):
        anchor_day = int(round(median(d.day for d in dates)))
        return ("yearly", 365, anchor_day)

    return None


def _annualize_minor(amount_minor: int, cadence: RecurringCadence) -> int:
    if cadence == "weekly":
        return amount_minor * 52
    if cadence == "biweekly":
        return amount_minor * 26
    if cadence == "monthly":
        return amount_minor * 12
    if cadence == "quarterly":
        return amount_minor * 4
    return amount_minor


def _project_next_date(
    last_date: date,
    as_of: date,
    cadence: RecurringCadence,
    anchor_day: int | None,
) -> tuple[date, RecurringStatus]:
    if cadence == "weekly":
        next_d = last_date + timedelta(days=7)
        stale_cutoff = 14
    elif cadence == "biweekly":
        next_d = last_date + timedelta(days=14)
        stale_cutoff = 25
    elif cadence == "monthly":
        day = anchor_day or last_date.day
        next_d = _add_calendar_months(last_date, 1, day)
        stale_cutoff = 50
    elif cadence == "quarterly":
        day = anchor_day or last_date.day
        next_d = _add_calendar_months(last_date, 3, day)
        stale_cutoff = 135
    else:
        day = anchor_day or last_date.day
        next_d = _add_calendar_months(last_date, 12, day)
        stale_cutoff = 420

    status: RecurringStatus = "stale" if (as_of - last_date).days > stale_cutoff else "active"
    return next_d, status


def detect_recurring_charges(
    snapshot: LedgerSnapshot,
    *,
    config: DetectorConfig | None = None,
    include_income: bool = True,
) -> tuple[RecurringCharge, ...]:
    cfg = config or DetectorConfig()
    expenses, incomes = _effective_transactions(snapshot.transactions)

    series_groups: dict[tuple[str, str], list[tuple[date, int, TransactionSnapshot]]] = defaultdict(
        list
    )
    for item in expenses:
        if item.canonical_payee and item.txn.local_date <= snapshot.as_of:
            series_groups[("expense", item.canonical_payee)].append(
                (item.txn.local_date, item.net_base_minor, item.txn)
            )
    if include_income:
        for txn in incomes:
            canon = canonicalize_payee(txn.payee)
            if canon and txn.local_date <= snapshot.as_of:
                series_groups[("income", canon)].append(
                    (txn.local_date, txn.base_amount_minor, txn)
                )

    results: list[RecurringCharge] = []
    for (direction, canon), raw_points in series_groups.items():
        # Collapse same-day multiple postings of the same payee only if distinct days exist;
        # keep latest per date when inferring cadence to avoid duplicate-charge distortion.
        by_day: dict[date, tuple[int, TransactionSnapshot]] = {}
        all_txns_ordered: list[TransactionSnapshot] = []
        for occurred_on, amt, txn in raw_points:
            all_txns_ordered.append(txn)
            if occurred_on not in by_day:
                by_day[occurred_on] = (amt, txn)
            else:
                prev_amt, prev_txn = by_day[occurred_on]
                # Keep median-closer or sum? Keep first posting for cadence, preserve evidence
                by_day[occurred_on] = (prev_amt, prev_txn)

        distinct_days = sorted(by_day.keys())
        inferred = _infer_cadence(distinct_days, cfg)
        if inferred is None:
            continue
        cadence, nominal_days, anchor_day = inferred

        amounts = [by_day[d][0] for d in distinct_days]
        typical_minor = int(round(median(amounts)))
        if typical_minor <= 0:
            continue

        # Measure relative dispersion using MAD / median (robust to a single recent price hike)
        abs_devs = [abs(a - typical_minor) for a in amounts]
        mad_minor = int(round(median(abs_devs)))
        jitter_bps = ratio_to_bps(mad_minor, typical_minor) or 0
        max_rel_bps = ratio_to_bps(max(abs_devs), typical_minor) or 0
        if (
            jitter_bps > cfg.max_recurring_jitter_bps
            and max_rel_bps > cfg.max_recurring_jitter_bps * 2
        ):
            continue

        latest_date = distinct_days[-1]
        latest_minor, latest_txn = by_day[latest_date]
        prior_amounts = amounts[:-1]
        prior_typical = int(round(median(prior_amounts))) if prior_amounts else typical_minor

        hike_minor = 0
        hike_bps: int | None = None
        if direction == "expense" and latest_minor > prior_typical:
            diff = latest_minor - prior_typical
            rel = ratio_to_bps(diff, prior_typical) or 0
            if diff >= cfg.price_hike_min_minor and rel >= cfg.price_hike_threshold_bps:
                hike_minor = diff
                hike_bps = rel

        gaps = [
            (distinct_days[i] - distinct_days[i - 1]).days for i in range(1, len(distinct_days))
        ]
        gap_err_bps = (
            ratio_to_bps(int(round(median([abs(g - nominal_days) for g in gaps]))), nominal_days)
            or 0
        )
        occurrence_bonus = min(1_500, (len(distinct_days) - 2) * 500)
        raw_conf = BPS_SCALE - min(4_000, jitter_bps) - min(3_000, gap_err_bps) + occurrence_bonus
        confidence_bps = max(3_000, min(BPS_SCALE, raw_conf))

        next_expected, status = _project_next_date(latest_date, snapshot.as_of, cadence, anchor_day)
        projection_basis = latest_minor if hike_minor > 0 else typical_minor

        results.append(
            RecurringCharge(
                canonical_payee=canon,
                display_payee=latest_txn.payee,
                category_id=latest_txn.category_id,
                category_name=snapshot.category_name(latest_txn.category_id),
                direction="expense" if direction == "expense" else "income",
                cadence=cadence,
                status=status,
                occurrences=len(distinct_days),
                typical_amount_minor=typical_minor,
                latest_amount_minor=latest_minor,
                annualized_amount_minor=_annualize_minor(projection_basis, cadence),
                amount_jitter_bps=jitter_bps,
                price_hike_minor=hike_minor,
                price_hike_bps=hike_bps,
                anchor_day_of_month=anchor_day,
                last_occurred_on=latest_date,
                next_expected_on=next_expected,
                confidence_bps=confidence_bps,
                evidence_txn_ids=tuple(t.id for t in all_txns_ordered),
            )
        )

    results.sort(
        key=lambda item: (
            0 if item.status == "active" else 1,
            0 if item.direction == "expense" else 1,
            -item.annualized_amount_minor,
            item.canonical_payee,
        )
    )
    return tuple(results)


def detect_spending_anomalies(
    snapshot: LedgerSnapshot,
    *,
    focus_month: YearMonth | None = None,
    recurring_charges: tuple[RecurringCharge, ...] | None = None,
    config: DetectorConfig | None = None,
) -> tuple[DetectedAnomaly, ...]:
    """Detect duplicate charges, large ticket outliers, category spikes, and hygiene drift."""
    cfg = config or DetectorConfig()
    target_month = focus_month or YearMonth.from_date(snapshot.as_of)
    recurring = (
        recurring_charges
        if recurring_charges is not None
        else detect_recurring_charges(snapshot, config=cfg)
    )
    recurring_txn_ids = {
        txn_id
        for item in recurring
        if item.direction == "expense" and item.status == "active"
        for txn_id in item.evidence_txn_ids
    }

    expenses, _incomes = _effective_transactions(snapshot.transactions)
    visible_expenses = [item for item in expenses if item.txn.local_date <= snapshot.as_of]

    anomalies: list[DetectedAnomaly] = []

    # 1. Duplicate charge suspects inside focus_month (comparing against trailing window too)
    window_delta = timedelta(hours=cfg.duplicate_window_hours)
    flagged_dup_pairs: set[tuple[str, str]] = set()
    for idx, current in enumerate(visible_expenses):
        if current.txn.year_month != target_month:
            continue
        if current.net_base_minor < cfg.min_duplicate_amount_minor or not current.canonical_payee:
            continue
        for prev_idx in range(idx - 1, -1, -1):
            prev = visible_expenses[prev_idx]
            elapsed = current.txn.occurred_at - prev.txn.occurred_at
            if elapsed > window_delta:
                break
            if (
                prev.canonical_payee == current.canonical_payee
                and prev.net_base_minor == current.net_base_minor
                and prev.txn.currency == current.txn.currency
            ):
                pair = (prev.txn.id, current.txn.id)
                if pair not in flagged_dup_pairs:
                    flagged_dup_pairs.add(pair)
                    sev: SeverityLevel = (
                        "high"
                        if current.net_base_minor >= cfg.high_severity_duplicate_minor
                        else "warning"
                    )
                    hours_apart = max(0, int(elapsed.total_seconds() // 3600))
                    anomalies.append(
                        DetectedAnomaly(
                            kind="duplicate_charge",
                            severity=sev,
                            title=f"疑似重复扣费：{current.txn.payee}",
                            rationale=(
                                f"在 {hours_apart} 小时内出现两笔同商户、同金额支出"
                                f"（均未全额退款），建议核对是否重复入账或重复扣款。"
                            ),
                            amount_minor=current.net_base_minor,
                            baseline_minor=prev.net_base_minor,
                            deviation_bps=BPS_SCALE,
                            occurred_on=current.txn.local_date,
                            category_id=current.txn.category_id,
                            evidence_txn_ids=pair,
                        )
                    )
                break

    # Group historical vs focus-month expenses
    prior_by_cat: dict[str | None, list[_EffectiveExpense]] = defaultdict(list)
    focus_by_cat: dict[str | None, list[_EffectiveExpense]] = defaultdict(list)
    prior_payees: set[str] = set()
    prior_month_cat_totals: dict[YearMonth, dict[str | None, int]] = defaultdict(
        lambda: defaultdict(int)
    )

    for item in visible_expenses:
        ym = item.txn.year_month
        if ym < target_month:
            prior_by_cat[item.txn.category_id].append(item)
            if item.canonical_payee:
                prior_payees.add(item.canonical_payee)
            prior_month_cat_totals[ym][item.txn.category_id] += item.net_base_minor
        elif ym == target_month:
            focus_by_cat[item.txn.category_id].append(item)

    # Include standalone refunds (without refund_of_id) in monthly category totals
    focus_unlinked_refunds: dict[str | None, int] = defaultdict(int)
    for txn in snapshot.transactions:
        if txn.type == "refund" and txn.refund_of_id is None and txn.local_date <= snapshot.as_of:
            ym = txn.year_month
            if ym < target_month:
                prior_month_cat_totals[ym][txn.category_id] -= txn.base_amount_minor
            elif ym == target_month:
                focus_unlinked_refunds[txn.category_id] += txn.base_amount_minor

    # 2. Large single-transaction outlier & 3. First-time high-ticket merchant
    for cat_id, focus_items in focus_by_cat.items():
        history = prior_by_cat.get(cat_id, [])
        # Use prior history when large enough; otherwise leave-one-out on all category items
        all_cat_items = history + focus_items
        for item in focus_items:
            if item.txn.id in recurring_txn_ids:
                continue

            peer_pool = (
                [h.net_base_minor for h in history]
                if len(history) >= cfg.min_outlier_history_count
                else [
                    other.net_base_minor for other in all_cat_items if other.txn.id != item.txn.id
                ]
            )
            is_outlier = False
            if len(peer_pool) >= cfg.min_outlier_history_count:
                med_val = int(round(median(peer_pool)))
                mad_val = int(round(median([abs(x - med_val) for x in peer_pool])))
                mult_floor = scale_minor_by_bps(med_val, cfg.outlier_median_multiple_bps)
                spread_floor = med_val + 4 * max(mad_val, max(100, med_val // 5))
                fence = max(cfg.min_outlier_amount_minor, mult_floor, spread_floor)
                if item.net_base_minor >= fence and med_val > 0:
                    dev_bps = ratio_to_bps(item.net_base_minor - med_val, med_val)
                    sev = "high" if item.net_base_minor >= fence * 2 else "warning"
                    cat_label = snapshot.category_name(cat_id)
                    anomalies.append(
                        DetectedAnomaly(
                            kind="large_outlier",
                            severity=sev,
                            title=f"单笔大额异常：{item.txn.payee}",
                            rationale=(
                                f"「{cat_label}」单笔净支出显著高于历史中位水平，建议确认消费归类或分摊。"
                            ),
                            amount_minor=item.net_base_minor,
                            baseline_minor=med_val,
                            deviation_bps=dev_bps,
                            occurred_on=item.txn.local_date,
                            category_id=cat_id,
                            evidence_txn_ids=(item.txn.id,),
                        )
                    )
                    is_outlier = True

            if (
                not is_outlier
                and prior_payees
                and item.canonical_payee
                and item.canonical_payee not in prior_payees
                and item.net_base_minor >= cfg.new_merchant_high_minor
            ):
                anomalies.append(
                    DetectedAnomaly(
                        kind="new_high_merchant",
                        severity="info",
                        title=f"新商户大额支出：{item.txn.payee}",
                        rationale="首次出现的交易对手且单笔金额较高，建议复核账单明细与分类。",
                        amount_minor=item.net_base_minor,
                        baseline_minor=None,
                        deviation_bps=None,
                        occurred_on=item.txn.local_date,
                        category_id=cat_id,
                        evidence_txn_ids=(item.txn.id,),
                    )
                )

    # 4. Category monthly spend spike vs prior active months
    prior_active_months = sorted(prior_month_cat_totals.keys())[-6:]
    if prior_active_months:
        for cat_id, focus_items in focus_by_cat.items():
            if cat_id is None:
                continue
            focus_net = sum(i.net_base_minor for i in focus_items) - focus_unlinked_refunds.get(
                cat_id, 0
            )
            if focus_net <= 0:
                continue
            hist_vals = [
                max(0, prior_month_cat_totals[m].get(cat_id, 0)) for m in prior_active_months
            ]
            baseline_avg = safe_divide_minor(sum(hist_vals), len(hist_vals))
            if baseline_avg <= 0:
                continue
            delta = focus_net - baseline_avg
            dev_bps = ratio_to_bps(delta, baseline_avg) or 0
            if delta >= cfg.min_category_spike_delta_minor and dev_bps >= cfg.category_spike_bps:
                cat_label = snapshot.category_name(cat_id)
                sev = "high" if dev_bps >= BPS_SCALE else "warning"
                anomalies.append(
                    DetectedAnomaly(
                        kind="category_spike",
                        severity=sev,
                        title=f"分类支出激增：{cat_label}",
                        rationale=(
                            f"{target_month}「{cat_label}」净支出较前 "
                            f"{len(prior_active_months)} 个月均值上浮 {dev_bps / 100:.1f}%。"
                        ),
                        amount_minor=focus_net,
                        baseline_minor=baseline_avg,
                        deviation_bps=dev_bps,
                        occurred_on=max(i.txn.local_date for i in focus_items),
                        category_id=cat_id,
                        evidence_txn_ids=tuple(i.txn.id for i in focus_items),
                    )
                )

    # 5. Uncategorized hygiene drift in focus month
    uncat_items = focus_by_cat.get(None, [])
    if uncat_items:
        uncat_net = sum(i.net_base_minor for i in uncat_items) - focus_unlinked_refunds.get(None, 0)
        total_focus_net = sum(
            max(
                0,
                sum(i.net_base_minor for i in items) - focus_unlinked_refunds.get(cid, 0),
            )
            for cid, items in focus_by_cat.items()
        )
        share_bps = ratio_to_bps(max(0, uncat_net), total_focus_net) if total_focus_net > 0 else 0
        if (
            uncat_net >= cfg.min_uncategorized_minor
            and (share_bps or 0) >= cfg.uncategorized_warning_bps
        ):
            anomalies.append(
                DetectedAnomaly(
                    kind="uncategorized_drift",
                    severity="info",
                    title="待分类支出占比较高",
                    rationale=(
                        f"{target_month} 共有 {len(uncat_items)} 笔支出尚未归类，"
                        f"占当月净支出 {(share_bps or 0) / 100:.1f}%，"
                        "会影响分类预算与趋势归因精度。"
                    ),
                    amount_minor=uncat_net,
                    baseline_minor=total_focus_net,
                    deviation_bps=share_bps,
                    occurred_on=max(i.txn.local_date for i in uncat_items),
                    category_id=None,
                    evidence_txn_ids=tuple(i.txn.id for i in uncat_items),
                )
            )

    severity_rank = {"high": 0, "warning": 1, "info": 2}
    anomalies.sort(key=lambda item: (severity_rank[item.severity], -item.amount_minor, item.kind))
    return tuple(anomalies)
