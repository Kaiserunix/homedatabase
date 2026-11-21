"""Read-only ledger repositories enforcing HomeDatabase member permissions and redaction."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..config import get_settings
from ..models import Account, Category, Member, Split, Transaction
from ..services.permissions import (
    account_permission,
    can_view_transaction,
    require_account_permission,
    transaction_has_hidden_accounts,
)
from .protocols import (
    CatalogContext,
    CategoryInfo,
    VisibleAccountInfo,
    VisibleTransactionRecord,
)
from .schemas import ScopeFilter

SHANGHAI = ZoneInfo("Asia/Shanghai")
REDACTED_PAYEE = "私人账户往来（已脱敏）"
REDACTED_ACCOUNT_ID = "private:counterparty"
REDACTED_ACCOUNT_NAME = "私人账户（已脱敏）"


@contextmanager
def _readonly_snapshot(db: Session, member_id: str):
    """Run PostgreSQL NLQ reads inside one READ ONLY REPEATABLE READ snapshot."""
    bind = db.get_bind()
    if bind is None or bind.dialect.name != "postgresql":
        member = db.get(Member, member_id)
        if member is None or not member.active:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="家庭成员不存在")
        yield db, member
        return

    with bind.engine.connect() as raw_connection:
        connection = raw_connection.execution_options(
            isolation_level="REPEATABLE READ",
            postgresql_readonly=True,
        )
        with Session(bind=connection, autoflush=False, expire_on_commit=False) as read_db:
            member = read_db.get(Member, member_id)
            if member is None or not member.active:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="家庭成员不存在")
            yield read_db, member


def _to_utc_bounds(date_from: date, date_to: date) -> tuple[datetime, datetime]:
    start = datetime.combine(date_from, time.min, SHANGHAI).astimezone(UTC)
    end = datetime.combine(date_to, time.max, SHANGHAI).astimezone(UTC)
    return start, end


class SqlAlchemyLedgerReadRepository:
    """Strictly read-only SQLAlchemy implementation of LedgerReadRepository."""

    def __init__(self, db: Session):
        self._db = db

    def load_catalog_context(self, member_id: str) -> CatalogContext:
        with _readonly_snapshot(self._db, member_id) as (db, member):
            raw_accounts = db.scalars(
                select(Account).where(Account.deleted_at.is_(None)).order_by(Account.name)
            ).all()
            visible_accounts: list[VisibleAccountInfo] = []
            for acct in raw_accounts:
                perm = account_permission(db, member, acct)
                if perm is None:
                    continue
                visible_accounts.append(
                    VisibleAccountInfo(
                        id=acct.id,
                        name=acct.name,
                        kind=acct.kind,
                        scope=acct.scope,
                        currency=acct.currency,
                        permission=perm,
                    )
                )

            raw_categories = db.scalars(
                select(Category).order_by(Category.kind, Category.parent_id, Category.name)
            ).all()
            categories = tuple(
                CategoryInfo(
                    id=cat.id,
                    name=cat.name,
                    kind=cat.kind,
                    parent_id=cat.parent_id,
                    active=cat.active,
                )
                for cat in raw_categories
            )
            return CatalogContext(
                member_id=member.id,
                member_name=member.name,
                base_currency=get_settings().base_currency,
                accounts=tuple(visible_accounts),
                categories=categories,
            )

    def query_visible_transactions(
        self,
        member_id: str,
        *,
        date_from: date,
        date_to: date,
        types: tuple[str, ...] = (),
        category_ids: tuple[str, ...] = (),
        account_ids: tuple[str, ...] = (),
        scope: ScopeFilter = "all_visible",
        keyword: str | None = None,
        min_amount_minor: int | None = None,
        max_amount_minor: int | None = None,
        only_with_splits: bool = False,
        only_refunded: bool = False,
        limit: int = 500,
    ) -> list[VisibleTransactionRecord]:
        with _readonly_snapshot(self._db, member_id) as (db, member):
            # Defense in depth: verify every explicitly requested account is authorized.
            for acct_id in account_ids:
                require_account_permission(db, member, acct_id)

            start_utc, end_utc = _to_utc_bounds(date_from, date_to)
            stmt = (
                select(Transaction)
                .where(
                    Transaction.deleted_at.is_(None),
                    Transaction.occurred_at >= start_utc,
                    Transaction.occurred_at <= end_utc,
                )
                .options(selectinload(Transaction.lines))
                .order_by(Transaction.occurred_at.desc(), Transaction.id.desc())
            )
            if types:
                stmt = stmt.where(Transaction.type.in_(types))
            if category_ids:
                stmt = stmt.where(Transaction.category_id.in_(category_ids))
            if min_amount_minor is not None:
                stmt = stmt.where(Transaction.base_amount_minor >= min_amount_minor)
            if max_amount_minor is not None:
                stmt = stmt.where(Transaction.base_amount_minor <= max_amount_minor)

            rows = db.scalars(stmt).all()
            if not rows:
                return []

            categories = {cat.id: cat.name for cat in db.scalars(select(Category)).all()}
            accounts = {acct.id: acct for acct in db.scalars(select(Account)).all()}

            txn_ids = [row.id for row in rows]
            splits_by_txn: dict[str, Split] = {
                split.transaction_id: split
                for split in db.scalars(
                    select(Split)
                    .where(Split.transaction_id.in_(txn_ids))
                    .options(selectinload(Split.shares))
                ).all()
            }

            refunded_originals: set[str] = set()
            if only_refunded:
                refunded_originals = set(
                    db.scalars(
                        select(Transaction.refund_of_id).where(
                            Transaction.refund_of_id.in_(txn_ids),
                            Transaction.type == "refund",
                            Transaction.deleted_at.is_(None),
                        )
                    ).all()
                )

            requested_accounts = set(account_ids)
            needle = keyword.casefold() if keyword else None
            results: list[VisibleTransactionRecord] = []

            for txn in rows:
                if not can_view_transaction(db, member, txn):
                    continue

                partial = transaction_has_hidden_accounts(db, member, txn)
                line_account_ids = [line.account_id for line in txn.lines if line.account_id]
                if requested_accounts and not (requested_accounts & set(line_account_ids)):
                    continue

                visible_acct_ids: list[str] = []
                visible_acct_names: list[str] = []
                visible_acct_scopes: list[str] = []
                for acct_id in line_account_ids:
                    acct = accounts.get(acct_id)
                    if acct is not None and account_permission(db, member, acct) is not None:
                        visible_acct_ids.append(acct.id)
                        visible_acct_names.append(acct.name)
                        visible_acct_scopes.append(acct.scope)
                    else:
                        visible_acct_ids.append(REDACTED_ACCOUNT_ID)
                        visible_acct_names.append(REDACTED_ACCOUNT_NAME)
                        visible_acct_scopes.append("private_redacted")

                if scope == "family_only":
                    if (
                        partial
                        or not visible_acct_scopes
                        or any(s != "family" for s in visible_acct_scopes)
                    ):
                        continue
                elif scope == "personal_only":
                    if partial or "personal" not in visible_acct_scopes:
                        continue

                display_payee = REDACTED_PAYEE if partial else txn.payee
                display_note = "" if partial else txn.note
                if needle is not None:
                    if (
                        needle not in display_payee.casefold()
                        and needle not in display_note.casefold()
                    ):
                        continue

                split = splits_by_txn.get(txn.id)
                if only_with_splits and split is None:
                    continue
                if only_refunded and txn.type != "refund" and txn.id not in refunded_originals:
                    continue

                member_share_minor: int | None = None
                member_share_status: str | None = None
                if split is not None:
                    for share in split.shares:
                        if share.member_id == member.id:
                            member_share_minor = share.share_minor
                            member_share_status = share.status
                            break

                cat_name = (
                    "—"
                    if txn.type == "transfer"
                    else categories.get(txn.category_id or "", "待分类")
                )
                results.append(
                    VisibleTransactionRecord(
                        id=txn.id,
                        occurred_at=txn.occurred_at,
                        type=txn.type,
                        payee=display_payee,
                        note=display_note,
                        category_id=txn.category_id,
                        category_name=cat_name,
                        account_ids=tuple(visible_acct_ids),
                        account_names=tuple(visible_acct_names),
                        account_scopes=tuple(visible_acct_scopes),
                        amount_minor=txn.amount_minor,
                        currency=txn.currency,
                        base_amount_minor=txn.base_amount_minor,
                        base_currency=txn.base_currency,
                        refund_of_id=None if partial else txn.refund_of_id,
                        split_id=split.id if split else None,
                        split_status=split.status if split else None,
                        member_share_minor=member_share_minor,
                        member_share_status=member_share_status,
                        redacted=partial,
                    )
                )
                if len(results) >= limit:
                    break

            return results


class InMemoryLedgerReadRepository:
    """Pure-Python read-only repository double for fast deterministic unit tests."""

    def __init__(
        self,
        *,
        contexts: dict[str, CatalogContext],
        transactions_by_member: dict[str, list[VisibleTransactionRecord]],
    ):
        self._contexts = contexts
        self._transactions_by_member = transactions_by_member

    def load_catalog_context(self, member_id: str) -> CatalogContext:
        if member_id not in self._contexts:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="家庭成员不存在")
        return self._contexts[member_id]

    def query_visible_transactions(
        self,
        member_id: str,
        *,
        date_from: date,
        date_to: date,
        types: tuple[str, ...] = (),
        category_ids: tuple[str, ...] = (),
        account_ids: tuple[str, ...] = (),
        scope: ScopeFilter = "all_visible",
        keyword: str | None = None,
        min_amount_minor: int | None = None,
        max_amount_minor: int | None = None,
        only_with_splits: bool = False,
        only_refunded: bool = False,
        limit: int = 500,
    ) -> list[VisibleTransactionRecord]:
        context = self.load_catalog_context(member_id)
        allowed_accounts = {acct.id for acct in context.accounts}
        for acct_id in account_ids:
            if acct_id not in allowed_accounts:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权操作此账户")

        start_utc, end_utc = _to_utc_bounds(date_from, date_to)
        rows = self._transactions_by_member.get(member_id, [])
        refunded_originals = {r.refund_of_id for r in rows if r.type == "refund" and r.refund_of_id}
        needle = keyword.casefold() if keyword else None
        requested_accounts = set(account_ids)
        out: list[VisibleTransactionRecord] = []

        for rec in sorted(rows, key=lambda r: (r.occurred_at, r.id), reverse=True):
            occurred = (
                rec.occurred_at if rec.occurred_at.tzinfo else rec.occurred_at.replace(tzinfo=UTC)
            )
            if occurred < start_utc or occurred > end_utc:
                continue
            if types and rec.type not in types:
                continue
            if category_ids and rec.category_id not in category_ids:
                continue
            if requested_accounts and not (requested_accounts & set(rec.account_ids)):
                continue
            if scope == "family_only":
                if (
                    rec.redacted
                    or not rec.account_scopes
                    or any(s != "family" for s in rec.account_scopes)
                ):
                    continue
            elif scope == "personal_only":
                if rec.redacted or "personal" not in rec.account_scopes:
                    continue
            if min_amount_minor is not None and rec.base_amount_minor < min_amount_minor:
                continue
            if max_amount_minor is not None and rec.base_amount_minor > max_amount_minor:
                continue
            if (
                needle is not None
                and needle not in rec.payee.casefold()
                and needle not in rec.note.casefold()
            ):
                continue
            if only_with_splits and rec.split_id is None:
                continue
            if only_refunded and rec.type != "refund" and rec.id not in refunded_originals:
                continue
            out.append(rec)
            if len(out) >= limit:
                break
        return out
