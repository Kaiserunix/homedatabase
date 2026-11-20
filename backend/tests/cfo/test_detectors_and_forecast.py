from __future__ import annotations

from datetime import UTC, date, datetime

from homedatabase_api.cfo import (
    AccountSnapshot,
    CategoryBudgetTarget,
    CategorySnapshot,
    LedgerSnapshot,
    ScheduledCashflowItem,
    TransactionSnapshot,
    WhatIfAdjustment,
    WhatIfPlanOption,
    YearMonth,
    canonicalize_payee,
    compare_what_if_plans,
    detect_recurring_charges,
    detect_spending_anomalies,
    evaluate_category_budgets,
    forecast_cashflow,
)


def _dt(year: int, month: int, day: int, hour: int = 12) -> datetime:
    return datetime.fromisoformat(
        f"{year:04d}-{month:02d}-{day:02d}T{hour:02d}:00:00+08:00"
    ).astimezone(UTC)


def test_payee_canonicalization_handles_chinese_payment_channels() -> None:
    assert canonicalize_payee("支付宝 - 网易云音乐自动续费 2026-08") == "网易云音乐"
    assert canonicalize_payee("微信支付-向山姆会员商店付款") == "山姆会员商店"
    assert canonicalize_payee("APPLE.COM/BILL 08/15") == "apple.com/bill"


def test_recurring_detection_price_hike_month_end_and_refund_exclusion() -> None:
    categories = (
        CategorySnapshot(id="c-fun", name="娱乐订阅", kind="expense"),
        CategorySnapshot(id="c-home", name="居住", kind="expense"),
    )
    txns = (
        # Monthly streaming subscription with a price hike in August (¥25 -> ¥30)
        TransactionSnapshot(
            id="sub-1",
            occurred_at=_dt(2026, 5, 15),
            type="expense",
            payee="微信支付 - 网易云音乐自动续费 2026-05",
            category_id="c-fun",
            amount_minor=2_500,
            base_amount_minor=2_500,
        ),
        TransactionSnapshot(
            id="sub-2",
            occurred_at=_dt(2026, 6, 15),
            type="expense",
            payee="支付宝 - 网易云音乐自动续费 2026-06",
            category_id="c-fun",
            amount_minor=2_500,
            base_amount_minor=2_500,
        ),
        TransactionSnapshot(
            id="sub-3",
            occurred_at=_dt(2026, 7, 15),
            type="expense",
            payee="网易云音乐 2026-07",
            category_id="c-fun",
            amount_minor=2_500,
            base_amount_minor=2_500,
        ),
        TransactionSnapshot(
            id="sub-4",
            occurred_at=_dt(2026, 8, 15),
            type="expense",
            payee="网易云音乐自动续费",
            category_id="c-fun",
            amount_minor=3_000,
            base_amount_minor=3_000,
        ),
        # Month-end rent on Jan 31, Feb 28, Mar 31 -> anchor day 31
        TransactionSnapshot(
            id="rent-1",
            occurred_at=_dt(2026, 1, 31),
            type="expense",
            payee="向自如房租付款",
            category_id="c-home",
            amount_minor=450_000,
            base_amount_minor=450_000,
        ),
        TransactionSnapshot(
            id="rent-2",
            occurred_at=_dt(2026, 2, 28),
            type="expense",
            payee="向自如房租付款",
            category_id="c-home",
            amount_minor=450_000,
            base_amount_minor=450_000,
        ),
        TransactionSnapshot(
            id="rent-3",
            occurred_at=_dt(2026, 3, 31),
            type="expense",
            payee="向自如房租付款",
            category_id="c-home",
            amount_minor=450_000,
            base_amount_minor=450_000,
        ),
        # Trial subscription whose 3rd occurrence was 100% refunded -> only 2 net occurrences (<3)
        TransactionSnapshot(
            id="trial-1",
            occurred_at=_dt(2026, 6, 1),
            type="expense",
            payee="某修图App连续包月",
            category_id="c-fun",
            amount_minor=6_800,
            base_amount_minor=6_800,
        ),
        TransactionSnapshot(
            id="trial-2",
            occurred_at=_dt(2026, 7, 1),
            type="expense",
            payee="某修图App连续包月",
            category_id="c-fun",
            amount_minor=6_800,
            base_amount_minor=6_800,
        ),
        TransactionSnapshot(
            id="trial-3",
            occurred_at=_dt(2026, 8, 1),
            type="expense",
            payee="某修图App连续包月",
            category_id="c-fun",
            amount_minor=6_800,
            base_amount_minor=6_800,
        ),
        TransactionSnapshot(
            id="trial-3-ref",
            occurred_at=_dt(2026, 8, 2),
            type="refund",
            payee="某修图App退款",
            category_id="c-fun",
            amount_minor=6_800,
            base_amount_minor=6_800,
            refund_of_id="trial-3",
        ),
    )

    snapshot = LedgerSnapshot(
        as_of=date(2026, 8, 20),
        categories=categories,
        transactions=txns,
    )
    charges = detect_recurring_charges(snapshot)
    by_canon = {item.canonical_payee: item for item in charges}

    assert "某修图app" not in by_canon
    music = by_canon["网易云音乐"]
    assert music.cadence == "monthly"
    assert music.status == "active"
    assert music.price_hike_minor == 500
    assert music.price_hike_bps == 2_000  # +20%
    assert music.annualized_amount_minor == 36_000  # uses hiked ¥30 * 12
    assert music.next_expected_on == date(2026, 9, 15)

    rent = by_canon["自如房租"]
    assert rent.status == "stale"  # Stopped after March, as_of is August 20
    assert rent.next_expected_on == date(2026, 4, 30)  # Clamped April 30!


