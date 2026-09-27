"""Build a new fictional database without ever opening an existing household database."""

from __future__ import annotations

import argparse
import os
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "src"))


def build(output: Path) -> None:
    output = output.resolve()
    if output.exists():
        raise SystemExit("Refusing to overwrite an existing database")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="homedatabase-fictional-") as scratch:
        database = Path(scratch) / "demo.sqlite3"
        # Explicit settings override any developer's local .env or inherited DB URL.
        os.environ.update(
            HOMEDATABASE_ENVIRONMENT="test",
            HOMEDATABASE_DATABASE_URL=f"sqlite+pysqlite:///{database.as_posix()}",
            HOMEDATABASE_DEV_MEMBER_ID="",
            HOMEDATABASE_AUTO_CREATE_SCHEMA="false",
            HOMEDATABASE_SEED_DEMO="false",
            HOMEDATABASE_DATABASE_ECHO="false",
            HOMEDATABASE_TASK_ALWAYS_EAGER="true",
        )
        subprocess.run(
            [
                sys.executable,
                "-m",
                "alembic",
                "-c",
                str(BACKEND / "alembic.ini"),
                "upgrade",
                "head",
            ],
            cwd=BACKEND,
            check=True,
        )
        from homedatabase_api.database import clear_database_caches, get_session_factory
        from homedatabase_api.models import Member
        from homedatabase_api.schemas import NewTransactionInput
        from homedatabase_api.seed import MEMBERS, seed_family_data
        from homedatabase_api.services.ledger import create_transaction

        with get_session_factory()() as db:
            seed_family_data(
                db, passwords={identity: secrets.token_urlsafe(24) for _, identity, *_ in MEMBERS}
            )
            member = db.get(Member, "m-son")
            rows = [
                ("2026-08-01T09:00:00+08:00", "income", "示例公司工资", 600000, "c-salary"),
                ("2026-08-02T12:00:00+08:00", "expense", "示例餐厅", 2850, "c-food"),
                ("2026-08-03T18:00:00+08:00", "expense", "示例超市", 15680, "c-grocery"),
                ("2026-09-01T09:00:00+08:00", "income", "示例公司工资", 600000, "c-salary"),
                ("2026-09-02T12:00:00+08:00", "expense", "示例餐厅", 3200, "c-food"),
                ("2026-09-03T08:30:00+08:00", "expense", "示例公交", 400, "c-transport"),
            ]
            for occurred, kind, payee, amount, category in rows:
                create_transaction(
                    db,
                    member,
                    NewTransactionInput.model_validate(
                        {
                            "date": occurred,
                            "type": kind,
                            "payee": payee,
                            "amountCents": amount,
                            "categoryId": category,
                            "accountId": "a-family",
                            "currency": "CNY",
                            "note": "完全虚构的演示记录，不对应任何真实交易。",
                        }
                    ),
                )
        clear_database_caches()
        with closing(sqlite3.connect(database)) as connection:
            connection.execute("UPDATE members SET password_hash = ''")
            connection.execute("DELETE FROM audit_events")
            assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            assert not connection.execute("PRAGMA foreign_key_check").fetchall()
            connection.commit()
            # Remove deleted password hashes and audit data from free pages.
            connection.execute("VACUUM")
        with output.open("xb") as target, database.open("rb") as source:
            shutil.copyfileobj(source, target)
    print(f"Created fictional database: {output.name} (6 transactions; passwords disabled)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=BACKEND.parent / "examples/demo.sqlite3")
    build(parser.parse_args().output)
