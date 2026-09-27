from __future__ import annotations

import tempfile
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from fastapi import HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..config import Settings, get_settings
from ..models import (
    Account,
    Category,
    ImportBatch,
    ImportCandidate,
    Member,
    SourceFile,
    SourceRecord,
    Split,
    Transaction,
    new_id,
    utcnow,
)
from ..money import calculate_base_minor
from ..schemas import ImportCandidatePatch, NewTransactionInput
from .audit import record_event
from .import_parsers import normalise_to_utc, parse_import_file
from .ledger import create_transaction, period_is_locked, validate_projected_account_balances
from .locking import acquire_ledger_mutation_guard
from .permissions import (
    account_permission,
    can_manage_transaction,
    can_view_transaction,
    require_account_permission,
)
from .presenters import present_batch, present_candidate, present_import_job
from .source_storage import encrypt_staged_file, remove_encrypted_file, stage_upload


def _can_manage_batch(db: Session, member: Member, batch: ImportBatch) -> bool:
    if batch.target_account_id:
        account = db.get(Account, batch.target_account_id)
        return bool(account and account_permission(db, member, account) == "manage")
    return batch.owner_id == member.id


def get_batch(
    db: Session,
    member: Member,
    batch_id: str,
    *,
    lock_for_update: bool = False,
) -> ImportBatch:
    query = (
        select(ImportBatch)
        .where(ImportBatch.id == batch_id)
        .options(selectinload(ImportBatch.candidates), selectinload(ImportBatch.files))
    )
    if lock_for_update:
        # Serialize every mutation of one import batch.  In PostgreSQL this
        # prevents two confirm requests from both observing the same candidate
        # as pending and creating duplicate ledger transactions.  SQLite
        # ignores FOR UPDATE, which is acceptable for the single-process test
        # database; production is required to use PostgreSQL.
        query = query.with_for_update()
    batch = db.scalar(query)
    if not batch:
        raise HTTPException(status_code=404, detail="导入批次不存在")
    if not _can_manage_batch(db, member, batch):
        raise HTTPException(status_code=403, detail="无权查看此导入批次")
    return batch


def list_batches(db: Session, member: Member):
    batches = db.scalars(
        select(ImportBatch)
        .options(selectinload(ImportBatch.candidates), selectinload(ImportBatch.files))
        .order_by(ImportBatch.imported_at.desc())
    ).all()
    return [present_batch(batch) for batch in batches if _can_manage_batch(db, member, batch)]


