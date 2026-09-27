from __future__ import annotations

from .config import get_settings
from .database import get_session_factory
from .services.retention import cleanup_expired_source_files


def run() -> None:
    settings = get_settings()
    with get_session_factory()() as db:
        deleted = cleanup_expired_source_files(db, settings)
    print(f"expired_source_files_deleted={deleted}")
