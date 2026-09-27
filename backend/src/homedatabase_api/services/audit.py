from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..models import AuditEvent, Member


def record_event(
    db: Session,
    actor: Member | None,
    action: str,
    entity_type: str,
    entity_id: str,
    *,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    db.add(
        AuditEvent(
            actor_id=actor.id if actor else None,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            before=before,
            after=after,
        )
    )