async def create_import_batch(
    db: Session,
    member: Member,
    uploads: list[UploadFile],
    *,
    source: str,
    target_account_id: str | None,
    settings: Settings,
):
    if not uploads:
        raise HTTPException(status_code=422, detail="至少选择一个文件")
    if len(uploads) > settings.source_max_files:
        raise HTTPException(
            status_code=413,
            detail=f"一次最多上传 {settings.source_max_files} 个文件",
        )
    source = source.strip() or "unknown"
    if len(source) > 80:
        raise HTTPException(status_code=422, detail="来源名称不能超过 80 个字符")
    if target_account_id:
        require_account_permission(db, member, target_account_id, manage=True)

    encrypted_paths: list[Path] = []
    ocr_source_file_ids: list[str] = []
    with tempfile.TemporaryDirectory(prefix="homedatabase-import-") as temporary:
        temporary_dir = Path(temporary)
        staged = []
        remaining = settings.source_max_total_bytes
        for upload in uploads:
            item = await stage_upload(
                upload,
                temporary_dir,
                max_bytes=settings.source_max_file_bytes,
                remaining_total_bytes=remaining,
            )
            staged.append(item)
            remaining -= item.size_bytes

        filenames = [item.filename for item in staged]
        batch = ImportBatch(
            owner_id=member.id,
            target_account_id=target_account_id,
            filename=_batch_filename(filenames),
            source=source,
            status="processing",
            job_id=new_id("job"),
            job_progress=5,
            job_message="文件已接收，正在加密和解析",
        )
        db.add(batch)
        db.flush()
        categories = _category_lookup(db)
        seen_hashes: dict[str, str | None] = {}
        try:
            for index, item in enumerate(staged, start=1):
                expires_at = utcnow() + timedelta(days=settings.source_retention_days)
                duplicate = _find_duplicate_source_file(db, member, item.sha256, settings)
                source_file = SourceFile(
                    id=new_id("file"),
                    batch=batch,
                    filename=item.filename,
                    sha256=item.sha256,
                    mime_type=item.mime_type,
                    size_bytes=item.size_bytes,
                    expires_at=expires_at,
                )
                db.add(source_file)
                # SourceRecord only stores a scalar foreign key, so make the parent
                # durable in the current transaction before parser rows are added.
                db.flush([source_file])
                if duplicate is not None:
                    source_file.duplicate_of_file_id = duplicate.id
                    source_file.parse_status = "duplicate"
                    source_file.parse_message = "与本人此前上传的源文件哈希完全相同，已阻止重复解析"
                    duplicate.expires_at = expires_at
                else:
                    encrypted = encrypt_staged_file(
                        item.path,
                        source_file_id=source_file.id,
                        settings=settings,
                    )
                    encrypted_paths.append(encrypted)
                    source_file.encrypted_path = encrypted.name
                    _parse_source_file(
                        db,
                        member,
                        batch,
                        source_file,
                        item.path,
                        settings,
                        categories,
                        seen_hashes,
                    )
                    if source_file.parse_status == "needs_ocr":
                        ocr_source_file_ids.append(source_file.id)
                batch.job_progress = 5 + int(index / len(staged) * 90)
                batch.job_message = f"已处理 {index}/{len(staged)} 个文件"

            db.flush()
            db.expire(batch, ["files", "candidates"])
            _finalise_import_batch(batch)
            record_event(
                db,
                member,
                "import_batch.created",
                "import_batch",
                batch.id,
                after={
                    "source": source,
                    "target_account_id": target_account_id,
                    "filenames": filenames,
                    "file_count": len(batch.files),
                    "candidate_count": len(batch.candidates),
                    "status": batch.status,
                },
            )
            db.commit()
        except Exception:
            db.rollback()
            for encrypted in encrypted_paths:
                remove_encrypted_file(encrypted)
            raise
    if settings.ocr_enabled and ocr_source_file_ids:
        # Import completion must not depend on broker availability.  The source
        # remains encrypted and visibly marked needs_ocr if enqueueing fails.
        from .ocr_tasks import enqueue_ocr_source_files

        enqueue_ocr_source_files(ocr_source_file_ids)
    return present_batch(batch)


def get_import_job(db: Session, member: Member, batch_id: str):
    return present_import_job(get_batch(db, member, batch_id))


def _batch_filename(filenames: list[str]) -> str:
    if len(filenames) == 1:
        return filenames[0]
    return f"{filenames[0]} 等 {len(filenames)} 个文件"[:300]


def _find_duplicate_source_file(
    db: Session,
    member: Member,
    sha256: str,
    settings: Settings,
) -> SourceFile | None:
    matches = db.scalars(
        select(SourceFile)
        .join(ImportBatch, SourceFile.batch_id == ImportBatch.id)
        .where(
            SourceFile.sha256 == sha256,
            SourceFile.deleted_at.is_(None),
            SourceFile.encrypted_path.is_not(None),
            ImportBatch.owner_id == member.id,
        )
        .order_by(SourceFile.id)
    ).all()
    archive_dir = settings.source_archive_dir.resolve()
    for match in matches:
        retained = archive_dir / Path(match.encrypted_path or "").name
        if retained.is_file():
            return match
    return None


