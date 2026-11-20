from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .. import __version__
from ..config import Settings
from ..models import BackupRecord, Member
from ..repositories.auth import AuthRepository
from ..repositories.system import SystemRepository
from ..schemas import BackupRecordOut, HealthOut, MemberOut, PasskeyOut
from .backups import (
    create_backup as create_backup_record,
)
from .backups import (
    get_backup_path,
)
from .backups import (
    verify_backup as verify_backup_record,
)
from .presenters import present_backup, present_passkey


def check_health(db: Session) -> HealthOut:
    SystemRepository(db).ping_database()
    return HealthOut(status="ok", database="ok", version=__version__)


def list_active_members(db: Session) -> list[MemberOut]:
    return [MemberOut.model_validate(item) for item in AuthRepository(db).list_active_members()]


def list_member_passkeys(db: Session, member: Member) -> list[PasskeyOut]:
    return [present_passkey(item) for item in AuthRepository(db).list_member_passkeys(member.id)]


def list_backups(db: Session) -> list[BackupRecordOut]:
    return [present_backup(item) for item in SystemRepository(db).list_backups()]


def create_system_backup(db: Session, admin: Member, settings: Settings) -> BackupRecordOut:
    return present_backup(create_backup_record(db, admin, settings))


def _require_backup(db: Session, backup_id: str) -> BackupRecord:
    record = SystemRepository(db).get_backup(backup_id)
    if not record:
        raise HTTPException(status_code=404, detail="备份记录不存在")
    return record


def verify_system_backup(
    db: Session,
    admin: Member,
    backup_id: str,
    settings: Settings,
) -> BackupRecordOut:
    record = _require_backup(db, backup_id)
    return present_backup(verify_backup_record(db, admin, record, settings))


def resolve_backup_download(
    db: Session,
    backup_id: str,
    settings: Settings,
) -> tuple[Path, str]:
    record = _require_backup(db, backup_id)
    path = get_backup_path(record, settings)
    filename = f"homedatabase-{record.created_at:%Y%m%d-%H%M%S}.hdbak"
    return path, filename
