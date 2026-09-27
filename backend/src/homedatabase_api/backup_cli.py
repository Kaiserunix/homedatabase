from __future__ import annotations

from .config import get_settings
from .database import get_session_factory
from .services.backups import create_backup


def run() -> None:
    settings = get_settings()
    with get_session_factory()() as db:
        record = create_backup(db, None, settings)
    print(
        f"backup={record.id} sha256={record.sha256} "
        f"size={record.size_bytes} verified={record.last_verified_at.isoformat()}"
    )