def _category_lookup(db: Session) -> dict[tuple[str, str], str]:
    categories = db.scalars(select(Category).where(Category.active.is_(True))).all()
    return {(item.kind, item.name.strip().casefold()): item.id for item in categories}


def _parse_source_file(
    db: Session,
    member: Member,
    batch: ImportBatch,
    source_file: SourceFile,
    staged_path: Path,
    settings: Settings,
    categories: dict[tuple[str, str], str],
    seen_hashes: dict[str, str | None],
) -> None:
    try:
        outcome = parse_import_file(
            staged_path,
            source_file.filename,
            max_rows=settings.source_max_rows,
            max_uncompressed_bytes=settings.source_max_uncompressed_bytes,
        )
    except Exception as exc:
        source_file.parse_status = "failed"
        source_file.parse_message = f"解析器异常：{type(exc).__name__}"
        return
    _ingest_parse_outcome(
        db,
        member,
        batch,
        source_file,
        outcome,
        settings,
        categories,
        seen_hashes,
    )


def _ingest_parse_outcome(
    db: Session,
    member: Member,
    batch: ImportBatch,
    source_file: SourceFile,
    outcome,
    settings: Settings,
    categories: dict[tuple[str, str], str],
    seen_hashes: dict[str, str | None],
) -> None:
    """Persist one deterministic or OCR parse through the same safety path."""
    source_file.parse_status = outcome.status
    source_file.parse_message = outcome.message[:500]
    source_file.record_count = len(outcome.rows)
    source_file.candidate_count = outcome.candidate_count
    source_file.rejected_count = outcome.rejected_count
    if outcome.status != "parsed":
        return
    known_hashes = _known_source_hashes(db, member, [row.source_hash for row in outcome.rows])
    for row in outcome.rows:
        record = SourceRecord(
            id=new_id("src"),
            file_id=source_file.id,
            source_ref=row.source_ref[:300],
            source_hash=row.source_hash,
            raw_payload={"fields": row.raw_payload, "issues": row.issues},
        )
        db.add(record)
        if not row.can_create_candidate:
            continue
        duplicate_link = seen_hashes.get(row.source_hash, known_hashes.get(row.source_hash))
        exact_transaction = None
        if duplicate_link:
            exact_transaction = _visible_transaction(db, member, duplicate_link)
            if exact_transaction is None:
                duplicate_link = None
        if row.source_hash in seen_hashes or row.source_hash in known_hashes:
            validation_status = "exact_duplicate" if duplicate_link else "duplicate_source_record"
        else:
            exact_transaction = _find_duplicate_transaction(
                db,
                member,
                occurred_at=row.occurred_at,
                payee=row.payee,
                direction=row.direction,
                amount_minor=row.amount_minor,
                currency=row.currency,
            )
            validation_status = "exact_duplicate" if exact_transaction else "valid"
            duplicate_link = exact_transaction.id if exact_transaction else None
        if validation_status == "valid" and row.currency != settings.base_currency:
            validation_status = "needs_fx_review"
        category_id = None
        if row.original_category:
            category_id = categories.get((row.direction, row.original_category.strip().casefold()))
        candidate = ImportCandidate(
            id=new_id("cand"),
            batch=batch,
            source_record_id=record.id,
            date=normalise_to_utc(row.occurred_at),
            payee=row.payee,
            direction=row.direction,
            amount_minor=row.amount_minor,
            currency=row.currency,
            fx_rate=None,
            base_amount_minor=None,
            suggested_category_id=category_id,
            original_category=row.original_category,
            target_account_id=batch.target_account_id,
            duplicate_of_txn_id=duplicate_link,
            validation_status=validation_status,
            confidence=row.confidence,
        )
        db.add(candidate)
        seen_hashes[row.source_hash] = duplicate_link


