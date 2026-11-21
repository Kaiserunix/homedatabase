"""End-to-end integration tests bridging pluggable importers, pure CFO engine, and core API."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from conftest import as_member
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from homedatabase_api.cfo import CFOAnalyticalEngine, build_snapshot_from_ledger_records
from homedatabase_api.database import get_session_factory
from homedatabase_api.models import Account, AccountGrant, Category, Split, Transaction
from homedatabase_api.services.import_parsers import ParsedRow, ParseOutcome

_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


def test_pluggable_importers_legacy_adapter_returns_core_parse_outcome(tmp_path: Path):
    from importers.legacy_adapter import parse_import_file_pluggable

    csv_path = tmp_path / "wechat_bill.csv"
    csv_path.write_text(
        "\n".join(
            [
                "微信支付账单明细",
                (
                    "交易时间,交易类型,交易对方,商品,收/支,金额(元),"
                    "支付方式,当前状态,交易单号,商户单号,备注"
                ),
                (
                    "2026-08-15 12:30:00,商户消费,盒马鲜生,生鲜采购,支出,"
                    "¥128.50,零钱,支付成功,wx001,m001,"
                ),
                (
                    "2026-08-16 09:00:00,商户消费,工资发放,八月薪资,收入,"
                    "¥8000.00,零钱,已存入零钱,wx002,m002,"
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    outcome = parse_import_file_pluggable(
        csv_path,
        csv_path.name,
        max_rows=500,
        max_uncompressed_bytes=5_000_000,
    )
    assert isinstance(outcome, ParseOutcome)
    assert outcome.status == "parsed"
    assert outcome.candidate_count == 2
    assert all(isinstance(row, ParsedRow) for row in outcome.rows)
    amounts = sorted(row.amount_minor for row in outcome.rows if row.amount_minor is not None)
    assert amounts == [12850, 800000]


def test_pure_cfo_engine_analyzes_live_sqlalchemy_ledger_with_privacy_isolation(client):
    # Family expense visible to m-son
    resp_family = client.post(
        "/api/transactions",
        headers=as_member("m-son"),
        json={
            "type": "expense",
            "payee": "山姆会员店",
            "note": "家庭补货",
            "categoryId": "c-grocery",
            "accountId": "a-family",
            "amountCents": 45600,
            "currency": "CNY",
            "date": "2026-08-14T15:00:00+08:00",
        },
    )
    assert resp_family.status_code == 201, resp_family.text

    # Private expense by m-dad (invisible to m-son)
    resp_private = client.post(
        "/api/transactions",
        headers=as_member("m-dad"),
        json={
            "type": "expense",
            "payee": "私密渔具店",
            "note": "不公开",
            "categoryId": "c-fun",
            "accountId": "a-dad-bank",
            "amountCents": 99900,
            "currency": "CNY",
            "date": "2026-08-15T10:00:00+08:00",
        },
    )
    assert resp_private.status_code == 201, resp_private.text

    with get_session_factory()() as db:
        accounts = db.scalars(select(Account)).all()
        categories = db.scalars(select(Category)).all()
        grants = db.scalars(select(AccountGrant)).all()
        splits = db.scalars(select(Split).options(selectinload(Split.shares))).all()
        transactions = db.scalars(
            select(Transaction).options(selectinload(Transaction.lines))
        ).all()

        snapshot = build_snapshot_from_ledger_records(
            as_of=date(2026, 8, 31),
            accounts=accounts,
            categories=categories,
            transactions=transactions,
            grants=grants,
            splits=splits,
            viewer_member_id="m-son",
        )

    assert all(txn.payee != "私密渔具店" for txn in snapshot.transactions)
    assert any(
        txn.payee == "山姆会员店" and txn.base_amount_minor == 45600
        for txn in snapshot.transactions
    )

    report = CFOAnalyticalEngine().analyze(snapshot)
    assert report.metrics.focus_month_point.net_expense_minor >= 45600
    assert 0 <= report.scorecard.score <= 100
    assert resp_family.json()["id"] in report.all_referenced_txn_ids()
    assert resp_private.json()["id"] not in report.all_referenced_txn_ids()
