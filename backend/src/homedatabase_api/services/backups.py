from __future__ import annotations

import base64
import hashlib
import os
import shutil
import sqlite3
import subprocess
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from ..config import Settings
from ..database import get_engine
from ..models import BackupRecord, Member, utcnow
from .audit import record_event

MAGIC = b"HDBACKUP1"
NONCE_BYTES = 12
TAG_BYTES = 16
CHUNK_BYTES = 1024 * 1024
BACKUP_ADVISORY_LOCK_ID = 0x48444241434B5550
_LOCAL_BACKUP_LOCK = threading.Lock()


@contextmanager
def backup_creation_lock(db: Session) -> Iterator[None]:
    """Serialize backup creation across API and maintenance processes.

    Production uses a dedicated PostgreSQL connection so the session-level
    advisory lock cannot leak through SQLAlchemy's pool across intermediate
    commits. SQLite is development/test only and uses a process-local lock.
    """

    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        with bind.connect() as lock_connection:
            acquired = lock_connection.scalar(
                text("SELECT pg_try_advisory_lock(:lock_id)"),
                {"lock_id": BACKUP_ADVISORY_LOCK_ID},
            )
            if not acquired:
                raise HTTPException(status_code=409, detail="已有数据库备份正在创建")
            try:
                yield
            finally:
                lock_connection.execute(
                    text("SELECT pg_advisory_unlock(:lock_id)"),
                    {"lock_id": BACKUP_ADVISORY_LOCK_ID},
                )
        return

    if not _LOCAL_BACKUP_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="已有数据库备份正在创建")
    try:
        yield
    finally:
        _LOCAL_BACKUP_LOCK.release()


def _decode_key(configured: str | None) -> bytes:
    if not configured:
        raise HTTPException(status_code=503, detail="数据库备份密钥尚未配置")
    try:
        key = base64.urlsafe_b64decode(configured + "=" * (-len(configured) % 4))
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=503, detail="数据库备份密钥格式错误") from exc
    if len(key) != 32:
        raise HTTPException(status_code=503, detail="数据库备份密钥必须是 32 字节")
    return key


def _snapshot_sqlite(target: Path) -> None:
    raw = get_engine().raw_connection()
    try:
        source = raw.driver_connection
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        raw.close()