def _known_source_hashes(
    db: Session,
    member: Member,
    hashes: list[str],
) -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    unique_hashes = list(dict.fromkeys(hashes))
    for start in range(0, len(unique_hashes), 500):
        chunk = unique_hashes[start : start + 500]
        rows = db.execute(
            select(SourceRecord.source_hash, SourceRecord.linked_transaction_id)
            .join(SourceFile, SourceRecord.file_id == SourceFile.id)
            .join(ImportBatch, SourceFile.batch_id == ImportBatch.id)
            .where(ImportBatch.owner_id == member.id, SourceRecord.source_hash.in_(chunk))
        )
        for source_hash, transaction_id in rows:
            if source_hash not in result or transaction_id:
                result[source_hash] = transaction_id
    return result


def _visible_transaction(
    db: Session,
    member: Member,
    transaction_id: str,
) -> Transaction | None:
    transaction = db.scalar(
        select(Transaction)
        .where(Transaction.id == transaction_id, Transaction.deleted_at.is_(None))
        .options(selectinload(Transaction.lines))
    )
    if transaction and can_view_transaction(db, member, transaction):
        return transaction
    return None


def _find_duplicate_transaction(
    db: Session,
    member: Member,
    *,
    occurred_at,
    payee: str,
    direction: str,
    amount_minor: int,
    currency: str,
) -> Transaction | None:
    exact_occurred_at = normalise_to_utc(occurred_at)
    matches = db.scalars(
        select(Transaction)
        .where(
            Transaction.type == direction,
            Transaction.payee == payee,
            Transaction.amount_minor == amount_minor,
            Transaction.currency == currency,
            Transaction.occurred_at == exact_occurred_at,
            Transaction.deleted_at.is_(None),
        )
        .options(selectinload(Transaction.lines))
        .order_by(Transaction.id)
    ).all()
    for transaction in matches:
        if can_view_transaction(db, member, transaction):
            return transaction
    return None


def _finalise_import_batch(batch: ImportBatch) -> None:
    candidate_count = len(batch.candidates)
    needs_ocr = sum(item.parse_status == "needs_ocr" for item in batch.files)
    duplicates = sum(item.parse_status == "duplicate" for item in batch.files)
    failed = sum(item.parse_status == "failed" for item in batch.files)
    if candidate_count:
        batch.status = "reviewing"
        batch.job_message = f"已生成 {candidate_count} 条候选，等待人工确认"
        if needs_ocr:
            batch.job_message += f"；另有 {needs_ocr} 个文件等待 OCR"
    elif needs_ocr:
        batch.status = "needs_ocr"
        batch.job_message = f"{needs_ocr} 个文件已加密保存，等待 OCR/多模态工作器"
    elif failed:
        batch.status = "failed"
        batch.job_message = "文件已加密保存，但解析失败；请查看各文件状态"
    else:
        batch.status = "rejected"
        if duplicates == len(batch.files):
            batch.job_message = "所有文件都是本人已上传源文件的精确副本，未重复生成候选"
        else:
            batch.job_message = "没有生成可确认的候选；请查看各文件的拒绝原因"
    batch.job_progress = 100
    batch.processed_at = utcnow()
    batch.version += 1


def _candidate_fx_status(
    *,
    currency: str,
    amount_minor: int,
    fx_rate: Decimal | None,
    base_amount_minor: int | None,
) -> str:
    settings = get_settings()
    if currency == settings.base_currency:
        if fx_rate is not None or base_amount_minor is not None:
            raise HTTPException(status_code=422, detail="本币候选不应填写汇率或本币折算金额")
        return "valid"
    if fx_rate is None and base_amount_minor is None:
        return "needs_fx_review"
    if fx_rate is not None and base_amount_minor is not None:
        calculated = calculate_base_minor(amount_minor, fx_rate)
        if abs(calculated - base_amount_minor) > 1:
            raise HTTPException(status_code=422, detail="汇率与本币折算金额不一致")
    elif fx_rate is not None:
        calculate_base_minor(amount_minor, fx_rate)
    return "valid"


