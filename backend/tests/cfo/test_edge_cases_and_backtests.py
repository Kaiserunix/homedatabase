from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace

from homedatabase_api.cfo import (
    AccountSnapshot,
    CategorySnapshot,
    CFOAnalyticalEngine,
    DeterministicTemplateNarrator,
    LedgerSnapshot,
    ScheduledCashflowItem,
    TransactionSnapshot,
    WhatIfAdjustment,
    WhatIfPlanOption,
    YearMonth,
    build_narration_packet,
    build_snapshot_from_ledger_records,
    compare_what_if_plans,
    detect_recurring_charges,
    explain_spend_change,
)


def _dt(year: int, month: int, day: int, hour: int = 12) -> datetime:
    return datetime.fromisoformat(
        f"{year:04d}-{month:02d}-{day:02d}T{hour:02d}:00:00+08:00"
    ).astimezone(UTC)


def test_empty_ledger_zero_division_safety() -> None:
    empty = LedgerSnapshot(as_of=date(2026, 8, 20), base_currency="CNY")
    report = CFOAnalyticalEngine().analyze(empty)

    assert report.metrics.focus_month_point.income_minor == 0
    assert report.metrics.focus_month_point.net_expense_minor == 0
    assert report.metrics.focus_month_point.savings_rate_bps is None
    assert report.metrics.emergency_runway_months_bps is None
    assert report.recurring_charges == ()
    assert report.anomalies == ()
    assert report.budget_evaluations == ()
    assert report.forecast.baseline.closing_cash_minor == 0
    assert report.scorecard.score == 100
    assert report.scorecard.grade == "A"

    narration = DeterministicTemplateNarrator().narrate(build_narration_packet(report, empty))
    assert narration.source == "deterministic"
    assert len(narration.assumptions_and_scope) >= 1


def test_weekly_biweekly_quarterly_and_yearly_cadences() -> None:
    txns = (
        # Weekly cleaner every 7 days
        TransactionSnapshot(
            id="w-1",
            occurred_at=_dt(2026, 8, 1),
            type="expense",
            payee="家政保洁",
            amount_minor=15_000,
            base_amount_minor=15_000,
        ),
        TransactionSnapshot(
            id="w-2",
            occurred_at=_dt(2026, 8, 8),
            type="expense",
            payee="家政保洁",
            amount_minor=15_000,
            base_amount_minor=15_000,
        ),
        TransactionSnapshot(
            id="w-3",
            occurred_at=_dt(2026, 8, 15),
            type="expense",
            payee="家政保洁",
            amount_minor=15_000,
            base_amount_minor=15_000,
        ),
        # Biweekly organic farm box every 14 days
        TransactionSnapshot(
            id="bw-1",
            occurred_at=_dt(2026, 7, 4),
            type="expense",
            payee="有机蔬菜配送",
            amount_minor=12_000,
            base_amount_minor=12_000,
        ),
        TransactionSnapshot(
            id="bw-2",
            occurred_at=_dt(2026, 7, 18),
            type="expense",
            payee="有机蔬菜配送",
            amount_minor=12_000,
            base_amount_minor=12_000,
        ),
        TransactionSnapshot(
            id="bw-3",
            occurred_at=_dt(2026, 8, 1),
            type="expense",
            payee="有机蔬菜配送",
            amount_minor=12_000,
            base_amount_minor=12_000,
        ),
        # Quarterly property fee
        TransactionSnapshot(
            id="q-1",
            occurred_at=_dt(2026, 2, 10),
            type="expense",
            payee="万科物业费",
            amount_minor=90_000,
            base_amount_minor=90_000,
        ),
        TransactionSnapshot(
            id="q-2",
            occurred_at=_dt(2026, 5, 10),
            type="expense",
            payee="万科物业费",
            amount_minor=90_000,
            base_amount_minor=90_000,
        ),
        TransactionSnapshot(
            id="q-3",
            occurred_at=_dt(2026, 8, 10),
            type="expense",
            payee="万科物业费",
            amount_minor=90_000,
            base_amount_minor=90_000,
        ),
        # Yearly home insurance
        TransactionSnapshot(
            id="y-1",
            occurred_at=_dt(2025, 8, 12),
            type="expense",
            payee="家庭财产险",
            amount_minor=120_000,
            base_amount_minor=120_000,
        ),
        TransactionSnapshot(
            id="y-2",
            occurred_at=_dt(2026, 8, 12),
            type="expense",
            payee="家庭财产险",
            amount_minor=120_000,
            base_amount_minor=120_000,
        ),
    )

    snapshot = LedgerSnapshot(as_of=date(2026, 8, 18), transactions=txns)
    by_payee = {r.canonical_payee: r for r in detect_recurring_charges(snapshot)}

    assert by_payee["家政保洁"].cadence == "weekly"
    assert by_payee["家政保洁"].annualized_amount_minor == 15_000 * 52
    assert by_payee["家政保洁"].next_expected_on == date(2026, 8, 22)

    assert by_payee["有机蔬菜配送"].cadence == "biweekly"
    assert by_payee["有机蔬菜配送"].annualized_amount_minor == 12_000 * 26

    assert by_payee["万科物业费"].cadence == "quarterly"
    assert by_payee["万科物业费"].annualized_amount_minor == 90_000 * 4
    assert by_payee["万科物业费"].next_expected_on == date(2026, 11, 10)

    assert by_payee["家庭财产险"].cadence == "yearly"
    assert by_payee["家庭财产险"].annualized_amount_minor == 120_000
    assert by_payee["家庭财产险"].next_expected_on == date(2027, 8, 12)


