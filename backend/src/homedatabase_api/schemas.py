from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .money import FxRate, NonNegativeMinor, PositiveMinor, SignedMinor

CurrencyCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]


class ApiModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, from_attributes=True, extra="ignore")


class MemberOut(ApiModel):
    id: str
    identity: Literal["dad", "mom", "son"]
    name: str
    username: str
    is_admin: bool = Field(alias="isAdmin")
    color: str


class LoginInput(ApiModel):
    username: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    password: Annotated[str, StringConstraints(min_length=1, max_length=1024)]


class AuthOut(ApiModel):
    member: MemberOut
    expires_at: datetime = Field(alias="expiresAt")


class WebAuthnCeremonyOut(ApiModel):
    ceremony_id: str = Field(alias="ceremonyId")
    options: dict[str, Any]


class PasskeyRegistrationFinishInput(ApiModel):
    ceremony_id: str = Field(alias="ceremonyId")
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    credential: dict[str, Any]


class PasskeyAuthenticationBeginInput(ApiModel):
    username: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class PasskeyAuthenticationFinishInput(ApiModel):
    ceremony_id: str = Field(alias="ceremonyId")
    credential: dict[str, Any]


class AccountOut(ApiModel):
    id: str
    name: str
    kind: Literal["asset", "liability"]
    scope: Literal["personal", "family"]
    owner_id: str | None = Field(alias="ownerId")
    currency: CurrencyCode
    balance_cents: SignedMinor = Field(alias="balanceCents")
    icon: str
    permission: Literal["view", "manage"]
    version: int


class AccountCreateInput(ApiModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    kind: Literal["asset", "liability"]
    scope: Literal["personal", "family"]
    currency: CurrencyCode = "CNY"
    opening_balance_cents: SignedMinor = Field(default=0, alias="openingBalanceCents")
    icon: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)] = (
        "WalletCards"
    )


class AccountUpdateInput(ApiModel):
    expected_version: int = Field(alias="expectedVersion", ge=1)
    name: Annotated[
        str | None, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)
    ] = None
    icon: Annotated[
        str | None, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)
    ] = None


class AccountGrantOut(ApiModel):
    member_id: str = Field(alias="memberId")
    permission: Literal["view", "manage"]


class AccountGrantInput(ApiModel):
    permission: Literal["view", "manage"]


class CategoryOut(ApiModel):
    id: str
    name: str
    kind: Literal["expense", "income"]
    parent_id: str | None = Field(alias="parentId")
    icon: str
    active: bool
    version: int


class CategoryCreateInput(ApiModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    kind: Literal["expense", "income"]
    parent_id: str | None = Field(default=None, alias="parentId")
    icon: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)] = (
        "Shapes"
    )


class CategoryUpdateInput(ApiModel):
    expected_version: int = Field(alias="expectedVersion", ge=1)
    name: Annotated[
        str | None, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
    ] = None
    icon: Annotated[
        str | None, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)
    ] = None
    active: bool | None = None


class EntryLineOut(ApiModel):
    account_id: str | None = Field(alias="accountId")
    ledger_code: str | None = Field(alias="ledgerCode")
    debit_cents: NonNegativeMinor = Field(alias="debitCents")
    credit_cents: NonNegativeMinor = Field(alias="creditCents")
    account_amount_cents: PositiveMinor | None = Field(alias="accountAmountCents")
    account_currency: CurrencyCode | None = Field(alias="accountCurrency")


TxnType = Literal["expense", "income", "transfer", "refund"]


class TransactionOut(ApiModel):
    id: str
    date: datetime
    type: TxnType
    payee: str
    note: str
    category_id: str | None = Field(alias="categoryId")
    currency: CurrencyCode
    base_currency: CurrencyCode = Field(alias="baseCurrency")
    fx_rate: FxRate | None = Field(default=None, alias="fxRate")
    amount_cents: PositiveMinor = Field(alias="amountCents")
    base_amount_cents: PositiveMinor = Field(alias="baseAmountCents")
    lines: list[EntryLineOut]
    source: Literal["manual", "import"]
    batch_id: str | None = Field(default=None, alias="batchId")
    refund_of: str | None = Field(default=None, alias="refundOf")
    split_id: str | None = Field(default=None, alias="splitId")
    created_by: str = Field(alias="createdBy")
    version: int
    deleted_at: datetime | None = Field(default=None, alias="deletedAt")
    deleted_by: str | None = Field(default=None, alias="deletedBy")


