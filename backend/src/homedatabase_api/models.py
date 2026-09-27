from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from .database import Base
from .money import MAX_SAFE_MINOR


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Store UTC and always restore an aware value, including on SQLite.

    PostgreSQL preserves timezone awareness for ``TIMESTAMP WITH TIME ZONE``.
    SQLite does not, so its normal ``DateTime(timezone=True)`` result is naive.
    Treating that result as local time shifts imported transactions a second
    time when they are confirmed.  The database representation remains the
    same; this adapter only makes the UTC contract explicit at the ORM edge.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        value = value.astimezone(UTC)
        if dialect.name == "sqlite":
            return value.replace(tzinfo=None)
        return value

    def process_result_value(self, value: datetime | None, _dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class Member(Base):
    __tablename__ = "members"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    identity: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    username: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    color: Mapped[str] = mapped_column(String(64), nullable=False, default="bg-slate-400")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    __table_args__ = (
        CheckConstraint("identity IN ('dad','mom','son')", name="ck_members_identity"),
    )


class LoginSession(Base):
    __tablename__ = "login_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("sess"))
    member_id: Mapped[str] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_used_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    user_agent: Mapped[str | None] = mapped_column(String(300))

    member: Mapped[Member] = relationship()


class Passkey(Base):
    __tablename__ = "passkeys"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("pk"))
    member_id: Mapped[str] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    credential_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    public_key: Mapped[str] = mapped_column(Text, nullable=False)
    sign_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    transports: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    device_type: Mapped[str | None] = mapped_column(String(32))
    backed_up: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    last_used_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class WebAuthnChallenge(Base):
    __tablename__ = "webauthn_challenges"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: new_id("ceremony")
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    member_id: Mapped[str] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), index=True)
    challenge: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, index=True)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    __table_args__ = (
        CheckConstraint("kind IN ('registration','authentication')", name="ck_webauthn_kind"),
    )


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("acct"))
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    owner_id: Mapped[str | None] = mapped_column(
        ForeignKey("members.id", ondelete="RESTRICT"), index=True
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="CNY")
    opening_balance_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    icon: Mapped[str] = mapped_column(String(80), nullable=False, default="WalletCards")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    __table_args__ = (
        CheckConstraint("kind IN ('asset','liability')", name="ck_accounts_kind"),
        CheckConstraint("scope IN ('personal','family')", name="ck_accounts_scope"),
        CheckConstraint(
            "(scope = 'personal' AND owner_id IS NOT NULL) OR (scope = 'family')",
            name="ck_accounts_owner",
        ),
        CheckConstraint(
            f"opening_balance_minor BETWEEN -{MAX_SAFE_MINOR} AND {MAX_SAFE_MINOR}",
            name="ck_accounts_opening_balance_safe",
        ),
    )


class AccountGrant(Base):
    __tablename__ = "account_grants"

    account_id: Mapped[str] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True
    )
    member_id: Mapped[str] = mapped_column(
        ForeignKey("members.id", ondelete="CASCADE"), primary_key=True
    )
    permission: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    __table_args__ = (
        CheckConstraint("permission IN ('view','manage')", name="ck_account_grants_permission"),
    )


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("cat"))
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("categories.id", ondelete="RESTRICT"))
    icon: Mapped[str] = mapped_column(String(80), nullable=False, default="Shapes")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (
        CheckConstraint("kind IN ('expense','income')", name="ck_categories_kind"),
        Index(
            "uq_categories_sibling_name",
            "kind",
            func.coalesce(parent_id, ""),
            "name",
            unique=True,
        ),
    )


class PeriodLock(Base):
    __tablename__ = "period_locks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("lock"))
    starts_on: Mapped[date] = mapped_column(Date, nullable=False)
    ends_on: Mapped[date] = mapped_column(Date, nullable=False)
    locked_by: Mapped[str] = mapped_column(ForeignKey("members.id", ondelete="RESTRICT"))
    reason: Mapped[str] = mapped_column(String(300), nullable=False, default="月度核对完成")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    __table_args__ = (CheckConstraint("starts_on <= ends_on", name="ck_period_lock_range"),)


class ImportBatch(Base):
    __tablename__ = "import_batches"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("batch"))
    owner_id: Mapped[str] = mapped_column(ForeignKey("members.id", ondelete="RESTRICT"), index=True)
    target_account_id: Mapped[str | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT"), index=True
    )
    filename: Mapped[str] = mapped_column(String(300), nullable=False)
    source: Mapped[str] = mapped_column(String(80), nullable=False)
    imported_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="processing")
    job_id: Mapped[str | None] = mapped_column(String(80), index=True)
    job_progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    job_message: Mapped[str] = mapped_column(String(500), nullable=False, default="等待处理")
    processed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    candidates: Mapped[list[ImportCandidate]] = relationship(
        back_populates="batch", cascade="all, delete-orphan", order_by="ImportCandidate.date"
    )
    files: Mapped[list[SourceFile]] = relationship(
        back_populates="batch", cascade="all, delete-orphan", order_by="SourceFile.id"
    )

    __table_args__ = (
        CheckConstraint(
            "status IN "
            "('queued','processing','reviewing','needs_ocr','rejected','failed','posted','undone')",
            name="ck_import_batches_status",
        ),
        CheckConstraint(
            "job_progress >= 0 AND job_progress <= 100", name="ck_import_batches_progress"
        ),
    )


