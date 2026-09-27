from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Member, PeriodLock
from ..schemas import PeriodLockInput, PeriodLockOut
from .audit import record_event
from .locking import acquire_ledger_mutation_guard


def list_period_locks(db: Session) -> list[PeriodLockOut]:
    records = db.scalars(select(PeriodLock).order_by(PeriodLock.starts_on.desc())).all()
    return [PeriodLockOut.model_validate(item) for item in records]


def create_period_lock(db: Session, member: Member, input_data: PeriodLockInput) -> PeriodLockOut:
    acquire_ledger_mutation_guard(db)
    overlap = db.scalar(
        select(PeriodLock.id).where(
            PeriodLock.starts_on <= input_data.ends_on,
            PeriodLock.ends_on >= input_data.starts_on,
        )
    )
    if overlap:
        raise HTTPException(status_code=409, detail="该日期范围与已有锁定期间重叠")
    record = PeriodLock(
        starts_on=input_data.starts_on,
        ends_on=input_data.ends_on,
        locked_by=member.id,
        reason=input_data.reason,
    )
    db.add(record)
    db.flush()
    record_event(
        db,
        member,
        "period.locked",
        "period_lock",
        record.id,
        after={
            "starts_on": record.starts_on.isoformat(),
            "ends_on": record.ends_on.isoformat(),
            "reason": record.reason,
        },
    )
    db.commit()
    db.refresh(record)
    return PeriodLockOut.model_validate(record)


def delete_period_lock(db: Session, member: Member, period_lock_id: str, reason: str) -> None:
    acquire_ledger_mutation_guard(db)
    record = db.get(PeriodLock, period_lock_id)
    if not record:
        raise HTTPException(status_code=404, detail="锁定期间不存在")
    record_event(
        db,
        member,
        "period.unlocked",
        "period_lock",
        record.id,
        before={
            "starts_on": record.starts_on.isoformat(),
            "ends_on": record.ends_on.isoformat(),
            "lock_reason": record.reason,
            "unlock_reason": reason,
        },
    )
    db.delete(record)
    db.commit()
