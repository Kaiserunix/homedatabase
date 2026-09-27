from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Account, AccountGrant, Member, Transaction


def account_permission(db: Session, member: Member, account: Account) -> str | None:
    if account.deleted_at is not None:
        return None
    if account.owner_id == member.id:
        return "manage"
    grant = db.scalar(
        select(AccountGrant).where(
            AccountGrant.account_id == account.id,
            AccountGrant.member_id == member.id,
        )
    )
    if grant:
        return grant.permission
    if account.scope == "family":
        return "view"
    return None


def require_account_permission(
    db: Session, member: Member, account_id: str, *, manage: bool = False
) -> Account:
    account = db.get(Account, account_id)
    if not account or account.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="账户不存在")
    permission = account_permission(db, member, account)
    if permission is None or (manage and permission != "manage"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权操作此账户")
    return account


def transaction_account_ids(transaction: Transaction) -> list[str]:
    return [line.account_id for line in transaction.lines if line.account_id is not None]


def visible_transaction_account_ids(
    db: Session, member: Member, transaction: Transaction
) -> set[str]:
    visible: set[str] = set()
    for account_id in transaction_account_ids(transaction):
        account = db.get(Account, account_id)
        if account and account_permission(db, member, account) is not None:
            visible.add(account_id)
    return visible


def transaction_has_hidden_accounts(db: Session, member: Member, transaction: Transaction) -> bool:
    account_ids = set(transaction_account_ids(transaction))
    return bool(account_ids - visible_transaction_account_ids(db, member, transaction))


def can_view_transaction(db: Session, member: Member, transaction: Transaction) -> bool:
    # A member must be able to reconcile every account they can see.  If one
    # side of a transfer is private, callers expose a balanced redacted
    # counter-line rather than the private account identity or native balance.
    return bool(visible_transaction_account_ids(db, member, transaction))


def can_manage_transaction(db: Session, member: Member, transaction: Transaction) -> bool:
    ids = transaction_account_ids(transaction)
    if not ids:
        return False
    for account_id in ids:
        account = db.get(Account, account_id)
        if not account or account_permission(db, member, account) != "manage":
            return False
    return True
