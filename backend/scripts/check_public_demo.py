"""Validate the shipped fictional database, including credential and accounting invariants."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

DEMO = Path(__file__).resolve().parents[2] / "examples/demo.sqlite3"
EMPTY_TABLES = (
    "login_sessions",
    "passkeys",
    "webauthn_challenges",
    "source_files",
    "source_records",
    "import_batches",
    "import_candidates",
    "backup_records",
    "audit_events",
    "period_locks",
    "splits",
    "split_shares",
)


def check(path: Path = DEMO) -> None:
    with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as db:
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0004_category_name_uniqueness",
        )
        assert db.execute("SELECT COUNT(*) FROM members").fetchone() == (3,)
        assert db.execute("SELECT COUNT(*) FROM accounts").fetchone() == (6,)
        assert db.execute("SELECT COUNT(*) FROM categories").fetchone() == (12,)
        assert db.execute("SELECT COUNT(*) FROM transactions").fetchone() == (6,)
        assert db.execute("SELECT COUNT(*) FROM entry_lines").fetchone() == (12,)
        assert db.execute(
            "SELECT username, password_hash FROM members ORDER BY username"
        ).fetchall() == [
            ("dad", ""),
            ("mom", ""),
            ("son", ""),
        ]
        for table in EMPTY_TABLES:
            assert db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone() == (0,), table
        assert db.execute(
            "SELECT payee, amount_minor FROM transactions ORDER BY occurred_at"
        ).fetchall() == [
            ("示例公司工资", 600000),
            ("示例餐厅", 2850),
            ("示例超市", 15680),
            ("示例公司工资", 600000),
            ("示例餐厅", 3200),
            ("示例公交", 400),
        ]
        assert not db.execute(
            "SELECT transaction_id FROM entry_lines GROUP BY transaction_id "
            "HAVING SUM(debit_minor) != SUM(credit_minor)"
        ).fetchall()
    assert b"scrypt$" not in path.read_bytes(), "Password hash remains in SQLite pages"
    print(
        "Public demo: integrity, foreign keys, fictional rows, "
        "empty credentials, balanced entries OK"
    )


if __name__ == "__main__":
    check()