def patch_candidate(
    db: Session,
    member: Member,
    batch_id: str,
    candidate_id: str,
    patch: ImportCandidatePatch,
):
    batch = get_batch(db, member, batch_id, lock_for_update=True)
    if batch.status != "reviewing":
        raise HTTPException(status_code=409, detail="该批次已结束复核")
    candidate = next((item for item in batch.candidates if item.id == candidate_id), None)
    if not candidate:
        raise HTTPException(status_code=404, detail="候选账目不存在")
    if candidate.status != "pending":
        raise HTTPException(status_code=409, detail="候选账目已经处理")
    if candidate.version != patch.expected_version:
        raise HTTPException(status_code=409, detail="候选账目已被修改，请刷新后重试")
    before = {
        "date": candidate.date.isoformat(),
        "payee": candidate.payee,
        "amount_minor": candidate.amount_minor,
        "fx_rate": str(candidate.fx_rate) if candidate.fx_rate is not None else None,
        "base_amount_minor": candidate.base_amount_minor,
        "category_id": candidate.suggested_category_id,
        "target_account_id": candidate.target_account_id,
    }
    proposed_amount = patch.amount_cents or candidate.amount_minor
    proposed_fx_rate = patch.fx_rate if "fx_rate" in patch.model_fields_set else candidate.fx_rate
    proposed_base_amount = (
        patch.base_amount_cents
        if "base_amount_cents" in patch.model_fields_set
        else candidate.base_amount_minor
    )
    if patch.amount_cents is not None and "base_amount_cents" not in patch.model_fields_set:
        proposed_base_amount = None
    fx_status = _candidate_fx_status(
        currency=candidate.currency,
        amount_minor=proposed_amount,
        fx_rate=proposed_fx_rate,
        base_amount_minor=proposed_base_amount,
    )
    if patch.date is not None:
        candidate.date = normalise_to_utc(patch.date)
    if patch.payee is not None:
        candidate.payee = patch.payee
    if patch.amount_cents is not None:
        candidate.amount_minor = patch.amount_cents
    candidate.fx_rate = proposed_fx_rate
    candidate.base_amount_minor = proposed_base_amount
    if candidate.validation_status not in {"exact_duplicate", "duplicate_source_record"}:
        candidate.validation_status = fx_status
    if "suggested_category_id" in patch.model_fields_set:
        candidate.suggested_category_id = patch.suggested_category_id
    if "target_account_id" in patch.model_fields_set:
        if patch.target_account_id:
            require_account_permission(db, member, patch.target_account_id, manage=True)
        candidate.target_account_id = patch.target_account_id
    candidate.version += 1
    record_event(
        db,
        member,
        "import_candidate.updated",
        "import_candidate",
        candidate.id,
        before=before,
        after={
            "date": candidate.date.isoformat(),
            "payee": candidate.payee,
            "amount_minor": candidate.amount_minor,
            "fx_rate": str(candidate.fx_rate) if candidate.fx_rate is not None else None,
            "base_amount_minor": candidate.base_amount_minor,
            "validation_status": candidate.validation_status,
            "category_id": candidate.suggested_category_id,
            "target_account_id": candidate.target_account_id,
        },
    )
    db.commit()
    return present_candidate(candidate)


def _refresh_batch_status(batch: ImportBatch) -> None:
    pending = sum(item.status == "pending" for item in batch.candidates)
    needs_ocr = sum(item.parse_status == "needs_ocr" for item in batch.files)
    confirmed = sum(item.status == "confirmed" for item in batch.candidates)
    skipped = sum(item.status == "skipped" for item in batch.candidates)
    if pending:
        batch.status = "reviewing"
        batch.job_message = f"尚有 {pending} 条候选等待人工确认"
        if needs_ocr:
            batch.job_message += f"；另有 {needs_ocr} 个文件等待 OCR"
    elif needs_ocr:
        batch.status = "needs_ocr"
        batch.job_message = (
            f"已入账 {confirmed} 条候选；仍有 {needs_ocr} 个文件等待 OCR，批次尚未完成"
        )
    elif confirmed:
        batch.status = "posted"
        batch.job_message = f"已入账 {confirmed} 条候选"
        if skipped:
            batch.job_message += f"；另有 {skipped} 条已跳过"
    else:
        batch.status = "rejected"
        batch.job_message = "没有候选入账"
        if skipped:
            batch.job_message += f"；{skipped} 条候选已跳过"
    batch.job_progress = 100
    batch.version += 1


