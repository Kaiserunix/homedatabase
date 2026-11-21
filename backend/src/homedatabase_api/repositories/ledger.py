from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..models import (
    Category,
    EntryLine,
    PeriodLock,
    SourceFile,
    SourceRecord,
    Split,
    Transaction,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")


class LedgerRepository:
    """Persistence queries and row-locking helpers for ledger, splits, and provenance."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def list_categories(self, *, include_inactive: bool = False) -> list[Category]:
        statement = select(Category)
        if not include_inactive:
            statement = statement.where(Category.active.is_(True))
        return list(
            self.db.scalars(
                statement.order_by(Category.kind, Category.parent_id, Category.name)
            ).all()
        )

    def get_category(self, category_id: str) -> Category | None:
        return self.db.get(Category, category_id)

    def period_is_locked(self, occurred_at: datetime) -> bool:
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=UTC)
        local_day = occurred_at.astimezone(SHANGHAI).date()
        return (
            self.db.scalar(
                select(func.count(PeriodLock.id)).where(
                    PeriodLock.starts_on <= local_day,
                    PeriodLock.ends_on >= local_day,
                )
            )
            or 0
        ) > 0

    def list_transactions_filtered(
        self,
        *,
        type_filter: str | None = None,
        account_id: str | None = None,
        starts_at: datetime | None = None,
        ends_at: datetime | None = None,
        ascending: bool = False,
        limit: int | None = 5000,
    ) -> list[Transaction]:
        statement = (
            select(Transaction)
            .where(Transaction.deleted_at.is_(None))
            .options(selectinload(Transaction.lines))
        )
        if type_filter:
            statement = statement.where(Transaction.type == type_filter)
        if account_id:
            statement = statement.where(
                Transaction.id.in_(
                    select(EntryLine.transaction_id).where(EntryLine.account_id == account_id)
                )
            )
        if starts_at is not None:
            statement = statement.where(Transaction.occurred_at >= starts_at)
        if ends_at is not None:
            statement = statement.where(Transaction.occurred_at <= ends_at)
        if ascending:
            statement = statement.order_by(Transaction.occurred_at.asc(), Transaction.id.asc())
        else:
            statement = statement.order_by(Transaction.occurred_at.desc(), Transaction.id.desc())
        if limit is not None:
            statement = statement.limit(limit)
        return list(self.db.scalars(statement).all())

    def list_recycled_transactions(self, *, limit: int = 500) -> list[Transaction]:
        return list(
            self.db.scalars(
                select(Transaction)
                .where(Transaction.deleted_at.is_not(None))
                .options(selectinload(Transaction.lines))
                .order_by(Transaction.deleted_at.desc())
                .limit(limit)
            ).all()
        )

    def get_active_transaction(
        self,
        transaction_id: str,
        *,
        for_update: bool = False,
    ) -> Transaction | None:
        statement = (
            select(Transaction)
            .where(Transaction.id == transaction_id, Transaction.deleted_at.is_(None))
            .options(selectinload(Transaction.lines))
        )
        if for_update:
            statement = statement.with_for_update()
        return self.db.scalar(statement)

    def list_split_ids_by_transaction_ids(
        self,
        transaction_ids: Sequence[str],
    ) -> dict[str, str]:
        if not transaction_ids:
            return {}
        rows = self.db.execute(
            select(Split.transaction_id, Split.id).where(
                Split.transaction_id.in_(list(dict.fromkeys(transaction_ids)))
            )
        ).all()
        return {str(txn_id): str(split_id) for txn_id, split_id in rows}

    def list_splits_by_transaction_ids(
        self,
        transaction_ids: Sequence[str],
    ) -> dict[str, Split]:
        if not transaction_ids:
            return {}
        splits = self.db.scalars(
            select(Split)
            .where(Split.transaction_id.in_(list(dict.fromkeys(transaction_ids))))
            .options(selectinload(Split.shares))
        ).all()
        return {split.transaction_id: split for split in splits}

    def list_active_refunds_by_original_ids(
        self,
        original_ids: Sequence[str],
    ) -> dict[str, list[Transaction]]:
        if not original_ids:
            return {}
        refunds = self.db.scalars(
            select(Transaction)
            .where(
                Transaction.refund_of_id.in_(list(dict.fromkeys(original_ids))),
                Transaction.type == "refund",
                Transaction.deleted_at.is_(None),
            )
            .options(selectinload(Transaction.lines))
            .order_by(Transaction.occurred_at.asc(), Transaction.id.asc())
        ).all()
        grouped: dict[str, list[Transaction]] = defaultdict(list)
        for refund in refunds:
            if refund.refund_of_id:
                grouped[refund.refund_of_id].append(refund)
        return dict(grouped)

    def list_source_provenance_by_transaction_ids(
        self,
        transaction_ids: Sequence[str],
    ) -> dict[str, tuple[str | None, str | None]]:
        if not transaction_ids:
            return {}
        rows = self.db.execute(
            select(
                SourceRecord.linked_transaction_id,
                SourceFile.filename,
                SourceRecord.source_ref,
            )
            .join(SourceFile, SourceRecord.file_id == SourceFile.id)
            .where(
                SourceRecord.linked_transaction_id.in_(list(dict.fromkeys(transaction_ids))),
                SourceFile.deleted_at.is_(None),
            )
        ).all()
        result: dict[str, tuple[str | None, str | None]] = {}
        for txn_id, filename, source_ref in rows:
            if txn_id and str(txn_id) not in result:
                result[str(txn_id)] = (filename, source_ref)
        return result
