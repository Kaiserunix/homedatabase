from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Event
from time import monotonic

from fastapi import HTTPException
from sqlalchemy import text

from homedatabase_api.config import get_settings
from homedatabase_api.database import get_session_factory
from homedatabase_api.services.backups import backup_creation_lock

WAIT_SECONDS = 15
MAX_CONFLICT_SECONDS = 5


@dataclass(frozen=True)
class LockOutcome:
    status_code: int
    detail: str
    conflict_seconds: float
    reacquired: bool


def _hold_lock(holding: Event, release: Event) -> None:
    with get_session_factory()() as db:
        with backup_creation_lock(db):
            holding.set()
            if not release.wait(WAIT_SECONDS):
                raise TimeoutError("timed out waiting to release holder lock")


def _contend_then_retry(holding: Event, conflicted: Event, retry: Event) -> LockOutcome:
    if not holding.wait(WAIT_SECONDS):
        raise TimeoutError("holder never acquired the backup lock")

    with get_session_factory()() as db:
        started = monotonic()
        try:
            with backup_creation_lock(db):
                raise AssertionError("contender acquired the lock while holder still owned it")
        except HTTPException as exc:
            elapsed = monotonic() - started
            if exc.status_code != 409:
                raise AssertionError(f"expected HTTP 409, got {exc.status_code}") from exc
            if exc.detail != "已有数据库备份正在创建":
                raise AssertionError(f"unexpected conflict detail: {exc.detail!r}") from exc
            if elapsed > MAX_CONFLICT_SECONDS:
                raise AssertionError(f"nonblocking lock conflict took {elapsed:.3f}s") from exc

        conflicted.set()
        if not retry.wait(WAIT_SECONDS):
            raise TimeoutError("contender was not released for its retry")
        with backup_creation_lock(db):
            reacquired = True

    return LockOutcome(
        status_code=409,
        detail="已有数据库备份正在创建",
        conflict_seconds=elapsed,
        reacquired=reacquired,
    )


def _postgres_major_version() -> int:
    settings = get_settings()
    if not settings.database_url.startswith("postgresql"):
        raise RuntimeError("this backup-lock verification requires PostgreSQL")
    with get_session_factory()() as db:
        if db.get_bind().dialect.name != "postgresql":
            raise RuntimeError("database dialect is not PostgreSQL")
        version_number = int(db.scalar(text("SHOW server_version_num")) or 0)
    major = version_number // 10_000
    if major != 17:
        raise RuntimeError(f"PostgreSQL 17 is required, found major version {major}")
    return major


def main() -> None:
    major = _postgres_major_version()
    holding = Event()
    release = Event()
    conflicted = Event()
    retry = Event()

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="backup-lock-ci") as pool:
        holder = pool.submit(_hold_lock, holding, release)
        contender = pool.submit(_contend_then_retry, holding, conflicted, retry)
        try:
            if not conflicted.wait(WAIT_SECONDS):
                raise TimeoutError("contender did not report the expected conflict")
            release.set()
            holder.result(timeout=WAIT_SECONDS)
            retry.set()
            outcome = contender.result(timeout=WAIT_SECONDS)
        finally:
            release.set()
            retry.set()

    if not outcome.reacquired:
        raise AssertionError("contender did not acquire the lock after holder released it")
    print(
        f"PostgreSQL {major} backup advisory lock verified: "
        f"conflict={outcome.status_code}, elapsed={outcome.conflict_seconds:.3f}s, "
        "reacquired=true"
    )


if __name__ == "__main__":
    main()