def _lock_confirmation_accounts(db: Session, account_ids: set[str]) -> list[Account]:
    """Acquire every batch account lock once, using one global PostgreSQL order."""
    ordered_ids = sorted(account_ids)
    if not ordered_ids:
        return []
    accounts = db.scalars(
        select(Account).where(Account.id.in_(ordered_ids)).order_by(Account.id).with_for_update()
    ).all()
    if len(accounts) != len(ordered_ids):
        raise HTTPException(status_code=404, detail="账户不存在")
    return list(accounts)


def confirm_candidates(db: Session, member: Member, batch_id: str, candidate_ids: list[str]):
    acquire_ledger_mutation_guard(db)
    batch = get_batch(db, member, batch_id, lock_for_update=True)
    if batch.status != "reviewing":
        raise HTTPException(status_code=409, detail="该批次已结束复核")
    requested_ids = set(candidate_ids)
    selected = [item for item in batch.candidates if item.id in requested_ids]
    if len(selected) != len(requested_ids):
        raise HTTPException(status_code=404, detail="部分候选账目不存在")
    resolved_accounts: dict[str, str] = {}
    for candidate in selected:
        if candidate.status != "pending":
            raise HTTPException(status_code=409, detail=f"候选 {candidate.id} 已处理")
        if candidate.validation_status != "valid":
            raise HTTPException(status_code=422, detail=f"候选 {candidate.id} 尚未通过校验")
        account_id = candidate.target_account_id or batch.target_account_id
        if not account_id:
            raise HTTPException(status_code=422, detail=f"候选 {candidate.id} 尚未选择账户")
        resolved_accounts[candidate.id] = account_id

    _lock_confirmation_accounts(db, set(resolved_accounts.values()))
    for account_id in sorted(set(resolved_accounts.values())):
        require_account_permission(db, member, account_id, manage=True)

    duplicate_candidate_ids: list[str] = []
    for candidate in selected:
        duplicate = _find_duplicate_transaction(
            db,
            member,
            occurred_at=candidate.date,
            payee=candidate.payee,
            direction=candidate.direction,
            amount_minor=candidate.amount_minor,
            currency=candidate.currency,
        )
        if duplicate is None:
            continue
        candidate.validation_status = "exact_duplicate"
        candidate.duplicate_of_txn_id = duplicate.id
        candidate.version += 1
        duplicate_candidate_ids.append(candidate.id)

    if duplicate_candidate_ids:
        record_event(
            db,
            member,
            "import_batch.confirmation_conflict",
            "import_batch",
            batch.id,
            after={"candidate_ids": duplicate_candidate_ids},
        )
        db.commit()
        raise HTTPException(
            status_code=409,
            detail={
                "message": "确认前发现重复账目，请重新复核",
                "candidateIds": duplicate_candidate_ids,
            },
        )

    for candidate in selected:
        account_id = resolved_accounts[candidate.id]
        txn = create_transaction(
            db,
            member,
            NewTransactionInput(
                type=candidate.direction,
                payee=candidate.payee,
                note="",
                categoryId=candidate.suggested_category_id,
                accountId=account_id,
                amountCents=candidate.amount_minor,
                currency=candidate.currency,
                date=candidate.date,
                fxRate=candidate.fx_rate,
                baseAmountCents=candidate.base_amount_minor,
            ),
            source="import",
            batch_id=batch.id,
            commit=False,
        )
        candidate.status = "confirmed"
        candidate.transaction_id = txn.id
        if candidate.source_record_id:
            source_record = db.get(SourceRecord, candidate.source_record_id)
            if source_record:
                source_record.linked_transaction_id = txn.id
    _refresh_batch_status(batch)
    record_event(
        db,
        member,
        "import_batch.confirmed",
        "import_batch",
        batch.id,
        after={"candidate_ids": candidate_ids, "status": batch.status},
    )
    db.commit()
    return present_batch(batch)