def _snapshot_postgres(target: Path, settings: Settings) -> None:
    url = make_url(settings.database_url)
    environment = os.environ.copy()
    if url.password:
        environment["PGPASSWORD"] = url.password
    command = [settings.pg_dump_path, "--format=custom", "--no-owner", "--no-privileges"]
    if url.host:
        command.extend(["--host", url.host])
    if url.port:
        command.extend(["--port", str(url.port)])
    if url.username:
        command.extend(["--username", url.username])
    command.extend(["--file", str(target), url.database or "homedatabase"])
    try:
        result = subprocess.run(
            command,
            env=environment,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("pg_dump 无法执行或超时") from exc
    if result.returncode != 0:
        message = (result.stderr or "pg_dump 失败").strip().splitlines()[-1]
        raise RuntimeError(message[:300])


def _encrypt(source: Path, target: Path, key: bytes) -> str:
    nonce = os.urandom(NONCE_BYTES)
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    digest = hashlib.sha256()
    with source.open("rb") as plain, target.open("wb") as encrypted:
        header = MAGIC + nonce
        encrypted.write(header)
        digest.update(header)
        while chunk := plain.read(CHUNK_BYTES):
            ciphertext = encryptor.update(chunk)
            encrypted.write(ciphertext)
            digest.update(ciphertext)
        tail = encryptor.finalize()
        encrypted.write(tail)
        digest.update(tail)
        encrypted.write(encryptor.tag)
        digest.update(encryptor.tag)
    return digest.hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _decrypt(source: Path, target: Path, key: bytes) -> None:
    size = source.stat().st_size
    if size < len(MAGIC) + NONCE_BYTES + TAG_BYTES:
        raise RuntimeError("备份文件不完整")
    with source.open("rb") as encrypted:
        if encrypted.read(len(MAGIC)) != MAGIC:
            raise RuntimeError("备份文件头错误")
        nonce = encrypted.read(NONCE_BYTES)
        encrypted.seek(-TAG_BYTES, os.SEEK_END)
        tag = encrypted.read(TAG_BYTES)
        encrypted.seek(len(MAGIC) + NONCE_BYTES)
        remaining = size - len(MAGIC) - NONCE_BYTES - TAG_BYTES
        decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
        with target.open("wb") as plain:
            while remaining:
                chunk = encrypted.read(min(CHUNK_BYTES, remaining))
                if not chunk:
                    raise RuntimeError("备份文件被截断")
                remaining -= len(chunk)
                plain.write(decryptor.update(chunk))
            plain.write(decryptor.finalize())


def _verify_backup_payload(path: Path, key: bytes, settings: Settings) -> None:
    with tempfile.TemporaryDirectory(prefix="homedatabase-verify-") as temporary:
        restored = Path(temporary) / "restored.dump"
        _decrypt(path, restored, key)
        if settings.database_url.startswith("sqlite"):
            connection = sqlite3.connect(f"file:{restored}?mode=ro", uri=True)
            try:
                result = connection.execute("PRAGMA integrity_check").fetchone()
            finally:
                connection.close()
            if not result or result[0] != "ok":
                raise RuntimeError("SQLite integrity_check 未通过")
            return

        try:
            result = subprocess.run(
                [settings.pg_restore_path, "--list", str(restored)],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError("pg_restore 无法执行或超时") from exc
        if result.returncode != 0 or not result.stdout.strip():
            raise RuntimeError("pg_restore 无法读取备份目录")


def _record_path(record: BackupRecord, settings: Settings) -> Path:
    if not record.encrypted_path:
        raise HTTPException(status_code=409, detail="备份尚未生成")
    root = settings.backup_dir.resolve()
    path = Path(record.encrypted_path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(status_code=404, detail="备份文件不存在")
    return path


def cleanup_interrupted_backups(
    db: Session,
    member: Member | None,
    settings: Settings,
) -> int:
    """Recover storage left behind by a killed backup process.

    The caller holds ``backup_creation_lock``. Records still marked creating
    therefore belong to an interrupted process and their deterministic files
    can be removed immediately. Other unreferenced files receive a short age
    grace so an operator copying a file into the directory is not raced.
    """

    root = settings.backup_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    stale_records = db.scalars(select(BackupRecord).where(BackupRecord.status == "creating")).all()
    removed_files = 0

    for record in stale_records:
        for name in (f"{record.id}.hdbak", f"{record.id}.hdbak.part"):
            candidate = root / name
            if candidate.parent.resolve() == root and candidate.exists():
                candidate.unlink(missing_ok=True)
                removed_files += 1
        record_event(
            db,
            member,
            "backup.interrupted",
            "backup",
            record.id,
            before={"status": "creating"},
            after={"status": "failed"},
        )
        record.status = "failed"
        record.encrypted_path = None
        record.last_verified_at = None
        record.size_bytes = 0
        record.sha256 = None
        record.locations = [{"kind": settings.backup_location_kind, "status": "failed"}]

    referenced: set[Path] = set()
    for configured_path in db.scalars(
        select(BackupRecord.encrypted_path).where(BackupRecord.encrypted_path.is_not(None))
    ):
        path = Path(configured_path).resolve()
        if path.is_relative_to(root):
            referenced.add(path)

    cutoff = datetime.now(UTC).timestamp() - settings.backup_orphan_grace_seconds
    for pattern in ("backup-*.hdbak.part", "backup-*.hdbak"):
        for candidate in root.glob(pattern):
            resolved = candidate.resolve()
            if not resolved.is_relative_to(root) or resolved in referenced:
                continue
            try:
                old_enough = candidate.stat().st_mtime <= cutoff
            except FileNotFoundError:
                continue
            if old_enough:
                candidate.unlink(missing_ok=True)
                removed_files += 1

    if stale_records or removed_files:
        record_event(
            db,
            member,
            "backup.orphans_pruned",
            "backup-storage",
            "local",
            after={"stale_records": len(stale_records), "removed_files": removed_files},
        )
        db.commit()
    return removed_files


def create_backup(db: Session, member: Member | None, settings: Settings) -> BackupRecord:
    with backup_creation_lock(db):
        return _create_backup_locked(db, member, settings)


def _create_backup_locked(db: Session, member: Member | None, settings: Settings) -> BackupRecord:
    key = _decode_key(settings.backup_encryption_key)
    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    cleanup_interrupted_backups(db, member, settings)
    prune_verified_backups(db, member, settings)
    free_bytes = shutil.disk_usage(settings.backup_dir).free
    if free_bytes < settings.backup_min_free_bytes:
        raise HTTPException(status_code=507, detail="备份磁盘剩余空间低于安全线")
    record = BackupRecord(
        size_bytes=0,
        status="creating",
        locations=[{"kind": settings.backup_location_kind, "status": "syncing"}],
    )
    db.add(record)
    db.flush()
    db.commit()
    final_path = (settings.backup_dir / f"{record.id}.hdbak").resolve()
    partial_path = final_path.with_suffix(".hdbak.part")
    try:
        # Plain database dumps must never be created inside the persistent
        # encrypted-backup volume.  The deployment mounts /tmp as tmpfs, so a
        # crash cannot leave a plaintext dump next to files that are synced or
        # retained as backups.
        with tempfile.TemporaryDirectory(prefix="homedatabase-backup-") as temporary:
            snapshot = Path(temporary) / "database.dump"
            if settings.database_url.startswith("sqlite"):
                _snapshot_sqlite(snapshot)
            else:
                _snapshot_postgres(snapshot, settings)
            encrypted_size = snapshot.stat().st_size + len(MAGIC) + NONCE_BYTES + TAG_BYTES
            free_bytes = shutil.disk_usage(settings.backup_dir).free
            if free_bytes < settings.backup_min_free_bytes + encrypted_size:
                raise HTTPException(
                    status_code=507,
                    detail="备份磁盘空间不足以写入本次备份并保留安全余量",
                )
            record.sha256 = _encrypt(snapshot, partial_path, key)
            os.replace(partial_path, final_path)
        record.size_bytes = final_path.stat().st_size
        record.encrypted_path = str(final_path)
        _verify_backup_payload(final_path, key, settings)
        record.last_verified_at = utcnow()
        record.status = "ready"
        record.locations = [{"kind": settings.backup_location_kind, "status": "ok"}]
        record_event(
            db,
            member,
            "backup.created",
            "backup",
            record.id,
            after={"sha256": record.sha256, "size_bytes": record.size_bytes},
        )
        record_event(db, member, "backup.verified", "backup", record.id)
        db.commit()
        prune_verified_backups(db, member, settings)
        db.refresh(record)
        return record
    except Exception as exc:
        partial_path.unlink(missing_ok=True)
        final_path.unlink(missing_ok=True)
        record.status = "failed"
        record.encrypted_path = None
        record.last_verified_at = None
        record.size_bytes = 0
        record.sha256 = None
        record.locations = [{"kind": settings.backup_location_kind, "status": "failed"}]
        db.commit()
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(status_code=503, detail=f"数据库备份失败：{exc}") from exc


def verify_backup(
    db: Session, member: Member | None, record: BackupRecord, settings: Settings
) -> BackupRecord:
    if record.status != "ready":
        raise HTTPException(status_code=409, detail="备份尚未就绪")
    path = _record_path(record, settings)
    actual_hash = _sha256_file(path)
    if actual_hash != record.sha256:
        raise HTTPException(status_code=409, detail="备份校验和不匹配")
    key = _decode_key(settings.backup_encryption_key)
    try:
        _verify_backup_payload(path, key, settings)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=f"备份恢复校验失败：{exc}") from exc
    record.last_verified_at = utcnow()
    record_event(db, member, "backup.verified", "backup", record.id)
    db.commit()
    db.refresh(record)
    prune_verified_backups(db, member, settings)
    return record


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def prune_verified_backups(
    db: Session,
    member: Member | None,
    settings: Settings,
) -> int:
    """Apply hourly/daily/monthly retention only to successfully verified backups."""
    records = db.scalars(
        select(BackupRecord)
        .where(
            BackupRecord.status == "ready",
            BackupRecord.last_verified_at.is_not(None),
        )
        .order_by(BackupRecord.created_at.desc(), BackupRecord.id.desc())
    ).all()
    if not records:
        return 0

    keep: set[str] = {record.id for record in records[: max(settings.backup_keep_hourly, 1)]}

    daily_seen: set[date] = set()
    for record in records:
        day = _aware(record.created_at).date()
        if day in daily_seen:
            continue
        if len(daily_seen) >= max(settings.backup_keep_daily, 0):
            break
        daily_seen.add(day)
        keep.add(record.id)

    monthly_seen: set[tuple[int, int]] = set()
    for record in records:
        created = _aware(record.created_at)
        month = (created.year, created.month)
        if month in monthly_seen:
            continue
        if len(monthly_seen) >= max(settings.backup_keep_monthly, 0):
            break
        monthly_seen.add(month)
        keep.add(record.id)

    removed = 0
    for record in records:
        if record.id in keep:
            continue
        try:
            path = _record_path(record, settings)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
        else:
            path.unlink(missing_ok=True)
        record_event(
            db,
            member,
            "backup.pruned",
            "backup",
            record.id,
            before={
                "created_at": _aware(record.created_at).isoformat(),
                "sha256": record.sha256,
                "size_bytes": record.size_bytes,
            },
        )
        db.delete(record)
        removed += 1
    if removed:
        db.commit()
    return removed


def get_backup_path(record: BackupRecord, settings: Settings) -> Path:
    if record.status != "ready":
        raise HTTPException(status_code=409, detail="备份尚未就绪")
    return _record_path(record, settings)
