from __future__ import annotations

import json
from datetime import timedelta

from conftest import as_member
from sqlalchemy import select

from homedatabase_api.config import get_settings
from homedatabase_api.database import get_session_factory
from homedatabase_api.models import AuditEvent, SourceFile, SourceRecord, utcnow
from homedatabase_api.services.retention import cleanup_expired_source_files


def test_expired_source_ciphertext_is_deleted_but_metadata_is_retained(client):
    uploaded = client.post(
        "/api/imports",
        headers=as_member("m-son"),
        data={"source": "retention-test", "targetAccountId": "a-son-wallet"},
        files=[
            (
                "files",
                (
                    "bill.csv",
                    (
                        "交易时间,交易对方,收支类型,金额,银行卡号\n"
                        "2026-08-01,测试,支出,1.00,6222021234567890\n"
                    ).encode(),
                    "text/csv",
                ),
            )
        ],
    )
    assert uploaded.status_code == 201
    with get_session_factory()() as db:
        source = db.scalar(select(SourceFile).where(SourceFile.batch_id == uploaded.json()["id"]))
        assert source is not None and source.encrypted_path
        source_record = db.scalar(select(SourceRecord).where(SourceRecord.file_id == source.id))
        assert source_record is not None
        assert "6222021234567890" in json.dumps(source_record.raw_payload)
        encrypted = get_settings().source_archive_dir / source.encrypted_path
        assert encrypted.is_file()
        source.expires_at = utcnow() - timedelta(minutes=1)
        db.commit()
        assert cleanup_expired_source_files(db, get_settings()) == 1
        db.refresh(source)
        db.refresh(source_record)
        assert source.deleted_at is not None
        assert source.encrypted_path is None
        assert not encrypted.exists()
        retained = json.dumps(source_record.raw_payload)
        assert "6222021234567890" not in retained
        assert source_record.raw_payload == {
            "schema": "source-record-redacted-v1",
            "redacted": True,
            "issueCodes": [],
        }
        audit = db.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "source_file.expired",
                AuditEvent.entity_id == source.id,
            )
        )
        assert audit is not None
        assert audit.after["redacted_source_record_count"] == 1