class SourceFile(Base):
    __tablename__ = "source_files"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("file"))
    batch_id: Mapped[str] = mapped_column(
        ForeignKey("import_batches.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(300), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    encrypted_path: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    duplicate_of_file_id: Mapped[str | None] = mapped_column(
        ForeignKey("source_files.id", ondelete="SET NULL"), index=True
    )
    parse_status: Mapped[str] = mapped_column(String(24), nullable=False, default="processing")
    parse_message: Mapped[str] = mapped_column(String(500), nullable=False, default="等待解析")
    record_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rejected_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    batch: Mapped[ImportBatch] = relationship(back_populates="files")

    __table_args__ = (
        CheckConstraint(
            "parse_status IN ('processing','parsed','duplicate','needs_ocr','rejected','failed')",
            name="ck_source_files_parse_status",
        ),
        CheckConstraint(
            "record_count >= 0 AND candidate_count >= 0 AND rejected_count >= 0",
            name="ck_source_files_counts",
        ),
    )


class SourceRecord(Base):
    __tablename__ = "source_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("src"))
    file_id: Mapped[str] = mapped_column(
        ForeignKey("source_files.id", ondelete="CASCADE"), index=True
    )
    source_ref: Mapped[str | None] = mapped_column(String(300))
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    linked_transaction_id: Mapped[str | None] = mapped_column(
        ForeignKey("transactions.id", ondelete="SET NULL"), index=True
    )


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("txn"))
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    payee: Mapped[str] = mapped_column(String(300), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    category_id: Mapped[str | None] = mapped_column(
        ForeignKey("categories.id", ondelete="RESTRICT")
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False, default="CNY")
    base_amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fx_rate: Mapped[Decimal | None] = mapped_column(Numeric(20, 10))
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    batch_id: Mapped[str | None] = mapped_column(
        ForeignKey("import_batches.id", ondelete="SET NULL")
    )
    refund_of_id: Mapped[str | None] = mapped_column(
        ForeignKey("transactions.id", ondelete="RESTRICT")
    )
    created_by: Mapped[str] = mapped_column(
        ForeignKey("members.id", ondelete="RESTRICT"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)
    deleted_by: Mapped[str | None] = mapped_column(ForeignKey("members.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)

    lines: Mapped[list[EntryLine]] = relationship(
        back_populates="transaction", cascade="all, delete-orphan", order_by="EntryLine.id"
    )

    __table_args__ = (
        CheckConstraint(
            "type IN ('expense','income','transfer','refund')", name="ck_transactions_type"
        ),
        CheckConstraint("amount_minor > 0", name="ck_transactions_amount"),
        CheckConstraint("base_amount_minor > 0", name="ck_transactions_base_amount"),
        CheckConstraint(f"amount_minor <= {MAX_SAFE_MINOR}", name="ck_transactions_amount_safe"),
        CheckConstraint(
            f"base_amount_minor <= {MAX_SAFE_MINOR}",
            name="ck_transactions_base_amount_safe",
        ),
        CheckConstraint("source IN ('manual','import')", name="ck_transactions_source"),
        Index("ix_transactions_active_date", "deleted_at", "occurred_at"),
    )


class EntryLine(Base):
    __tablename__ = "entry_lines"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("line"))
    transaction_id: Mapped[str] = mapped_column(
        ForeignKey("transactions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    account_id: Mapped[str | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT"), index=True
    )
    ledger_code: Mapped[str | None] = mapped_column(String(160), index=True)
    debit_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    credit_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    # Debit/credit are always expressed in the household base currency so the
    # journal can be balanced. Account balances, however, must move in the
    # account's own currency. Ledger-code lines therefore leave this NULL.
    account_amount_minor: Mapped[int | None] = mapped_column(BigInteger)

    transaction: Mapped[Transaction] = relationship(back_populates="lines")

    __table_args__ = (
        CheckConstraint(
            "debit_minor >= 0 AND credit_minor >= 0", name="ck_entry_lines_nonnegative"
        ),
        CheckConstraint(
            f"debit_minor <= {MAX_SAFE_MINOR} AND credit_minor <= {MAX_SAFE_MINOR}",
            name="ck_entry_lines_amounts_safe",
        ),
        CheckConstraint(
            "(debit_minor > 0 AND credit_minor = 0) OR (credit_minor > 0 AND debit_minor = 0)",
            name="ck_entry_lines_one_side",
        ),
        CheckConstraint(
            "(account_id IS NOT NULL AND ledger_code IS NULL) OR "
            "(account_id IS NULL AND ledger_code IS NOT NULL)",
            name="ck_entry_lines_target",
        ),
        CheckConstraint(
            "(account_id IS NOT NULL AND account_amount_minor > 0) OR "
            "(account_id IS NULL AND account_amount_minor IS NULL)",
            name="ck_entry_lines_account_amount",
        ),
        CheckConstraint(
            f"account_amount_minor IS NULL OR account_amount_minor <= {MAX_SAFE_MINOR}",
            name="ck_entry_lines_account_amount_safe",
        ),
    )


class ImportCandidate(Base):
    __tablename__ = "import_candidates"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("cand"))
    batch_id: Mapped[str] = mapped_column(
        ForeignKey("import_batches.id", ondelete="CASCADE"), index=True
    )
    source_record_id: Mapped[str | None] = mapped_column(
        ForeignKey("source_records.id", ondelete="SET NULL"), index=True
    )
    date: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    payee: Mapped[str] = mapped_column(String(300), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="CNY")
    fx_rate: Mapped[Decimal | None] = mapped_column(Numeric(20, 10))
    base_amount_minor: Mapped[int | None] = mapped_column(BigInteger)
    suggested_category_id: Mapped[str | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL")
    )
    original_category: Mapped[str | None] = mapped_column(String(160))
    target_account_id: Mapped[str | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT"), index=True
    )
    duplicate_of_txn_id: Mapped[str | None] = mapped_column(
        ForeignKey("transactions.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    validation_status: Mapped[str] = mapped_column(String(24), nullable=False, default="valid")
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    transaction_id: Mapped[str | None] = mapped_column(
        ForeignKey("transactions.id", ondelete="SET NULL"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    batch: Mapped[ImportBatch] = relationship(back_populates="candidates")

    __table_args__ = (
        CheckConstraint("direction IN ('expense','income')", name="ck_import_candidates_direction"),
        CheckConstraint("amount_minor > 0", name="ck_import_candidates_amount"),
        CheckConstraint(
            f"amount_minor <= {MAX_SAFE_MINOR}",
            name="ck_import_candidates_amount_safe",
        ),
        CheckConstraint("fx_rate IS NULL OR fx_rate > 0", name="ck_import_candidates_fx_rate"),
        CheckConstraint(
            "base_amount_minor IS NULL OR base_amount_minor > 0",
            name="ck_import_candidates_base_amount",
        ),
        CheckConstraint(
            f"base_amount_minor IS NULL OR base_amount_minor <= {MAX_SAFE_MINOR}",
            name="ck_import_candidates_base_amount_safe",
        ),
        CheckConstraint(
            "status IN ('pending','confirmed','skipped')", name="ck_import_candidates_status"
        ),
    )


class Split(Base):
    __tablename__ = "splits"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("split"))
    transaction_id: Mapped[str] = mapped_column(
        ForeignKey("transactions.id", ondelete="RESTRICT"), unique=True, index=True
    )
    created_by: Mapped[str] = mapped_column(
        ForeignKey("members.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)

    shares: Mapped[list[SplitShare]] = relationship(
        back_populates="split", cascade="all, delete-orphan", order_by="SplitShare.member_id"
    )

    __table_args__ = (
        CheckConstraint("status IN ('open','settled','returned')", name="ck_splits_status"),
    )


class SplitShare(Base):
    __tablename__ = "split_shares"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("share"))
    split_id: Mapped[str] = mapped_column(ForeignKey("splits.id", ondelete="CASCADE"), index=True)
    member_id: Mapped[str] = mapped_column(
        ForeignKey("members.id", ondelete="RESTRICT"), index=True
    )
    share_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    note: Mapped[str | None] = mapped_column(Text)
    accepted_version: Mapped[int | None] = mapped_column(Integer)
    responded_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    split: Mapped[Split] = relationship(back_populates="shares")

    __table_args__ = (
        UniqueConstraint("split_id", "member_id", name="uq_split_share_member"),
        CheckConstraint("share_minor > 0", name="ck_split_shares_amount"),
        CheckConstraint(f"share_minor <= {MAX_SAFE_MINOR}", name="ck_split_shares_amount_safe"),
        CheckConstraint(
            "status IN ('pending','accepted','returned')", name="ck_split_shares_status"
        ),
    )


class BackupRecord(Base):
    __tablename__ = "backup_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("backup"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    encrypted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="creating")
    sha256: Mapped[str | None] = mapped_column(String(64))
    encrypted_path: Mapped[str | None] = mapped_column(Text)
    locations: Mapped[list[dict[str, str]]] = mapped_column(JSON, nullable=False, default=list)
    last_verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    __table_args__ = (
        CheckConstraint("status IN ('creating','ready','failed')", name="ck_backup_status"),
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("audit"))
    actor_id: Mapped[str | None] = mapped_column(
        ForeignKey("members.id", ondelete="SET NULL"), index=True
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
