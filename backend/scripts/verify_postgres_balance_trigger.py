from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import Connection, create_engine, text
from sqlalchemy.exc import IntegrityError

from homedatabase_api.config import get_settings


def _insert_transaction(connection: Connection, transaction_id: str) -> None:
    connection.execute(
        text(
            """
            INSERT INTO transactions (
                id, occurred_at, type, payee, note, currency, amount_minor,
                base_currency, base_amount_minor, source, created_by, version,
                created_at, updated_at
            ) VALUES (
                :id, CURRENT_TIMESTAMP, 'expense', 'CI balance trigger', '', 'CNY', 100,
                'CNY', 100, 'manual', 'm-trigger-ci', 1,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
            """
        ),
        {"id": transaction_id},
    )


def _insert_line(
    connection: Connection,
    *,
    line_id: str,
    transaction_id: str,
    debit: int,
    credit: int,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO entry_lines (
                id, transaction_id, account_id, ledger_code,
                debit_minor, credit_minor, account_amount_minor
            ) VALUES (
                :id, :transaction_id, NULL, :ledger_code,
                :debit, :credit, NULL
            )
            """
        ),
        {
            "id": line_id,
            "transaction_id": transaction_id,
            "ledger_code": f"ci:{line_id}",
            "debit": debit,
            "credit": credit,
        },
    )


def _expect_balance_violation(action: Callable[[], None]) -> None:
    try:
        action()
    except IntegrityError as exc:
        if getattr(exc.orig, "sqlstate", None) != "23514":
            raise
    else:
        raise AssertionError("PostgreSQL accepted an invalid journal transaction")


def main() -> None:
    settings = get_settings()
    if not settings.database_url.startswith("postgresql"):
        raise RuntimeError("this verification requires PostgreSQL")
    engine = create_engine(settings.database_url)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO members (
                    id, identity, name, username, password_hash, is_admin,
                    color, active, version, created_at
                ) VALUES (
                    'm-trigger-ci', 'son', 'CI', 'trigger-ci', 'not-a-login', FALSE,
                    'bg-slate-400', TRUE, 1, CURRENT_TIMESTAMP
                )
                ON CONFLICT (id) DO NOTHING
                """
            )
        )

    with engine.begin() as connection:
        _insert_transaction(connection, "txn-trigger-balanced")
        _insert_line(
            connection,
            line_id="line-trigger-debit",
            transaction_id="txn-trigger-balanced",
            debit=100,
            credit=0,
        )
        _insert_line(
            connection,
            line_id="line-trigger-credit",
            transaction_id="txn-trigger-balanced",
            debit=0,
            credit=100,
        )

    def insert_unbalanced() -> None:
        with engine.begin() as connection:
            _insert_transaction(connection, "txn-trigger-unbalanced")
            _insert_line(
                connection,
                line_id="line-unbalanced-debit",
                transaction_id="txn-trigger-unbalanced",
                debit=100,
                credit=0,
            )
            _insert_line(
                connection,
                line_id="line-unbalanced-credit",
                transaction_id="txn-trigger-unbalanced",
                debit=0,
                credit=99,
            )

    _expect_balance_violation(insert_unbalanced)

    def insert_single_line() -> None:
        with engine.begin() as connection:
            _insert_transaction(connection, "txn-trigger-single")
            _insert_line(
                connection,
                line_id="line-trigger-single",
                transaction_id="txn-trigger-single",
                debit=100,
                credit=0,
            )

    _expect_balance_violation(insert_single_line)

    def delete_one_line() -> None:
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM entry_lines WHERE id = 'line-trigger-debit'"))

    _expect_balance_violation(delete_one_line)

    with engine.begin() as connection:
        connection.execute(text("DELETE FROM transactions WHERE id = 'txn-trigger-balanced'"))

    engine.dispose()
    print("PostgreSQL deferred balance constraint verified")


if __name__ == "__main__":
    main()
