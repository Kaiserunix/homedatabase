from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..database import get_db
from ..dependencies import get_current_member, require_admin
from ..models import Member
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
from ..services import passkeys as passkey_service
from ..services import periods as period_service
from ..services import system as system_service
from ..services.reports import build_xlsx, report_tiers

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthOut)
def health(db: Session = Depends(get_db)) -> HealthOut:
    return system_service.check_health(db)


@router.get("/members", response_model=list[MemberOut])
def members(db: Session = Depends(get_db), _member: Member = Depends(get_current_member)):
    return system_service.list_active_members(db)


@router.get("/passkeys", response_model=list[PasskeyOut])
def passkeys(db: Session = Depends(get_db), member: Member = Depends(get_current_member)):
    return system_service.list_member_passkeys(db, member)


@router.delete("/passkeys/{passkey_id}", status_code=204)
def delete_passkey(
    passkey_id: str,
    db: Session = Depends(get_db),
    member: Member = Depends(get_current_member),
) -> Response:
    passkey_service.delete_passkey(db, member, passkey_id)
    return Response(status_code=204)


@router.get("/backups", response_model=list[BackupRecordOut])
def backups(db: Session = Depends(get_db), _admin: Member = Depends(require_admin)):
    return system_service.list_backups(db)


@router.post("/backups", response_model=BackupRecordOut, status_code=201)
def create_backup(
    db: Session = Depends(get_db),
    admin: Member = Depends(require_admin),
    settings: Settings = Depends(get_settings),
):
    return system_service.create_system_backup(db, admin, settings)


@router.post("/backups/{backup_id}/verify", response_model=BackupRecordOut)
def verify_backup(
    backup_id: str,
    db: Session = Depends(get_db),
    admin: Member = Depends(require_admin),
    settings: Settings = Depends(get_settings),
):
    return system_service.verify_system_backup(db, admin, backup_id, settings)


@router.get("/backups/{backup_id}/download")
def download_backup(
    backup_id: str,
    db: Session = Depends(get_db),
    _admin: Member = Depends(require_admin),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    path, filename = system_service.resolve_backup_download(db, backup_id, settings)
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=filename,
    )


@router.get("/report-tiers", response_model=list[ReportTierOut])
def tiers(_member: Member = Depends(get_current_member)):
    return report_tiers()


@router.get("/period-locks", response_model=list[PeriodLockOut])
def period_locks(db: Session = Depends(get_db), _member: Member = Depends(get_current_member)):
    return period_service.list_period_locks(db)


@router.post("/period-locks", response_model=PeriodLockOut, status_code=201)
def create_period_lock(
    payload: PeriodLockInput,
    db: Session = Depends(get_db),
    admin: Member = Depends(require_admin),
):
    return period_service.create_period_lock(db, admin, payload)


@router.delete("/period-locks/{period_lock_id}", status_code=204)
def delete_period_lock(
    period_lock_id: str,
    payload: DeleteInput,
    db: Session = Depends(get_db),
    admin: Member = Depends(require_admin),
) -> Response:
    period_service.delete_period_lock(db, admin, period_lock_id, payload.reason)
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
