"""Zero-coupling structural adapters from HomeDatabase ledger records to pure CFO snapshots.

Uses ``typing.Protocol`` duck typing so callers can pass SQLAlchemy ORM models,
lightweight read models, or test doubles without importing FastAPI or SQLAlchemy here.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from typing import Protocol

from .domain import (
    AccountSnapshot,
    CategoryBudgetTarget,
    CategorySnapshot,
    LedgerSnapshot,
    ScheduledCashflowItem,
    TransactionSnapshot,
    UnbalancedLedgerError,
    require_int_minor,
)

REDACTED_COUNTERPARTY_PAYEE = "私人账户往来（已脱敏）"


class AccountRecordLike(Protocol):
    id: str
    name: str
    kind: str
    scope: str
    owner_id: str | None
    currency: str
    opening_balance_minor: int
    deleted_at: datetime | None


class AccountGrantLike(Protocol):
    account_id: str
    member_id: str
    permission: str


class CategoryRecordLike(Protocol):
    id: str
    name: str
    kind: str
    parent_id: str | None


class EntryLineRecordLike(Protocol):
    account_id: str | None
    ledger_code: str | None
    debit_minor: int
    credit_minor: int
    account_amount_minor: int | None


class TransactionRecordLike(Protocol):
    id: str
    occurred_at: datetime
    type: str
    payee: str
    note: str
    category_id: str | None
    currency: str
    amount_minor: int
    base_currency: str
    base_amount_minor: int
    refund_of_id: str | None
    deleted_at: datetime | None
    lines: Sequence[EntryLineRecordLike]


class SplitShareRecordLike(Protocol):
    member_id: str
    share_minor: int
    status: str


class SplitRecordLike(Protocol):
    transaction_id: str
    created_by: str
    status: str
    shares: Sequence[SplitShareRecordLike]


def _can_member_view_account(
    account: AccountRecordLike,
    viewer_member_id: str | None,
    granted_account_ids: set[str],
) -> bool:
    if account.deleted_at is not None:
        return False
    if viewer_member_id is None:
        return True
    if account.owner_id == viewer_member_id:
        return True
    if account.id in granted_account_ids:
        return True
    return account.scope == "family"


def build_snapshot_from_ledger_records(
    *,
    as_of: date,
    accounts: Iterable[AccountRecordLike],
    categories: Iterable[CategoryRecordLike],
    transactions: Iterable[TransactionRecordLike],
    grants: Iterable[AccountGrantLike] = (),
    splits: Iterable[SplitRecordLike] = (),
    budgets: Iterable[CategoryBudgetTarget] = (),
    scheduled_items: Iterable[ScheduledCashflowItem] = (),
    viewer_member_id: str | None = None,
    base_currency: str = "CNY",
    scope_label: str | None = None,
    non_liquid_account_ids: frozenset[str] = frozenset(),
) -> LedgerSnapshot:
    """Build an authorized, balanced ``LedgerSnapshot`` from HomeDatabase ledger entities.

    Enforces:
    1. Soft-deleted accounts and transactions are excluded.
    2. Every active transaction must satisfy ``sum(debit_minor) == sum(credit_minor)``.
    3. Account balances follow ``services/ledger.py`` exact native-currency movement rules.
    4. Member permission isolation matches ``services/permissions.py`` and ``services/reports.py``:
       private accounts without a grant are invisible, and half-private transfers redact
       payee/note/refund linkage so private counterparty metadata never leaks.
    """
    account_list = [acct for acct in accounts if acct.deleted_at is None]
    account_by_id = {acct.id: acct for acct in account_list}

    granted_ids = {
        grant.account_id
        for grant in grants
        if viewer_member_id is not None and grant.member_id == viewer_member_id
    }
    visible_account_ids = {
        acct.id
        for acct in account_list
        if _can_member_view_account(acct, viewer_member_id, granted_ids)
    }

    active_txns = [txn for txn in transactions if txn.deleted_at is None]

    # Verify double-entry balance and accumulate native account movements up to all active entries
    native_movements: dict[str, int] = defaultdict(int)
    for txn in active_txns:
        debit_sum = sum(line.debit_minor for line in txn.lines)
        credit_sum = sum(line.credit_minor for line in txn.lines)
        if debit_sum != credit_sum:
            raise UnbalancedLedgerError(
                f"transaction {txn.id} is unbalanced: debit={debit_sum}, credit={credit_sum}"
            )
        for line in txn.lines:
            if line.account_id is not None and line.account_amount_minor is not None:
                delta = (
                    line.account_amount_minor
                    if line.debit_minor > 0
                    else -line.account_amount_minor
                )
                native_movements[line.account_id] += delta

    # Map splits by transaction_id to compute reimbursed shares
    split_by_txn: dict[str, SplitRecordLike] = {
        sp.transaction_id: sp for sp in splits if sp.status != "returned"
    }

    account_snapshots: list[AccountSnapshot] = []
    for acct in sorted(account_list, key=lambda a: a.id):
        if acct.id not in visible_account_ids:
            continue
        movement = native_movements.get(acct.id, 0)
        if acct.kind == "liability":
            movement = -movement
        balance = require_int_minor(
            acct.opening_balance_minor + movement,
            field_name=f"account[{acct.id}].balance_minor",
        )
        account_snapshots.append(
            AccountSnapshot(
                id=acct.id,
                name=acct.name,
                kind="asset" if acct.kind == "asset" else "liability",
                scope="personal" if acct.scope == "personal" else "family",
                currency=acct.currency,
                balance_minor=balance,
                included_in_liquid_cash=acct.id not in non_liquid_account_ids,
            )
        )

    category_snapshots = tuple(
        CategorySnapshot(
            id=cat.id,
            name=cat.name,
            kind="income" if cat.kind == "income" else "expense",
            parent_id=cat.parent_id,
        )
        for cat in categories
    )

    txn_snapshots: list[TransactionSnapshot] = []
    for txn in sorted(active_txns, key=lambda t: (t.occurred_at, t.id)):
        touched_acct_ids = [line.account_id for line in txn.lines if line.account_id is not None]
        visible_touched = [aid for aid in touched_acct_ids if aid in visible_account_ids]
        if not visible_touched:
            continue

        has_hidden = any(
            aid not in visible_account_ids or aid not in account_by_id for aid in touched_acct_ids
        )
        display_payee = REDACTED_COUNTERPARTY_PAYEE if has_hidden else txn.payee
        display_note = "" if has_hidden else txn.note
        refund_link = None if has_hidden else txn.refund_of_id

        reimbursed_base_minor = 0
        split = split_by_txn.get(txn.id)
        if split is not None and txn.type == "expense" and not has_hidden:
            if viewer_member_id is not None:
                # Reimbursed portion = shares borne by OTHER family members (non-returned)
                other_native = sum(
                    sh.share_minor
                    for sh in split.shares
                    if sh.member_id != viewer_member_id and sh.status != "returned"
                )
            else:
                other_native = 0
            if other_native > 0 and txn.amount_minor > 0:
                if txn.currency == txn.base_currency:
                    reimbursed_base_minor = min(txn.base_amount_minor, other_native)
                else:
                    reimbursed_base_minor = min(
                        txn.base_amount_minor,
                        round(other_native * txn.base_amount_minor / txn.amount_minor),
                    )

        txn_snapshots.append(
            TransactionSnapshot(
                id=txn.id,
                occurred_at=txn.occurred_at,
                type=txn.type,  # type: ignore[arg-type]
                payee=display_payee,
                note=display_note,
                category_id=txn.category_id,
                currency=txn.currency,
                amount_minor=txn.amount_minor,
                base_currency=txn.base_currency,
                base_amount_minor=txn.base_amount_minor,
                account_ids=tuple(visible_touched),
                refund_of_id=refund_link,
                has_hidden_counterparty=has_hidden,
                split_reimbursed_minor=reimbursed_base_minor,
            )
        )

    effective_scope = scope_label or (
        f"member:{viewer_member_id}" if viewer_member_id else "household"
    )
    return LedgerSnapshot(
        as_of=as_of,
        base_currency=base_currency,
        accounts=tuple(account_snapshots),
        categories=category_snapshots,
        transactions=tuple(txn_snapshots),
        budgets=tuple(budgets),
        scheduled_items=tuple(scheduled_items),
        viewer_member_id=viewer_member_id,
        scope_label=effective_scope,
    )
