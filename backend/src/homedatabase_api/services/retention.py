from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import SourceFile, SourceRecord, utcnow
from .audit import record_event


def cleanup_expired_source_files(
    db: Session, settings: Settings, *, now: datetime | None = None
) -> int:
    cutoff = now or utcnow()
    records = db.scalars(
        select(SourceFile).where(
            SourceFile.deleted_at.is_(None),
            SourceFile.expires_at.is_not(None),
            SourceFile.expires_at <= cutoff,
        )
    ).all()
    root = settings.source_archive_dir.resolve()
    deleted = 0
    for record in records:
        if record.encrypted_path:
            path = (root / Path(record.encrypted_path).name).resolve()
            if path.is_relative_to(root):
                path.unlink(missing_ok=True)
        source_records = db.scalars(
            select(SourceRecord).where(SourceRecord.file_id == record.id)
        ).all()
        for source_record in source_records:
            issue_codes = source_record.raw_payload.get("issues", [])
            if not isinstance(issue_codes, list):
                issue_codes = []
            source_record.raw_payload = {
                "schema": "source-record-redacted-v1",
                "redacted": True,
                "issueCodes": [value[:100] for value in issue_codes if isinstance(value, str)][:50],
            }
        record.encrypted_path = None
        record.deleted_at = cutoff
        record_event(
            db,
            None,
            "source_file.expired",
            "source_file",
            record.id,
            after={
                "expired_at": cutoff.isoformat(),
                "redacted_source_record_count": len(source_records),
            },
        )
        deleted += 1
    db.commit()
    return deleted