class NewTransactionInput(ApiModel):
    type: TxnType
    payee: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
    note: Annotated[str, StringConstraints(max_length=4000)] = ""
    category_id: str | None = Field(default=None, alias="categoryId")
    account_id: str = Field(alias="accountId")
    to_account_id: str | None = Field(default=None, alias="toAccountId")
    amount_cents: PositiveMinor = Field(alias="amountCents")
    currency: CurrencyCode = "CNY"
    date: datetime
    fx_rate: FxRate | None = Field(default=None, alias="fxRate")
    base_amount_cents: PositiveMinor | None = Field(default=None, alias="baseAmountCents")
    refund_of: str | None = Field(default=None, alias="refundOf")

    @model_validator(mode="after")
    def transaction_shape(self) -> NewTransactionInput:
        if self.type == "transfer":
            if not self.to_account_id or self.to_account_id == self.account_id:
                raise ValueError("转账必须选择不同的转入账户")
            if self.category_id is not None:
                raise ValueError("转账不能设置收支分类")
        elif self.to_account_id is not None:
            raise ValueError("只有转账可以设置转入账户")
        if self.type == "refund" and not self.refund_of:
            raise ValueError("退款必须关联原支出")
        if self.type != "refund" and self.refund_of is not None:
            raise ValueError("只有退款可以关联原支出")
        return self


class TransactionUpdateInput(ApiModel):
    expected_version: int = Field(alias="expectedVersion", ge=1)
    payee: Annotated[
        str | None, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)
    ] = None
    note: Annotated[str | None, StringConstraints(max_length=4000)] = None
    category_id: str | None = Field(default=None, alias="categoryId")


class TransactionRestoreInput(ApiModel):
    expected_version: int = Field(alias="expectedVersion", ge=1)


class ImportCandidateOut(ApiModel):
    id: str
    date: datetime
    payee: str
    direction: Literal["expense", "income"]
    amount_cents: PositiveMinor = Field(alias="amountCents")
    currency: CurrencyCode
    fx_rate: FxRate | None = Field(default=None, alias="fxRate")
    base_amount_cents: PositiveMinor | None = Field(default=None, alias="baseAmountCents")
    suggested_category_id: str | None = Field(alias="suggestedCategoryId")
    original_category: str | None = Field(alias="originalCategory")
    target_account_id: str | None = Field(alias="targetAccountId")
    duplicate_of_txn_id: str | None = Field(alias="duplicateOfTxnId")
    status: Literal["pending", "confirmed", "skipped"]
    validation_status: str = Field(alias="validationStatus")
    confidence: Decimal | None
    version: int


ImportBatchStatus = Literal[
    "queued",
    "processing",
    "reviewing",
    "needs_ocr",
    "rejected",
    "failed",
    "posted",
    "undone",
]


class SourceFileOut(ApiModel):
    id: str
    filename: str
    mime_type: str = Field(alias="mimeType")
    size_bytes: int = Field(alias="sizeBytes")
    sha256: str
    encrypted: bool
    expires_at: datetime | None = Field(alias="expiresAt")
    duplicate_of_file_id: str | None = Field(alias="duplicateOfFileId")
    parse_status: Literal[
        "processing", "parsed", "duplicate", "needs_ocr", "rejected", "failed"
    ] = Field(alias="parseStatus")
    parse_message: str = Field(alias="parseMessage")
    record_count: int = Field(alias="recordCount")
    candidate_count: int = Field(alias="candidateCount")
    rejected_count: int = Field(alias="rejectedCount")


class ImportBatchOut(ApiModel):
    id: str
    filename: str
    source: str
    imported_at: datetime = Field(alias="importedAt")
    status: ImportBatchStatus
    owner_id: str = Field(alias="ownerId")
    target_account_id: str | None = Field(alias="targetAccountId")
    candidates: list[ImportCandidateOut]
    files: list[SourceFileOut]
    job_id: str | None = Field(alias="jobId")
    progress: int = Field(ge=0, le=100)
    message: str
    file_count: int = Field(alias="fileCount")
    files_processed: int = Field(alias="filesProcessed")
    candidate_count: int = Field(alias="candidateCount")
    needs_ocr_count: int = Field(alias="needsOcrCount")
    failed_file_count: int = Field(alias="failedFileCount")
    version: int


class CandidateIdsInput(ApiModel):
    candidate_ids: list[str] = Field(alias="candidateIds", min_length=1)


