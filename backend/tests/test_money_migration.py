from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from homedatabase_api.config import get_settings
from homedatabase_api.money import MAX_SAFE_MINOR


def test_sqlite_upgrade_adds_safe_minor_checks(tmp_path, monkeypatch):
    backend_root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "migration.sqlite3"
    config = Config(str(backend_root / "alembic.ini"))
    database_url = f"sqlite+pysqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("HOMEDATABASE_DATABASE_URL", database_url)
    get_settings.cache_clear()

    try:
        command.upgrade(config, "head")
    finally:
        get_settings.cache_clear()

    engine = create_engine(database_url)
    names = {
        constraint["name"]
        for table in (
            "accounts",
            "transactions",
            "entry_lines",
            "import_candidates",
            "split_shares",
        )
        for constraint in inspect(engine).get_check_constraints(table)
    }
    assert {
        "ck_accounts_opening_balance_safe",
        "ck_transactions_amount_safe",
        "ck_transactions_base_amount_safe",
        "ck_entry_lines_amounts_safe",
        "ck_entry_lines_account_amount_safe",
        "ck_import_candidates_amount_safe",
        "ck_import_candidates_base_amount_safe",
        "ck_split_shares_amount_safe",
    } <= names

    with engine.begin() as connection, pytest.raises(IntegrityError):
        connection.execute(
            text(
                """
                INSERT INTO accounts (
                    id, name, kind, scope, owner_id, currency,
                    opening_balance_minor, icon, version, created_at
                ) VALUES (
                    'unsafe', 'unsafe', 'asset', 'family', NULL, 'CNY',
                    :amount, 'WalletCards', 1, CURRENT_TIMESTAMP
                )
                """
            ),
            {"amount": MAX_SAFE_MINOR + 1},
        )

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO categories (id, name, kind, parent_id, icon, active, version)
                VALUES ('root-one', '迁移根分类', 'expense', NULL, 'Shapes', 1, 1)
                """
            )
        )
    with engine.begin() as connection, pytest.raises(IntegrityError):
        connection.execute(
            text(
                """
                INSERT INTO categories (id, name, kind, parent_id, icon, active, version)
                VALUES ('root-two', '迁移根分类', 'expense', NULL, 'Shapes', 1, 1)
                """
            )
        )