def test_multi_month_window_attribution_and_fx_split_scaling() -> None:
    # 3-month vs 3-month comparison (Q2 vs Q3 takeout)
    categories = (CategorySnapshot(id="c-food", name="外卖", kind="expense"),)
    txns: list[TransactionSnapshot] = []
    for month in (3, 4, 5):
        for idx in range(3):
            txns.append(
                TransactionSnapshot(
                    id=f"q2-{month}-{idx}",
                    occurred_at=_dt(2026, month, 5 + idx),
                    type="expense",
                    payee="饿了么",
                    category_id="c-food",
                    amount_minor=4_000,
                    base_amount_minor=4_000,
                )
            )
    for month in (6, 7, 8):
        for idx in range(10):
            txns.append(
                TransactionSnapshot(
                    id=f"q3-{month}-{idx}",
                    occurred_at=_dt(2026, month, 5 + idx),
                    type="expense",
                    payee="饿了么",
                    category_id="c-food",
                    amount_minor=4_000,
                    base_amount_minor=4_000,
                )
            )

    snap = LedgerSnapshot(
        as_of=date(2026, 8, 31),
        categories=categories,
        transactions=tuple(txns),
    )
    attr = explain_spend_change(
        snap,
        baseline_months=(YearMonth(2026, 3), YearMonth(2026, 4), YearMonth(2026, 5)),
        current_months=(YearMonth(2026, 6), YearMonth(2026, 7), YearMonth(2026, 8)),
    )
    assert attr.previous_total_net_minor == 12_000  # ¥120/mo average
    assert attr.current_total_net_minor == 40_000  # ¥400/mo average
    assert attr.top_increases[0].driver_kind == "frequency_driven"

    # Duck-typed FX transaction ($100.00 = ¥715.00) split 50/50 -> ¥357.50 reimbursed
    fx_acct = SimpleNamespace(
        id="a-usd-card",
        name="USD Card",
        kind="liability",
        scope="family",
        owner_id=None,
        currency="USD",
        opening_balance_minor=0,
        deleted_at=None,
    )
    fx_txn = SimpleNamespace(
        id="t-fx-1",
        occurred_at=_dt(2026, 8, 10),
        type="expense",
        payee="Booking Hotel",
        note="",
        category_id="c-food",
        currency="USD",
        amount_minor=10_000,
        base_currency="CNY",
        base_amount_minor=71_500,
        refund_of_id=None,
        deleted_at=None,
        lines=[
            SimpleNamespace(
                account_id=None,
                ledger_code="expense:c-food",
                debit_minor=71_500,
                credit_minor=0,
                account_amount_minor=None,
            ),
            SimpleNamespace(
                account_id="a-usd-card",
                ledger_code=None,
                debit_minor=0,
                credit_minor=71_500,
                account_amount_minor=10_000,
            ),
        ],
    )
    fx_split = SimpleNamespace(
        transaction_id="t-fx-1",
        created_by="m-mom",
        status="settled",
        shares=[
            SimpleNamespace(member_id="m-mom", share_minor=5_000, status="accepted"),
            SimpleNamespace(member_id="m-dad", share_minor=5_000, status="accepted"),
        ],
    )
    fx_snap = build_snapshot_from_ledger_records(
        as_of=date(2026, 8, 31),
        accounts=[fx_acct],
        categories=[],
        transactions=[fx_txn],
        splits=[fx_split],
        viewer_member_id="m-mom",
    )
    assert fx_snap.accounts[0].balance_minor == 10_000  # $100.00 liability owed
    assert fx_snap.transactions[0].split_reimbursed_minor == 35_750


def test_what_if_deferred_scheduled_expense_avoids_mid_month_shortfall() -> None:
    snap = LedgerSnapshot(
        as_of=date(2026, 8, 1),
        accounts=(
            AccountSnapshot(
                id="a-cash",
                name="储蓄卡",
                kind="asset",
                scope="family",
                currency="CNY",
                balance_minor=2_000_000,  # ¥20,000 opening
            ),
        ),
        scheduled_items=(
            ScheduledCashflowItem(
                id="sch-reno",
                label="阳台封窗尾款",
                direction="expense",
                amount_minor=1_500_000,  # ¥15,000 due Aug 10 (before Aug 25 salary!)
                due_date=date(2026, 8, 10),
            ),
            ScheduledCashflowItem(
                id="sch-pay",
                label="8月发薪",
                direction="income",
                amount_minor=2_000_000,  # ¥20,000 due Aug 25
                due_date=date(2026, 8, 25),
            ),
        ),
    )

    options = (
        WhatIfPlanOption(option_id="keep", title="按原定 8月10日 支付尾款"),
        WhatIfPlanOption(
            option_id="defer",
            title="协商延后 20 天至发薪后支付 + 每月兼职增收",
            defer_scheduled_ids=(("sch-reno", 20),),
            adjustments=(
                WhatIfAdjustment(
                    label="兼职咨询",
                    direction="income",
                    amount_minor=200_000,
                    effective_on=date(2026, 8, 20),
                    cadence="monthly",
                ),
            ),
        ),
    )
    outcomes = {
        o.option_id: o
        for o in compare_what_if_plans(
            snap,
            options,
            horizon_days=60,
            safety_floor_minor=1_000_000,  # ¥10,000 safety floor
        )
    }
    assert outcomes["keep"].feasible_under_floor is False  # Drops to ¥5,000 on Aug 10
    assert outcomes["defer"].feasible_under_floor is True  # Never drops below ¥20,000!
    assert outcomes["defer"].ending_cash_minor == outcomes["keep"].ending_cash_minor + 400_000