def skip_candidates(db: Session, member: Member, batch_id: str, candidate_ids: list[str]):
    batch = get_batch(db, member, batch_id, lock_for_update=True)
    if batch.status != "reviewing":
        raise HTTPException(status_code=409, detail="该批次已结束复核")
    selected = [item for item in batch.candidates if item.id in set(candidate_ids)]
    if len(selected) != len(set(candidate_ids)):
        raise HTTPException(status_code=404, detail="部分候选账目不存在")
    for candidate in selected:
        if candidate.status != "pending":
            raise HTTPException(status_code=409, detail=f"候选 {candidate.id} 已处理")
        candidate.status = "skipped"
    _refresh_batch_status(batch)
    record_event(
        db,
        member,
        "import_batch.candidates_skipped",
        "import_batch",
        batch.id,
        after={"candidate_ids": candidate_ids, "status": batch.status},
    )
    db.commit()
    return present_batch(batch)


def undo_batch(db: Session, member: Member, batch_id: str):
    acquire_ledger_mutation_guard(db)
    batch = get_batch(db, member, batch_id, lock_for_update=True)
    if batch.status not in {"posted", "needs_ocr"}:
        raise HTTPException(status_code=409, detail="只有包含已入账账目的批次可以撤销")
    transactions = db.scalars(
        select(Transaction)
        .where(Transaction.batch_id == batch.id, Transaction.deleted_at.is_(None))
        .options(selectinload(Transaction.lines))
        .order_by(Transaction.id)
        .with_for_update()
    ).all()
    if not transactions:
        raise HTTPException(status_code=409, detail="该批次没有可撤销的已入账账目")
    transaction_ids = [transaction.id for transaction in transactions]
    split_transaction_ids = set(
        db.scalars(
            select(Split.transaction_id).where(Split.transaction_id.in_(transaction_ids))
        ).all()
    )
    refunded_transaction_ids = set(
        db.scalars(
            select(Transaction.refund_of_id)
            .where(
                Transaction.refund_of_id.in_(transaction_ids),
                Transaction.type == "refund",
                Transaction.deleted_at.is_(None),
            )
            .order_by(Transaction.id)
            .with_for_update()
        ).all()
    )
    blocked: list[str] = []
    for transaction in transactions:
        if (
            transaction.version > 1
            or transaction.id in split_transaction_ids
            or transaction.id in refunded_transaction_ids
            or period_is_locked(db, transaction.occurred_at)
            or not can_manage_transaction(db, member, transaction)
        ):
            blocked.append(transaction.id)
    if blocked:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "部分账目后来被修改、分摊、退款或位于锁定账期，不能整批撤销",
                "transactionIds": blocked,
            },
        )
    validate_projected_account_balances(
        db,
        [line for transaction in transactions for line in transaction.lines],
        multiplier=-1,
    )
    for transaction in transactions:
        transaction.deleted_at = utcnow()
        transaction.deleted_by = member.id
        transaction.version += 1
    batch.status = "undone"
    batch.job_message = "该批次创建的账目已撤销；加密源文件仍按保留期管理"
    batch.version += 1
    record_event(
        db,
        member,
        "import_batch.undone",
        "import_batch",
        batch.id,
        after={"transaction_ids": [item.id for item in transactions]},
    )
    db.commit()
    return present_batch(batch)
