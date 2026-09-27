from __future__ import annotations

import os
from datetime import UTC, datetime

from celery import Celery


def _redis_url() -> str:
    value = os.environ.get("HOMEDATABASE_REDIS_URL", "redis://127.0.0.1:6379/0").strip()
    if not value.startswith(("redis://", "rediss://")):
        raise RuntimeError("HOMEDATABASE_REDIS_URL must use redis:// or rediss://")
    return value


celery_app = Celery(
    "homedatabase",
    broker=_redis_url(),
    backend=_redis_url(),
)
celery_app.conf.update(
    accept_content=["json"],
    broker_connection_retry_on_startup=True,
    broker_transport_options={"visibility_timeout": 60 * 60},
    enable_utc=True,
    result_expires=60 * 60,
    result_serializer="json",
    task_acks_late=True,
    task_default_queue="light",
    task_reject_on_worker_lost=True,
    task_routes={
        "homedatabase.deployment_health": {"queue": "heavy"},
        "homedatabase.ocr_source_file": {"queue": "ocr"},
    },
    task_serializer="json",
    timezone="UTC",
    worker_prefetch_multiplier=1,
    imports=("homedatabase_api.services.ocr_tasks",),
)


@celery_app.task(name="homedatabase.deployment_health")
def deployment_health() -> dict[str, str]:
    """Verify only broker-to-worker task transport; this is not an OCR probe."""
    return {
        "status": "ok",
        "scope": "broker-and-worker-only",
        "checkedAt": datetime.now(UTC).isoformat(),
    }
