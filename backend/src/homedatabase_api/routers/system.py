from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .. import __version__
from ..config import Settings, get_settings
from ..database import get_db
from ..dependencies import get_current_member, require_admin
from ..models import BackupRecord, Member, Passkey
from ..schemas import (
    BackupRecordOut,
    DeleteInput,
    HealthOut,
    MemberOut,
    PasskeyOut,
    PeriodLockInput,
    PeriodLockOut,
    ReportTierOut,
)
from ..services.presenters import present_backup, present_passkey
from ..services.reports import build_xlsx, report_tiers

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthOut)
def health(db: Session = Depends(get_db)) -> HealthOut:
    db.execute(text("SELECT 1"))
    return HealthOut(status="ok", database="ok", version=__version__)


@router.get("/members", response_model=list[MemberOut])
def members(db: Session = Depends(get_db), _member: Member = Depends(get_current_member)):
    return [
        MemberOut.model_validate(item)
        for item in db.scalars(select(Member).where(Member.active.is_(True))).all()
    ]


@router.get("/passkeys", response_model=list[PasskeyOut])
def passkeys(db: Session = Depends(get_db), member: Member = Depends(get_current_member)):
    records = db.scalars(
        select(Passkey).where(Passkey.member_id == member.id).order_by(Passkey.created_at.desc())
    ).all()
    return [present_passkey(item) for item in records]


@router.delete("/passkeys/{passkey_id}", status_code=204)
def delete_passkey(
    passkey_id: str,
    db: Session = Depends(get_db),
    member: Member = Depends(get_current_member),
) -> Response:
    from ..services.passkeys import delete_passkey as delete_passkey_service

    delete_passkey_service(db, member, passkey_id)
    return Response(status_code=204)


@router.get("/backups", response_model=list[BackupRecordOut])
def backups(db: Session = Depends(get_db), _admin: Member = Depends(require_admin)):
    records = db.scalars(select(BackupRecord).order_by(BackupRecord.created_at.desc())).all()
    return [present_backup(item) for item in records]


@router.post("/backups", response_model=BackupRecordOut, status_code=201)
def create_backup(
    db: Session = Depends(get_db),
    admin: Member = Depends(require_admin),
    settings: Settings = Depends(get_settings),
):
    from ..services.backups import create_backup as create_backup_service

    return present_backup(create_backup_service(db, admin, settings))


def _backup_or_404(db: Session, backup_id: str) -> BackupRecord:
    record = db.get(BackupRecord, backup_id)
    if not record:
        raise HTTPException(status_code=404, detail="备份记录不存在")
    return record


@router.post("/backups/{backup_id}/verify", response_model=BackupRecordOut)
def verify_backup(
    backup_id: str,
    db: Session = Depends(get_db),
    admin: Member = Depends(require_admin),
    settings: Settings = Depends(get_settings),
):
    from ..services.backups import verify_backup as verify_backup_service

    record = verify_backup_service(db, admin, _backup_or_404(db, backup_id), settings)
    return present_backup(record)


@router.get("/backups/{backup_id}/download")
def download_backup(
    backup_id: str,
    db: Session = Depends(get_db),
    _admin: Member = Depends(require_admin),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    from ..services.backups import get_backup_path

    record = _backup_or_404(db, backup_id)
    path = get_backup_path(record, settings)
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=f"homedatabase-{record.created_at:%Y%m%d-%H%M%S}.hdbak",
    )


@router.get("/report-tiers", response_model=list[ReportTierOut])
def tiers(_member: Member = Depends(get_current_member)):
    return report_tiers()


@router.get("/period-locks", response_model=list[PeriodLockOut])
def period_locks(db: Session = Depends(get_db), _member: Member = Depends(get_current_member)):
    from ..services.periods import list_period_locks

    return list_period_locks(db)


@router.post("/period-locks", response_model=PeriodLockOut, status_code=201)
def create_period_lock(
    payload: PeriodLockInput,
    db: Session = Depends(get_db),
    admin: Member = Depends(require_admin),
):
    from ..services.periods import create_period_lock as create_period_lock_service

    return create_period_lock_service(db, admin, payload)


@router.delete("/period-locks/{period_lock_id}", status_code=204)
def delete_period_lock(
    period_lock_id: str,
    payload: DeleteInput,
    db: Session = Depends(get_db),
    admin: Member = Depends(require_admin),
) -> Response:
    from ..services.periods import delete_period_lock as delete_period_lock_service

    delete_period_lock_service(db, admin, period_lock_id, payload.reason)
    return Response(status_code=204)


@router.get("/reports/export")
def export_report(
    tier: int = Query(ge=1, le=5),
    starts_on: date = Query(alias="from"),
    ends_on: date = Query(alias="to"),
    db: Session = Depends(get_db),
    member: Member = Depends(get_current_member),
) -> Response:
    payload = build_xlsx(db, member, tier=tier, starts_on=starts_on, ends_on=ends_on)
    filename = f"homedatabase-{starts_on.isoformat()}-{ends_on.isoformat()}-tier{tier}.xlsx"
    return Response(
        content=payload,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