def test_anomaly_detection_duplicates_outliers_and_recurring_exemption() -> None:
    categories = (
        CategorySnapshot(id="c-food", name="餐饮", kind="expense"),
        CategorySnapshot(id="c-home", name="房租", kind="expense"),
    )
    txns: list[TransactionSnapshot] = [
        # Monthly rent in June, July, August -> active recurring, must NOT be flagged as outlier
        TransactionSnapshot(
            id="r-6",
            occurred_at=_dt(2026, 6, 5),
            type="expense",
            payee="房东房租",
            category_id="c-home",
            amount_minor=500_000,
            base_amount_minor=500_000,
        ),
        TransactionSnapshot(
            id="r-7",
            occurred_at=_dt(2026, 7, 5),
            type="expense",
            payee="房东房租",
            category_id="c-home",
            amount_minor=500_000,
            base_amount_minor=500_000,
        ),
        TransactionSnapshot(
            id="r-8",
            occurred_at=_dt(2026, 8, 5),
            type="expense",
            payee="房东房租",
            category_id="c-home",
            amount_minor=500_000,
            base_amount_minor=500_000,
        ),
    ]
    # 6 normal meals around ¥35 in July
    for day in range(10, 16):
        txns.append(
            TransactionSnapshot(
                id=f"meal-7-{day}",
                occurred_at=_dt(2026, 7, day),
                type="expense",
                payee="日常快餐",
                category_id="c-food",
                amount_minor=3_500,
                base_amount_minor=3_500,
            )
        )
    # Duplicate charge in August (2 hours apart, same merchant & amount)
    txns.append(
        TransactionSnapshot(
            id="dup-a",
            occurred_at=_dt(2026, 8, 10, 12),
            type="expense",
            payee="星巴克咖啡",
            category_id="c-food",
            amount_minor=3_800,
            base_amount_minor=3_800,
        )
    )
    txns.append(
        TransactionSnapshot(
            id="dup-b",
            occurred_at=_dt(2026, 8, 10, 14),
            type="expense",
            payee="微信支付 - 星巴克咖啡",
            category_id="c-food",
            amount_minor=3_800,
            base_amount_minor=3_800,
        )
    )
    # Huge banquet outlier in August
    txns.append(
        TransactionSnapshot(
            id="banquet-1",
            occurred_at=_dt(2026, 8, 18, 19),
            type="expense",
            payee="海鲜酒楼宴请",
            category_id="c-food",
            amount_minor=280_000,
            base_amount_minor=280_000,
        )
    )
    # Uncategorized spend in August
    txns.append(
        TransactionSnapshot(
            id="uncat-1",
            occurred_at=_dt(2026, 8, 19, 10),
            type="expense",
            payee="未知商户扫码",
            category_id=None,
            amount_minor=180_000,
            base_amount_minor=180_000,
        )
    )

    snapshot = LedgerSnapshot(
        as_of=date(2026, 8, 25),
        categories=categories,
        transactions=tuple(txns),
    )
    anomalies = detect_spending_anomalies(snapshot, focus_month=YearMonth(2026, 8))
    kinds = {a.kind for a in anomalies}

    assert "duplicate_charge" in kinds
    assert "large_outlier" in kinds
    assert "category_spike" in kinds
    assert "uncategorized_drift" in kinds

    dup = next(a for a in anomalies if a.kind == "duplicate_charge")
    assert dup.evidence_txn_ids == ("dup-a", "dup-b")

    # Rent r-8 must NOT be flagged as a large_outlier
    outlier_ids = {
        tid for a in anomalies if a.kind == "large_outlier" for tid in a.evidence_txn_ids
    }
    assert "banquet-1" in outlier_ids
    assert "r-8" not in outlier_ids


