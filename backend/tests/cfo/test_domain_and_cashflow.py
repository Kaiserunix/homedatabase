from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from homedatabase_api.cfo import (
    MAX_SAFE_MINOR,
    AccountSnapshot,
    CategorySnapshot,
    CFODomainError,
    LedgerSnapshot,
    TransactionSnapshot,
    UnsafeMoneyError,
    YearMonth,
    compute_cashflow_metrics,
    compute_liquidity_summary,
    explain_spend_change,
    format_minor_units,
    ratio_to_bps,
    scale_minor_by_bps,
    to_shanghai_date,
)


def _dt_shanghai_utc(year: int, month: int, day: int, hour: int = 12) -> datetime:
    """Create UTC datetime corresponding to Asia/Shanghai (UTC+8) wall time."""
    iso_text = f"{year:04d}-{month:02d}-{day:02d}T{hour:02d}:00:00+08:00"
    return datetime.fromisoformat(iso_text).astimezone(UTC)


def test_money_invariants_and_formatting() -> None:
    assert format_minor_units(0) == "0.00"
    assert format_minor_units(12345) == "123.45"
    assert format_minor_units(-505) == "-5.05"
    assert ratio_to_bps(1, 3) == 3333
    assert ratio_to_bps(2, 3) == 6667
    assert ratio_to_bps(100, 0) is None
    assert scale_minor_by_bps(10_005, 5_000) == 5_003  # deterministic ROUND_HALF_UP

    with pytest.raises(UnsafeMoneyError):
        format_minor_units(MAX_SAFE_MINOR + 1)

    with pytest.raises(UnsafeMoneyError):
        format_minor_units(True)  # type: ignore[arg-type]

    with pytest.raises(UnsafeMoneyError):
        TransactionSnapshot(
            id="t-bad",
            occurred_at=_dt_shanghai_utc(2026, 8, 1),
            type="expense",
            payee="Shop",
            amount_minor=0,
            base_amount_minor=100,
        )


def test_year_month_arithmetic_and_shanghai_boundary() -> None:
    ym = YearMonth.parse("2026-01")
    assert ym.add_months(-1) == YearMonth(2025, 12)
    assert ym.add_months(13) == YearMonth(2027, 2)
    assert YearMonth(2024, 2).days_in_month() == 29
    assert YearMonth(2026, 2).days_in_month() == 28

    # 2026-08-01 00:30+08:00 is 2026-07-31 16:30Z -> must bucket into August in Shanghai!
    just_after_midnight_cst = datetime(2026, 7, 31, 16, 30, tzinfo=UTC)
    assert to_shanghai_date(just_after_midnight_cst) == date(2026, 8, 1)
    assert YearMonth.from_datetime(just_after_midnight_cst) == YearMonth(2026, 8)

    with pytest.raises(CFODomainError):
        YearMonth.parse("2026/08")


def test_liquidity_isolates_liabilities_and_foreign_currencies() -> None:
    accounts = (
        AccountSnapshot(
            id="a-bank",
            name="家庭储蓄",
            kind="asset",
            scope="family",
            currency="CNY",
            balance_minor=2_000_000,
        ),
        AccountSnapshot(
            id="a-deposit",
            name="定期存单",
            kind="asset",
            scope="family",
            currency="CNY",
            balance_minor=5_000_000,
            included_in_liquid_cash=False,
        ),
        AccountSnapshot(
            id="a-card",
            name="家庭信用卡",
            kind="liability",
            scope="family",
            currency="CNY",
            balance_minor=500_000,  # 5,000.00 CNY owed
        ),
        AccountSnapshot(
            id="a-usd",
            name="美元账户",
            kind="asset",
            scope="personal",
            currency="USD",
            balance_minor=100_000,  # $1,000.00 must NOT be added to CNY cents
        ),
    )

    summary = compute_liquidity_summary(accounts, base_currency="CNY")
    assert summary.liquid_assets_minor == 2_000_000
    assert summary.non_liquid_assets_minor == 5_000_000
    assert summary.total_assets_minor == 7_000_000
    assert summary.total_liabilities_minor == 500_000
    assert summary.net_worth_minor == 6_500_000
    assert summary.debt_to_liquid_cash_bps == 2_500  # 25.00%
    assert summary.unconverted_balances_by_currency == (("USD", 100_000),)


