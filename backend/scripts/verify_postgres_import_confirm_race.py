from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Barrier

from fastapi import HTTPException
from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.orm import Session

from homedatabase_api.config import get_settings
from homedatabase_api.database import get_session_factory
from homedatabase_api.models import (
    Account,
    AuditEvent,
    ImportBatch,
    ImportCandidate,
    Member,
    Transaction,
    utcnow,
)
from homedatabase_api.services.imports import confirm_candidates

MEMBER_ID = "m-import-race-ci"
ACCOUNT_ID = "a-import-race-ci"
BATCH_ID = "batch-import-race-ci"
CANDIDATE_ID = "cand-import-race-ci"


@dataclass(frozen=True)
class Outcome:
    status_code: int
    detail: str


def _cleanup(db: Session) -> None:
    transaction_ids = select(Transaction.id).where(Transaction.batch_id == BATCH_ID)
    db.execute(
        delete(AuditEvent).where(
            or_(
                AuditEvent.actor_id == MEMBER_ID,
                AuditEvent.entity_id.in_(transaction_ids),
                AuditEvent.entity_id.in_([MEMBER_ID, ACCOUNT_ID, BATCH_ID, CANDIDATE_ID]),
            )
        )
    )
    db.execute(delete(Transaction).where(Transaction.batch_id == BATCH_ID))
    db.execute(delete(ImportBatch).where(ImportBatch.id == BATCH_ID))
    db.execute(delete(Account).where(Account.id == ACCOUNT_ID))
    db.execute(delete(Member).where(Member.id == MEMBER_ID))
    db.commit()


def _seed(db: Session) -> None:
    _cleanup(db)
    db.add(
        Member(
            id=MEMBER_ID,
            identity="dad",
            name="Import race CI",
            username="import-race-ci",
            password_hash="not-a-login",
            is_admin=False,
            color="bg-slate-400",
            active=True,
            version=1,
        )
    )
    # No ORM relationship orders these separate entities; flush each referenced row.
    db.flush()
    db.add(
        Account(
            id=ACCOUNT_ID,
            name="Import race CI account",
            kind="asset",
            scope="personal",
            owner_id=MEMBER_ID,
            currency="CNY",
            opening_balance_minor=0,
            icon="wallet",
            version=1,
        )
    )
    db.flush()
    db.add(
        ImportBatch(
            id=BATCH_ID,
            owner_id=MEMBER_ID,
            target_account_id=ACCOUNT_ID,
            filename="import-race-ci.csv",
            source="ci",
            status="reviewing",
            job_id="job-import-race-ci",
            job_progress=100,
            job_message="等待并发确认",
            version=1,
        )
    )
    db.flush()
    db.add(
        ImportCandidate(
            id=CANDIDATE_ID,
            batch_id=BATCH_ID,
            date=utcnow(),
            payee="并发确认验收",
            direction="expense",
            amount_minor=12345,
            currency="CNY",
            target_account_id=ACCOUNT_ID,
            status="pending",
            validation_status="valid",
            version=1,
        )
    )
    db.commit()


def _confirm_once(barrier: Barrier) -> Outcome:
    with get_session_factory()() as db:
        db.execute(text("SET LOCAL lock_timeout = '10s'"))
        db.execute(text("SET LOCAL statement_timeout = '20s'"))
        member = db.get(Member, MEMBER_ID)
        if member is None:
            raise AssertionError("concurrency fixture member is missing")
        barrier.wait(timeout=15)
        try:
            result = confirm_candidates(db, member, BATCH_ID, [CANDIDATE_ID])
        except HTTPException as exc:
            db.rollback()
            return Outcome(status_code=exc.status_code, detail=str(exc.detail))
        return Outcome(status_code=200, detail=result.status)


def _verify_final_state(db: Session, outcomes: list[Outcome]) -> str:
    if sorted(item.status_code for item in outcomes) != [200, 409]:
        raise AssertionError(f"expected one success and one conflict, got {outcomes!r}")

    batch = db.get(ImportBatch, BATCH_ID)
    candidate = db.get(ImportCandidate, CANDIDATE_ID)
    if batch is None or candidate is None:
        raise AssertionError("import batch or candidate disappeared")
    if batch.status != "posted":
        raise AssertionError(f"batch status is {batch.status!r}, expected 'posted'")
    if batch.job_message != "批次候选已全部处理并入账":
        raise AssertionError(f"unexpected batch message: {batch.job_message!r}")
    if candidate.status != "confirmed" or not candidate.transaction_id:
        raise AssertionError("candidate was not linked to a confirmed transaction")

    transaction_count = db.scalar(
        select(func.count(Transaction.id)).where(
            Transaction.batch_id == BATCH_ID,
            Transaction.deleted_at.is_(None),
        )
    )
    if transaction_count != 1:
        raise AssertionError(f"expected one transaction, found {transaction_count}")
    transaction = db.get(Transaction, candidate.transaction_id)
    if transaction is None or transaction.source != "import":
        raise AssertionError("candidate transaction is missing or not marked as an import")

    audit_count = db.scalar(
        select(func.count(AuditEvent.id)).where(
            AuditEvent.actor_id == MEMBER_ID,
            AuditEvent.action == "import_batch.confirmed",
            AuditEvent.entity_id == BATCH_ID,
        )
    )
    if audit_count != 1:
        raise AssertionError(f"expected one confirmation audit event, found {audit_count}")
    return transaction.id


def main() -> None:
    settings = get_settings()
    if not settings.database_url.startswith("postgresql"):
        raise RuntimeError("this concurrency verification requires PostgreSQL")

    try:
        with get_session_factory()() as db:
            _seed(db)

        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="import-confirm-ci") as pool:
            futures = [pool.submit(_confirm_once, barrier) for _ in range(2)]
            outcomes = [future.result(timeout=30) for future in futures]

        with get_session_factory()() as db:
            transaction_id = _verify_final_state(db, outcomes)
        conflict = next(item for item in outcomes if item.status_code == 409)
        print(
            "PostgreSQL concurrent import confirmation verified: "
            f"outcomes={sorted(item.status_code for item in outcomes)}, "
            f"transaction={transaction_id}, batch=posted, conflict={conflict.detail!r}"
        )
    finally:
        with get_session_factory()() as db:
            _cleanup(db)


if __name__ == "__main__":
    main()