def test_recurring_aware_budget_pace_does_not_over_extrapolate_rent() -> None:
    categories = (
        CategorySnapshot(id="c-home", name="居住", kind="expense"),
        CategorySnapshot(id="c-food", name="餐饮", kind="expense"),
    )
    budgets = (
        CategoryBudgetTarget(category_id="c-home", monthly_limit_minor=550_000),  # ¥5,500 limit
        CategoryBudgetTarget(category_id="c-food", monthly_limit_minor=150_000),  # ¥1,500 limit
    )
    txns = (
        TransactionSnapshot(
            id="rent-6",
            occurred_at=_dt(2026, 6, 1),
            type="expense",
            payee="房租",
            category_id="c-home",
            amount_minor=500_000,
            base_amount_minor=500_000,
        ),
        TransactionSnapshot(
            id="rent-7",
            occurred_at=_dt(2026, 7, 1),
            type="expense",
            payee="房租",
            category_id="c-home",
            amount_minor=500_000,
            base_amount_minor=500_000,
        ),
        # Paid August rent on Aug 1 (day 2 of 31 when as_of=Aug 2)
        TransactionSnapshot(
            id="rent-8",
            occurred_at=_dt(2026, 8, 1),
            type="expense",
            payee="房租",
            category_id="c-home",
            amount_minor=500_000,
            base_amount_minor=500_000,
        ),
        # Variable food spend: ¥400 in just 2 days -> linear EOM = ¥6,200 > ¥1,500 -> over_pace!
        TransactionSnapshot(
            id="food-8-1",
            occurred_at=_dt(2026, 8, 2),
            type="expense",
            payee="聚餐",
            category_id="c-food",
            amount_minor=40_000,
            base_amount_minor=40_000,
        ),
    )

    snapshot = LedgerSnapshot(
        as_of=date(2026, 8, 2),
        categories=categories,
        transactions=txns,
        budgets=budgets,
    )
    evals = {b.category_id: b for b in evaluate_category_budgets(snapshot)}

    home_eval = evals["c-home"]
    assert home_eval.paid_recurring_minor == 500_000
    assert home_eval.projected_end_of_month_minor == 500_000  # NOT 500_000 * 31 / 2!
    assert home_eval.status == "watch"  # 90.9% utilization >= 80% warning, but NOT over_pace!

    food_eval = evals["c-food"]
    assert food_eval.status == "over_pace"
    assert food_eval.projected_end_of_month_minor > food_eval.monthly_limit_minor


def test_three_scenario_cashflow_radar_and_what_if_life_simulator() -> None:
    accounts = (
        AccountSnapshot(
            id="a-main",
            name="家庭主账户",
            kind="asset",
            scope="family",
            currency="CNY",
            balance_minor=3_500_000,  # ¥35,000 opening cash
        ),
    )
    scheduled = (
        ScheduledCashflowItem(
            id="sch-tuition",
            label="秋季学费",
            direction="expense",
            amount_minor=1_200_000,  # ¥12,000 on Sep 1
            due_date=date(2026, 9, 1),
        ),
        ScheduledCashflowItem(
            id="sch-salary-sep",
            label="9月工资",
            direction="income",
            amount_minor=1_500_000,  # ¥15,000 on Sep 15
            due_date=date(2026, 9, 15),
        ),
    )
    txns = tuple(
        TransactionSnapshot(
            id=f"var-{d}",
            occurred_at=_dt(2026, 8, d),
            type="expense",
            payee="超市买菜",
            amount_minor=10_000,  # ¥100/day variable spend
            base_amount_minor=10_000,
        )
        for d in range(1, 21)
    )

    snapshot = LedgerSnapshot(
        as_of=date(2026, 8, 20),
        accounts=accounts,
        transactions=txns,
        scheduled_items=scheduled,
    )
    radar = forecast_cashflow(
        snapshot,
        horizon_days=60,
        safety_floor_minor=2_000_000,  # ¥20,000 emergency floor
    )

    assert (
        radar.stress.closing_cash_minor
        <= radar.conservative.closing_cash_minor
        <= radar.baseline.closing_cash_minor
    )
    assert "已确认安排：秋季学费" in radar.top_sensitivity_drivers

    # Life Simulator: compare departing on Sep 5 (before Sep 15 salary!) vs Oct 1 for a ¥20,000 trip
    options = (
        WhatIfPlanOption(
            option_id="trip-sep",
            title="9月初出发（学费后、发薪前）",
            adjustments=(
                WhatIfAdjustment(
                    label="家庭旅行",
                    direction="expense",
                    amount_minor=2_000_000,
                    effective_on=date(2026, 9, 5),
                ),
            ),
        ),
        WhatIfPlanOption(
            option_id="trip-oct-trim",
            title="10月出发 + 日常弹性开支压缩20%",
            adjustments=(
                WhatIfAdjustment(
                    label="家庭旅行",
                    direction="expense",
                    amount_minor=2_000_000,
                    effective_on=date(2026, 10, 1),
                ),
            ),
            variable_spend_delta_bps=-2_000,
        ),
    )

    outcomes = {
        o.option_id: o
        for o in compare_what_if_plans(
            snapshot,
            options,
            horizon_days=60,
            safety_floor_minor=1_500_000,  # ¥15,000 minimum cash floor
        )
    }
    # Sep 5 trip drops cash below ¥15,000 right after Sep 1 tuition (35k - 12k - 20k - var < 15k)
    assert outcomes["trip-sep"].feasible_under_floor is False
    assert outcomes["trip-sep"].max_shortfall_minor > 0
    # Oct 1 trip (after Sep 15 ¥15k salary + 20% spend trim) has higher min_cash and ending_cash
    assert outcomes["trip-oct-trim"].min_cash_minor > outcomes["trip-sep"].min_cash_minor
    assert outcomes["trip-oct-trim"].ending_cash_minor > outcomes["trip-sep"].ending_cash_minor
