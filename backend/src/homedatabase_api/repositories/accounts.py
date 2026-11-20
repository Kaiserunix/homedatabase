from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ..models import Account, AccountGrant, EntryLine, Transaction
from ..money import require_safe_signed_minor


class AccountRepository:
    """Persistence and deterministic balance aggregation for household accounts."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def get_active_account(self, account_id: str) -> Account | None:
        account = self.db.get(Account, account_id)
        if account and account.deleted_at is None:
            return account
        return None

    def get_account_for_update(self, account_id: str) -> Account | None:
        return self.db.scalar(select(Account).where(Account.id == account_id).with_for_update())

    def lock_accounts_ordered(self, account_ids: Iterable[str]) -> list[Account]:
        ordered_ids = sorted(set(account_ids))
        if not ordered_ids:
            return []
        return list(
            self.db.scalars(
                select(Account)
                .where(Account.id.in_(ordered_ids))
                .order_by(Account.id)
                .with_for_update()
            ).all()
        )

    def list_active_accounts(self) -> list[Account]:
        return list(
            self.db.scalars(
                select(Account).where(Account.deleted_at.is_(None)).order_by(Account.name)
            ).all()
        )

    def list_grants_for_member(self, member_id: str) -> dict[str, str]:
        grants = self.db.scalars(
            select(AccountGrant).where(AccountGrant.member_id == member_id)
        ).all()
        return {grant.account_id: grant.permission for grant in grants}

    def list_grants_for_account(self, account_id: str) -> list[AccountGrant]:
        return list(
            self.db.scalars(
                select(AccountGrant)
                .where(AccountGrant.account_id == account_id)
                .order_by(AccountGrant.member_id)
            ).all()
        )

    def get_grant(self, account_id: str, member_id: str) -> AccountGrant | None:
        return self.db.get(AccountGrant, (account_id, member_id))

    def compute_account_balance(self, account: Account) -> int:
        signed_native_movement = self.db.scalar(
            select(
                func.coalesce(
                    func.sum(
                        case(
                            (EntryLine.debit_minor > 0, EntryLine.account_amount_minor),
                            else_=-EntryLine.account_amount_minor,
                        )
                    ),
                    0,
                )
            )
            .join(Transaction, Transaction.id == EntryLine.transaction_id)
            .where(
                EntryLine.account_id == account.id,
                Transaction.deleted_at.is_(None),
            )
        )
        movement = int(signed_native_movement or 0)
        if account.kind == "liability":
            movement = -movement
        return require_safe_signed_minor(
            account.opening_balance_minor + movement,
            detail=f"账户 {account.id} 的当前余额已超出安全范围",
        )

    def compute_balances_for_accounts(self, accounts: Sequence[Account]) -> dict[str, int]:
        if not accounts:
            return {}
        account_ids = [account.id for account in accounts]
        rows = self.db.execute(
            select(
                EntryLine.account_id,
                func.coalesce(
                    func.sum(
                        case(
                            (EntryLine.debit_minor > 0, EntryLine.account_amount_minor),
                            else_=-EntryLine.account_amount_minor,
                        )
                    ),
                    0,
                ),
            )
            .join(Transaction, Transaction.id == EntryLine.transaction_id)
            .where(
                EntryLine.account_id.in_(account_ids),
                Transaction.deleted_at.is_(None),
            )
            .group_by(EntryLine.account_id)
        ).all()
        movements = {str(account_id): int(total or 0) for account_id, total in rows}
        balances: dict[str, int] = {}
        for account in accounts:
            movement = movements.get(account.id, 0)
            if account.kind == "liability":
                movement = -movement
            balances[account.id] = require_safe_signed_minor(
                account.opening_balance_minor + movement,
                detail=f"账户 {account.id} 的当前余额已超出安全范围",
            )
        return balances

    def compute_opening_balance_at(self, account: Account, starts_at: datetime) -> int:
        signed_native_movement = self.db.scalar(
            select(
                func.coalesce(
                    func.sum(
                        case(
                            (EntryLine.debit_minor > 0, EntryLine.account_amount_minor),
                            else_=-EntryLine.account_amount_minor,
                        )
                    ),
                    0,
                )
            )
            .join(Transaction, Transaction.id == EntryLine.transaction_id)
            .where(
                EntryLine.account_id == account.id,
                Transaction.deleted_at.is_(None),
                Transaction.occurred_at < starts_at,
            )
        )
        movement = int(signed_native_movement or 0)
        if account.kind == "liability":
            movement = -movement
        return account.opening_balance_minor + movement