class ImportCandidatePatch(ApiModel):
    expected_version: int = Field(alias="expectedVersion", ge=1)
    date: datetime | None = None
    payee: Annotated[
        str | None, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)
    ] = None
    amount_cents: PositiveMinor | None = Field(default=None, alias="amountCents")
    fx_rate: FxRate | None = Field(default=None, alias="fxRate")
    base_amount_cents: PositiveMinor | None = Field(default=None, alias="baseAmountCents")
    suggested_category_id: str | None = Field(default=None, alias="suggestedCategoryId")
    target_account_id: str | None = Field(default=None, alias="targetAccountId")


class ImportJobOut(ApiModel):
    id: str
    batch_id: str = Field(alias="batchId")
    status: ImportBatchStatus
    progress: int = Field(ge=0, le=100)
    message: str
    file_count: int = Field(alias="fileCount")
    files_processed: int = Field(alias="filesProcessed")
    candidate_count: int = Field(alias="candidateCount")
    needs_ocr_count: int = Field(alias="needsOcrCount")
    failed_file_count: int = Field(alias="failedFileCount")


class SplitShareOut(ApiModel):
    member_id: str = Field(alias="memberId")
    share_cents: PositiveMinor = Field(alias="shareCents")
    status: Literal["pending", "accepted", "returned"]
    note: str | None = None


class SplitOut(ApiModel):
    id: str
    txn_id: str = Field(alias="txnId")
    title: str
    transaction_date: datetime = Field(alias="transactionDate")
    category_id: str | None = Field(alias="categoryId")
    currency: CurrencyCode
    total_cents: PositiveMinor = Field(alias="totalCents")
    created_by: str = Field(alias="createdBy")
    created_at: datetime = Field(alias="createdAt")
    status: Literal["open", "settled", "returned"]
    shares: list[SplitShareOut]
    version: int


class NewSplitShare(ApiModel):
    member_id: str = Field(alias="memberId")
    share_cents: PositiveMinor = Field(alias="shareCents")


class NewSplitInput(ApiModel):
    txn_id: str = Field(alias="txnId")
    total_cents: PositiveMinor = Field(alias="totalCents")
    shares: list[NewSplitShare] = Field(min_length=1)

    @model_validator(mode="after")
    def shares_match_total(self) -> NewSplitInput:
        ids = [share.member_id for share in self.shares]
        if len(ids) != len(set(ids)):
            raise ValueError("同一成员不能出现两次")
        if sum(share.share_cents for share in self.shares) != self.total_cents:
            raise ValueError("分摊金额合计必须等于交易金额")
        return self


class SplitResponseInput(ApiModel):
    expected_version: int = Field(alias="expectedVersion", ge=1)
    accept: bool
    note: Annotated[str | None, StringConstraints(strip_whitespace=True, max_length=500)] = None

    @model_validator(mode="after")
    def return_requires_note(self) -> SplitResponseInput:
        if not self.accept and not self.note:
            raise ValueError("退回分摊时必须填写原因")
        return self


class SplitShareUpdateInput(ApiModel):
    expected_version: int = Field(alias="expectedVersion", ge=1)
    share_cents: PositiveMinor = Field(alias="shareCents")


class BackupLocationOut(ApiModel):
    kind: Literal["tencent", "local", "overseas"]
    status: Literal["ok", "syncing", "failed"]


class BackupRecordOut(ApiModel):
    id: str
    created_at: datetime = Field(alias="createdAt")
    size_mb: float = Field(alias="sizeMb")
    encrypted: Literal[True]
    status: Literal["creating", "ready", "failed"]
    sha256: str | None
    locations: list[BackupLocationOut]
    last_verified_at: datetime | None = Field(alias="lastVerifiedAt")


class PasskeyOut(ApiModel):
    id: str
    name: str
    created_at: datetime = Field(alias="createdAt")
    last_used_at: datetime = Field(alias="lastUsedAt")


class ReportTierOut(ApiModel):
    tier: Literal[1, 2, 3, 4, 5]
    name: str
    audience: str
    description: str


class HealthOut(ApiModel):
    status: Literal["ok"]
    database: Literal["ok"]
    version: str


class DeleteInput(ApiModel):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class PeriodLockInput(ApiModel):
    starts_on: date = Field(alias="startsOn")
    ends_on: date = Field(alias="endsOn")
    reason: str = "月度核对完成"

    @model_validator(mode="after")
    def valid_range(self) -> PeriodLockInput:
        if self.starts_on > self.ends_on:
            raise ValueError("锁定开始日期不能晚于结束日期")
        return self


class PeriodLockOut(ApiModel):
    id: str
    starts_on: date = Field(alias="startsOn")
    ends_on: date = Field(alias="endsOn")
    locked_by: str = Field(alias="lockedBy")
    reason: str
    created_at: datetime = Field(alias="createdAt")
