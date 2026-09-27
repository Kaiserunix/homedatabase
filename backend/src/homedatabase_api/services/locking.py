from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

# PostgreSQL advisory locks live in one database-wide signed bigint namespace.
# This stable key serialises the short critical sections that either mutate the
# authoritative ledger or change which ledger dates are frozen.  The app is a
# household ledger, so correctness is worth the negligible write contention.
_LEDGER_MUTATION_GUARD_KEY = 0x48444C4544474552  # "HDLEDGER"


def acquire_ledger_mutation_guard(db: Session) -> None:
    """Acquire the PostgreSQL transaction guard before taking any row lock.

    SQLite is only a development/test database and has no equivalent
    transaction-scoped advisory lock.  Production configuration already
    requires PostgreSQL.
    """

    if db.get_bind().dialect.name != "postgresql":
        return
    db.execute(
        text("SELECT pg_advisory_xact_lock(:guard_key)"),
        {"guard_key": _LEDGER_MUTATION_GUARD_KEY},
    )
