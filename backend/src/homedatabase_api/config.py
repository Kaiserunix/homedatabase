from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HOMEDATABASE_",
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Literal["local", "test", "production"] = "local"
    database_url: str = "sqlite+pysqlite:///./backend/data/homedatabase-dev.sqlite3"
    database_echo: bool = False
    auto_create_schema: bool = True
    seed_demo: bool = False
    bootstrap_password: str | None = None

    api_prefix: str = "/api"
    # V1 has one immutable household base currency.  Making this an arbitrary
    # environment toggle could mix CNY and another base in one existing ledger
    # after a restart, so changing it requires an explicit data migration.
    base_currency: Literal["CNY"] = "CNY"
    dev_member_id: str | None = "m-son"

    session_cookie_name: str = "hd_session"
    session_cookie_secure: bool = False
    session_ttl_seconds: int = 60 * 60 * 24 * 30

    webauthn_rp_id: str = "localhost"
    webauthn_rp_name: str = "HomeDatabase"
    webauthn_origins: list[str] = [
        "http://localhost:5200",
        "http://127.0.0.1:5200",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]
    allowed_hosts: list[str] = ["localhost", "127.0.0.1", "testserver"]
    webauthn_challenge_ttl_seconds: int = 300

    redis_url: str = "redis://127.0.0.1:6379/0"
    task_always_eager: bool = False
    auth_rate_limit_attempts: int = Field(default=10, ge=1)
    auth_rate_limit_ip_attempts: int = Field(default=30, ge=1)
    auth_rate_limit_subject_attempts: int = Field(default=50, ge=1)
    auth_rate_limit_window_seconds: int = Field(default=60, ge=1)
    max_json_body_bytes: int = 1024 * 1024
    source_archive_dir: Path = Path("backend/data/source-files")
    source_retention_days: int = 90
    source_encryption_key: str | None = None
    source_max_files: int = 10
    source_max_file_bytes: int = 25 * 1024 * 1024
    source_max_total_bytes: int = 100 * 1024 * 1024
    source_max_rows: int = 50_000
    source_max_uncompressed_bytes: int = 100 * 1024 * 1024
    ocr_enabled: bool = False
    ocr_jobs_dir: Path = Path("backend/data/ocr-jobs")
    ocr_timeout_seconds: int = Field(default=600, ge=30, le=1800)
    # The total document limit is separate from the number of pages sent to a
    # single Codex invocation.  Large statements are processed sequentially so
    # rendered pages do not accumulate in the shared tmpfs.
    ocr_max_pages: int = Field(default=200, ge=1, le=500)
    ocr_pages_per_job: int = Field(default=10, ge=1, le=50)
    # This is the whole-document cap.  The structured Codex contract still
    # limits every individual page chunk to at most 500 transactions.
    ocr_max_transactions: int = Field(default=5_000, ge=1, le=50_000)
    ocr_max_image_pixels: int = Field(default=20_000_000, ge=1_000_000)
    ocr_runner_poll_seconds: float = Field(default=0.5, ge=0.1, le=10)
    ocr_job_stale_seconds: int = Field(default=3600, ge=600)
    backup_dir: Path = Path("backend/data/backups")
    backup_encryption_key: str | None = None
    backup_location_kind: Literal["tencent", "local", "overseas"] = "tencent"
    backup_keep_hourly: int = Field(default=48, ge=1)
    backup_keep_daily: int = Field(default=30, ge=0)
    backup_keep_monthly: int = Field(default=12, ge=0)
    backup_min_free_bytes: int = Field(default=512 * 1024 * 1024, ge=0)
    backup_orphan_grace_seconds: int = Field(default=300, ge=60)
    pg_dump_path: str = "pg_dump"
    pg_restore_path: str = "pg_restore"

    @model_validator(mode="after")
    def production_safety(self) -> Settings:
        if self.environment != "production":
            return self
        failures: list[str] = []
        if self.database_url.startswith("sqlite"):
            failures.append("production requires PostgreSQL")
        if self.dev_member_id:
            failures.append("HOMEDATABASE_DEV_MEMBER_ID must be empty in production")
        if not self.session_cookie_secure:
            failures.append("production requires Secure session cookies")
        if self.auto_create_schema:
            failures.append("production schema must be managed by Alembic")
        if self.seed_demo:
            failures.append("production forbids demo seeding")
        if self.bootstrap_password:
            failures.append("production forbids HOMEDATABASE_BOOTSTRAP_PASSWORD")
        if self.webauthn_rp_id in {"localhost", "127.0.0.1"}:
            failures.append("production requires a real WebAuthn RP ID")
        if not self.webauthn_origins or any(
            not origin.startswith("https://") for origin in self.webauthn_origins
        ):
            failures.append("production WebAuthn origins must use HTTPS")
        if not self.allowed_hosts or "*" in self.allowed_hosts:
            failures.append("production requires explicit allowed hosts")
        if not self.source_encryption_key:
            failures.append("production requires HOMEDATABASE_SOURCE_ENCRYPTION_KEY")
        if not self.backup_encryption_key:
            failures.append("production requires HOMEDATABASE_BACKUP_ENCRYPTION_KEY")
        if failures:
            raise ValueError("; ".join(failures))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
