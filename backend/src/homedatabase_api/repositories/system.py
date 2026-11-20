from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..models import BackupRecord


class SystemRepository:
    """Persistence helpers for system health and backup metadata."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def ping_database(self) -> None:
        self.db.execute(text("SELECT 1"))

    def list_backups(self) -> list[BackupRecord]:
        return list(
            self.db.scalars(select(BackupRecord).order_by(BackupRecord.created_at.desc())).all()
        )

    def get_backup(self, backup_id: str) -> BackupRecord | None:
        return self.db.get(BackupRecord, backup_id)
