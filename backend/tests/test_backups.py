from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from conftest import as_member
from sqlalchemy import select

from homedatabase_api.config import get_settings
from homedatabase_api.database import get_session_factory
from homedatabase_api.models import BackupRecord
from homedatabase_api.services import backups as backup_service
from homedatabase_api.services.backups import cleanup_interrupted_backups, prune_verified_backups


def test_encrypted_database_backup_create_verify_and_download(client):
    denied = client.post("/api/backups", headers=as_member("m-dad"))
    assert denied.status_code == 403

    created = client.post("/api/backups", headers=as_member("m-son"))
    assert created.status_code == 201
    backup = created.json()
    assert backup["status"] == "ready"
    assert backup["encrypted"] is True
    assert len(backup["sha256"]) == 64
    assert backup["locations"] == [{"kind": "local", "status": "ok"}]
    assert backup["lastVerifiedAt"] is not None

    downloaded = client.get(f"/api/backups/{backup['id']}/download", headers=as_member("m-son"))
    assert downloaded.status_code == 200
    assert downloaded.content.startswith(b"HDBACKUP1")
    assert b"SQLite format 3" not in downloaded.content[:100]
    assert all(path.suffix == ".hdbak" for path in get_settings().backup_dir.iterdir())

    verified = client.post(f"/api/backups/{backup['id']}/verify", headers=as_member("m-son"))
    assert verified.status_code == 200
    assert verified.json()["lastVerifiedAt"] is not None


def test_verified_backup_rotation_keeps_newest_and_removes_old_files():
    settings = get_settings().model_copy(
        update={"backup_keep_hourly": 2, "backup_keep_daily": 0, "backup_keep_monthly": 0}
    )
    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    paths = []
    with get_session_factory()() as db:
        for index in range(4):
            backup_id = f"backup-rotation-{index}"
            path = (settings.backup_dir / f"{backup_id}.hdbak").resolve()
            path.write_bytes(b"HDBACKUP1-test")
            paths.append(path)
            db.add(
                BackupRecord(
                    id=backup_id,
                    created_at=now - timedelta(hours=index),
                    size_bytes=path.stat().st_size,
                    status="ready",
                    sha256="a" * 64,
                    encrypted_path=str(path),
                    locations=[{"kind": "local", "status": "ok"}],
                    last_verified_at=now,
                )
            )
        db.commit()
        assert prune_verified_backups(db, None, settings) == 2
        remaining = set(db.scalars(select(BackupRecord.id)).all())
    assert remaining == {"backup-rotation-0", "backup-rotation-1"}
    assert paths[0].exists() and paths[1].exists()
    assert not paths[2].exists() and not paths[3].exists()


def test_backup_creation_lock_rejects_parallel_attempt(client):
    assert backup_service._LOCAL_BACKUP_LOCK.acquire(blocking=False)
    try:
        response = client.post("/api/backups", headers=as_member("m-son"))
    finally:
        backup_service._LOCAL_BACKUP_LOCK.release()

    assert response.status_code == 409
    assert response.json()["detail"] == "已有数据库备份正在创建"


def test_backup_reserves_space_for_the_actual_encrypted_output(client, monkeypatch):
    settings = get_settings()
    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    existing_files = set(settings.backup_dir.iterdir())
    barely_above_floor = settings.backup_min_free_bytes + 1
    monkeypatch.setattr(
        backup_service.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=barely_above_floor, used=0, free=barely_above_floor),
    )

    response = client.post("/api/backups", headers=as_member("m-son"))

    assert response.status_code == 507
    assert "本次备份" in response.json()["detail"]
    assert set(settings.backup_dir.iterdir()) == existing_files


def test_interrupted_backup_cleanup_removes_stale_orphans_with_age_guard():
    settings = get_settings().model_copy(update={"backup_orphan_grace_seconds": 60})
    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    stale_final = settings.backup_dir / "backup-interrupted.hdbak"
    stale_partial = settings.backup_dir / "backup-interrupted.hdbak.part"
    old_orphan = settings.backup_dir / "backup-old-orphan.hdbak"
    young_orphan = settings.backup_dir / "backup-young-orphan.hdbak.part"
    referenced = settings.backup_dir / "backup-referenced.hdbak"
    for path in (stale_final, stale_partial, old_orphan, young_orphan, referenced):
        path.write_bytes(b"test")
    old_timestamp = (now - timedelta(minutes=10)).timestamp()
    os.utime(old_orphan, (old_timestamp, old_timestamp))

    with get_session_factory()() as db:
        db.add(
            BackupRecord(
                id="backup-interrupted",
                created_at=now - timedelta(minutes=1),
                size_bytes=0,
                status="creating",
                locations=[{"kind": "local", "status": "syncing"}],
            )
        )
        db.add(
            BackupRecord(
                id="backup-referenced",
                created_at=now,
                size_bytes=referenced.stat().st_size,
                status="ready",
                sha256="a" * 64,
                encrypted_path=str(referenced.resolve()),
                locations=[{"kind": "local", "status": "ok"}],
                last_verified_at=now,
            )
        )
        db.commit()

        assert cleanup_interrupted_backups(db, None, settings) == 3
        interrupted = db.get(BackupRecord, "backup-interrupted")
        assert interrupted is not None and interrupted.status == "failed"
        assert interrupted.encrypted_path is None

    assert not stale_final.exists()
    assert not stale_partial.exists()
    assert not old_orphan.exists()
    assert young_orphan.exists()
    assert referenced.exists()
