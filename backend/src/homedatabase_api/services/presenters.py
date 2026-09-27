from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    Account,
    BackupRecord,
    ImportBatch,
    ImportCandidate,
    Passkey,
    Split,
    Transaction,
)
from ..schemas import (
    AccountOut,
    BackupRecordOut,
    EntryLineOut,
    ImportBatchOut,
    ImportCandidateOut,
    ImportJobOut,
    PasskeyOut,
    SourceFileOut,
    SplitOut,
    SplitShareOut,
    TransactionOut,
)
from .permissions import account_permission, can_view_transaction, transaction_has_hidden_accounts


def present_account(db: Session, member, account: Account, balance: int) -> AccountOut:
    permission = account_permission(db, member, account)
    if permission is None:
        raise ValueError("account is not visible")
    return AccountOut(
        id=account.id,
        name=account.name,
        kind=account.kind,
        scope=account.scope,
        ownerId=account.owner_id,
        currency=account.currency,
        balanceCents=balance,
        icon=account.icon,
        permission=permission,
        version=account.version,
    )


def present_transaction(db: Session, member, transaction: Transaction) -> TransactionOut:
    split_id = db.scalar(select(Split.id).where(Split.transaction_id == transaction.id))
    if not can_view_transaction(db, member, transaction):
        raise ValueError("transaction is not visible")
    partial = transaction_has_hidden_accounts(db, member, transaction)
    lines = []
    for line in transaction.lines:
        account = db.get(Account, line.account_id) if line.account_id else None
        visible_account = bool(account and account_permission(db, member, account) is not None)
        if line.account_id and not visible_account:
            lines.append(
                EntryLineOut(
                    accountId="private:counterparty",
                    ledgerCode=None,
                    debitCents=line.debit_minor,
                    creditCents=line.credit_minor,
                    accountAmountCents=None,
                    accountCurrency=None,
                )
            )
        else:
            lines.append(
                EntryLineOut(
                    accountId=line.account_id,
                    ledgerCode=line.ledger_code,
                    debitCents=line.debit_minor,
                    creditCents=line.credit_minor,
                    accountAmountCents=line.account_amount_minor,
                    accountCurrency=account.currency if account else None,
                )
            )
    return TransactionOut(
        id=transaction.id,
        date=transaction.occurred_at,
        type=transaction.type,
        payee="私人账户往来（已脱敏）" if partial else transaction.payee,
        note="" if partial else transaction.note,
        categoryId=transaction.category_id,
        currency=transaction.currency,
        baseCurrency=transaction.base_currency,
        fxRate=transaction.fx_rate,
        amountCents=transaction.amount_minor,
        baseAmountCents=transaction.base_amount_minor,
        lines=lines,
        source=transaction.source,
        batchId=None if partial else transaction.batch_id,
        refundOf=None if partial else transaction.refund_of_id,
        splitId=split_id,
        createdBy=transaction.created_by,
        version=transaction.version,
        deletedAt=transaction.deleted_at,
        deletedBy=transaction.deleted_by,
    )


def present_candidate(candidate: ImportCandidate) -> ImportCandidateOut:
    return ImportCandidateOut(
        id=candidate.id,
        date=candidate.date,
        payee=candidate.payee,
        direction=candidate.direction,
        amountCents=candidate.amount_minor,
        currency=candidate.currency,
        fxRate=candidate.fx_rate,
        baseAmountCents=candidate.base_amount_minor,
        suggestedCategoryId=candidate.suggested_category_id,
        originalCategory=candidate.original_category,
        targetAccountId=candidate.target_account_id,
        duplicateOfTxnId=candidate.duplicate_of_txn_id,
        status=candidate.status,
        validationStatus=candidate.validation_status,
        confidence=candidate.confidence,
        version=candidate.version,
    )


def present_source_file(source_file) -> SourceFileOut:
    return SourceFileOut(
        id=source_file.id,
        filename=source_file.filename,
        mimeType=source_file.mime_type,
        sizeBytes=source_file.size_bytes,
        sha256=source_file.sha256,
        encrypted=bool(source_file.encrypted_path or source_file.duplicate_of_file_id),
        expiresAt=source_file.expires_at,
        duplicateOfFileId=source_file.duplicate_of_file_id,
        parseStatus=source_file.parse_status,
        parseMessage=source_file.parse_message,
        recordCount=source_file.record_count,
        candidateCount=source_file.candidate_count,
        rejectedCount=source_file.rejected_count,
    )


def _import_counts(batch: ImportBatch) -> dict[str, int]:
    return {
        "file_count": len(batch.files),
        "files_processed": sum(item.parse_status != "processing" for item in batch.files),
        "candidate_count": len(batch.candidates),
        "needs_ocr_count": sum(item.parse_status == "needs_ocr" for item in batch.files),
        "failed_file_count": sum(item.parse_status == "failed" for item in batch.files),
    }


def present_batch(batch: ImportBatch) -> ImportBatchOut:
    counts = _import_counts(batch)
    return ImportBatchOut(
        id=batch.id,
        filename=batch.filename,
        source=batch.source,
        importedAt=batch.imported_at,
        status=batch.status,
        ownerId=batch.owner_id,
        targetAccountId=batch.target_account_id,
        candidates=[present_candidate(candidate) for candidate in batch.candidates],
        files=[present_source_file(source_file) for source_file in batch.files],
        jobId=batch.job_id,
        progress=batch.job_progress,
        message=batch.job_message,
        fileCount=counts["file_count"],
        filesProcessed=counts["files_processed"],
        candidateCount=counts["candidate_count"],
        needsOcrCount=counts["needs_ocr_count"],
        failedFileCount=counts["failed_file_count"],
        version=batch.version,
    )


def present_import_job(batch: ImportBatch) -> ImportJobOut:
    counts = _import_counts(batch)
    return ImportJobOut(
        id=batch.job_id or batch.id,
        batchId=batch.id,
        status=batch.status,
        progress=batch.job_progress,
        message=batch.job_message,
        fileCount=counts["file_count"],
        filesProcessed=counts["files_processed"],
        candidateCount=counts["candidate_count"],
        needsOcrCount=counts["needs_ocr_count"],
        failedFileCount=counts["failed_file_count"],
    )


def present_split(db: Session, split: Split) -> SplitOut:
    transaction = db.get(Transaction, split.transaction_id)
    if transaction is None:
        raise ValueError("split transaction missing")
    return SplitOut(
        id=split.id,
        txnId=transaction.id,
        title=transaction.payee,
        transactionDate=transaction.occurred_at,
        categoryId=transaction.category_id,
        currency=transaction.currency,
        totalCents=transaction.amount_minor,
        createdBy=split.created_by,
        createdAt=split.created_at,
        status=split.status,
        shares=[
            SplitShareOut(
                memberId=share.member_id,
                shareCents=share.share_minor,
                status=share.status,
                note=share.note,
            )
            for share in split.shares
        ],
        version=split.version,
    )


def present_backup(record: BackupRecord) -> BackupRecordOut:
    return BackupRecordOut(
        id=record.id,
        createdAt=record.created_at,
        sizeMb=round(record.size_bytes / (1024 * 1024), 2),
        encrypted=True,
        status=record.status,
        sha256=record.sha256,
        locations=record.locations,
        lastVerifiedAt=record.last_verified_at,
    )


def present_passkey(passkey: Passkey) -> PasskeyOut:
    return PasskeyOut(
        id=passkey.id,
        name=passkey.name,
        createdAt=passkey.created_at,
        lastUsedAt=passkey.last_used_at,
    )