def test_cashflow_metrics_transfers_refunds_splits_and_runway() -> None:
    categories = (
        CategorySnapshot(id="c-salary", name="工资", kind="income"),
        CategorySnapshot(id="c-food", name="餐饮", kind="expense"),
        CategorySnapshot(id="c-home", name="居家", kind="expense"),
    )
    accounts = (
        AccountSnapshot(
            id="a-family",
            name="家庭公共账户",
            kind="asset",
            scope="family",
            currency="CNY",
            balance_minor=6_000_000,  # ¥60,000 liquid cash
        ),
    )
    transactions = (
        # July: ¥20,000 income, ¥10,000 expense -> ¥10,000 burn
        TransactionSnapshot(
            id="t-jul-inc",
            occurred_at=_dt_shanghai_utc(2026, 7, 10),
            type="income",
            payee="公司代发工资",
            category_id="c-salary",
            amount_minor=2_000_000,
            base_amount_minor=2_000_000,
        ),
        TransactionSnapshot(
            id="t-jul-exp",
            occurred_at=_dt_shanghai_utc(2026, 7, 15),
            type="expense",
            payee="物业与房租",
            category_id="c-home",
            amount_minor=1_000_000,
            base_amount_minor=1_000_000,
        ),
        # August: ¥20,000 income, ¥8,000 gross expense, ¥2,000 refund, ¥5,000 transfer, ¥1,000 split
        TransactionSnapshot(
            id="t-aug-inc",
            occurred_at=_dt_shanghai_utc(2026, 8, 10),
            type="income",
            payee="公司代发工资",
            category_id="c-salary",
            amount_minor=2_000_000,
            base_amount_minor=2_000_000,
        ),
        TransactionSnapshot(
            id="t-aug-exp-1",
            occurred_at=_dt_shanghai_utc(2026, 8, 12),
            type="expense",
            payee="家具采购",
            category_id="c-home",
            amount_minor=600_000,
            base_amount_minor=600_000,
            split_reimbursed_minor=100_000,
        ),
        TransactionSnapshot(
            id="t-aug-ref-1",
            occurred_at=_dt_shanghai_utc(2026, 8, 14),
            type="refund",
            payee="家具退差价",
            category_id="c-home",
            amount_minor=200_000,
            base_amount_minor=200_000,
            refund_of_id="t-aug-exp-1",
        ),
        TransactionSnapshot(
            id="t-aug-uncat",
            occurred_at=_dt_shanghai_utc(2026, 8, 18),
            type="expense",
            payee="便利店",
            category_id=None,
            amount_minor=200_000,
            base_amount_minor=200_000,
        ),
        TransactionSnapshot(
            id="t-aug-xfer",
            occurred_at=_dt_shanghai_utc(2026, 8, 20),
            type="transfer",
            payee="内部转存",
            amount_minor=500_000,
            base_amount_minor=500_000,
        ),
    )

    snapshot = LedgerSnapshot(
        as_of=date(2026, 8, 31),
        accounts=accounts,
        categories=categories,
        transactions=transactions,
    )
    metrics = compute_cashflow_metrics(snapshot, focus_month=YearMonth(2026, 8))

    pt = metrics.focus_month_point
    assert pt.income_minor == 2_000_000
    assert pt.gross_expense_minor == 800_000
    assert pt.refund_minor == 200_000
    assert pt.net_expense_minor == 600_000  # transfer excluded, refund deducted
    assert pt.effective_member_expense_minor == 500_000  # split reimbursed deducted
    assert pt.transfer_volume_minor == 500_000
    assert pt.net_savings_minor == 1_400_000
    assert pt.savings_rate_bps == 7_000  # 70.00%

    # Prior completed month (July) burn is ¥10,000 -> ¥60,000 cash = 6.00 months (60,000 bps)
    assert metrics.rolling_monthly_burn_minor == 1_000_000
    assert metrics.emergency_runway_months_bps == 60_000
    assert metrics.uncategorized_expense_minor == 200_000
    assert metrics.uncategorized_share_bps == 3_333


def test_explain_spend_change_decomposes_frequency_vs_ticket_size() -> None:
    categories = (
        CategorySnapshot(id="c-takeout", name="外卖餐饮", kind="expense"),
        CategorySnapshot(id="c-travel", name="交通出行", kind="expense"),
        CategorySnapshot(id="c-club", name="健身会员", kind="expense"),
    )
    txns: list[TransactionSnapshot] = []
    # July takeout: 4 orders * ¥30.00 = ¥120.00
    for idx in range(4):
        txns.append(
            TransactionSnapshot(
                id=f"jul-food-{idx}",
                occurred_at=_dt_shanghai_utc(2026, 7, 2 + idx),
                type="expense",
                payee="美团外卖",
                category_id="c-takeout",
                amount_minor=3_000,
                base_amount_minor=3_000,
            )
        )
    # August takeout: 16 orders * ¥30.00 = ¥480.00 (pure frequency increase!)
    for idx in range(16):
        txns.append(
            TransactionSnapshot(
                id=f"aug-food-{idx}",
                occurred_at=_dt_shanghai_utc(2026, 8, 2 + idx),
                type="expense",
                payee="美团外卖",
                category_id="c-takeout",
                amount_minor=3_000,
                base_amount_minor=3_000,
            )
        )
    # July travel: 2 rides * ¥20 = ¥40; August travel: 2 flights * ¥800 = ¥1,600 (ticket size!)
    for idx in range(2):
        txns.append(
            TransactionSnapshot(
                id=f"jul-trv-{idx}",
                occurred_at=_dt_shanghai_utc(2026, 7, 10 + idx),
                type="expense",
                payee="出行",
                category_id="c-travel",
                amount_minor=2_000,
                base_amount_minor=2_000,
            )
        )
        txns.append(
            TransactionSnapshot(
                id=f"aug-trv-{idx}",
                occurred_at=_dt_shanghai_utc(2026, 8, 10 + idx),
                type="expense",
                payee="高铁机票",
                category_id="c-travel",
                amount_minor=80_000,
                base_amount_minor=80_000,
            )
        )
    # New spend in August
    txns.append(
        TransactionSnapshot(
            id="aug-new-1",
            occurred_at=_dt_shanghai_utc(2026, 8, 25),
            type="expense",
            payee="攀岩馆",
            category_id="c-club",
            amount_minor=20_000,
            base_amount_minor=20_000,
        )
    )

    snapshot = LedgerSnapshot(
        as_of=date(2026, 8, 31),
        categories=categories,
        transactions=tuple(txns),
    )
    attribution = explain_spend_change(
        snapshot,
        baseline_months=(YearMonth(2026, 7),),
        current_months=(YearMonth(2026, 8),),
    )

    by_key = {drv.key: drv for drv in attribution.top_increases}
    food = by_key["c-takeout"]
    assert food.delta_net_minor == 36_000
    assert food.driver_kind == "frequency_driven"
    assert food.volume_effect_minor + food.price_effect_minor == food.delta_net_minor
    assert "aug-food-0" in food.evidence_txn_ids

    travel = by_key["c-travel"]
    assert travel.delta_net_minor == 156_000
    assert travel.driver_kind == "ticket_size_driven"
    assert travel.volume_effect_minor + travel.price_effect_minor == travel.delta_net_minor

    club = by_key["c-club"]
    assert club.driver_kind == "new_spend"
