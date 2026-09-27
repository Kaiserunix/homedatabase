from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, selectinload

from ..config import get_settings
from ..models import Account, Category, EntryLine, Member, PeriodLock, Split, Transaction, utcnow
from ..money import calculate_base_minor, require_safe_signed_minor
from ..schemas import NewTransactionInput, TransactionOut, TransactionUpdateInput
from .audit import record_event
from .locking import acquire_ledger_mutation_guard
from .permissions import (
    account_permission,
    can_manage_transaction,
    can_view_transaction,
    require_account_permission,
    transaction_has_hidden_accounts,
)
from .presenters import present_account, present_transaction

SHANGHAI = ZoneInfo("Asia/Shanghai")


def _normalise_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=SHANGHAI)
    return value.astimezone(UTC)


def period_is_locked(db: Session, occurred_at: datetime) -> bool:
    # SQLite persists UTC datetimes without their offset.  ORM-loaded values
    # are restored by UTCDateTime, but keep this boundary deterministic for
    # direct callers and test doubles as well.
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=UTC)
    local_day = occurred_at.astimezone(SHANGHAI).date()
    return (
        db.scalar(
            select(func.count(PeriodLock.id)).where(
                PeriodLock.starts_on <= local_day,
                PeriodLock.ends_on >= local_day,
            )
        )
        or 0
    ) > 0


def _require_unlocked(db: Session, occurred_at: datetime) -> None:
    if period_is_locked(db, occurred_at):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该账期已锁定")


def _category_for(db: Session, category_id: str | None, kind: str) -> Category | None:
    if category_id is None:
        return None
    category = db.get(Category, category_id)
    if not category or not category.active:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="分类不存在")
    expected = "income" if kind == "income" else "expense"
    if category.kind != expected:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="分类与收支类型不匹配"
        )
    return category


def _ledger_category_code(kind: str, category: Category | None) -> str:
    ledger_kind = "income" if kind == "income" else "expense"
    return f"{ledger_kind}:{category.id if category else 'uncategorized'}"


def _apply_category_to_entry_lines(transaction: Transaction, category: Category | None) -> None:
    if transaction.type == "transfer":
        return
    code = _ledger_category_code(transaction.type, category)
    for line in transaction.lines:
        if line.ledger_code is not None:
            line.ledger_code = code


def _invalidate_transaction_split(db: Session, transaction: Transaction) -> None:
    split = db.scalar(
        select(Split)
        .where(Split.transaction_id == transaction.id)
        .options(selectinload(Split.shares))
        .with_for_update()
    )
    if not split:
        return
    split.version += 1
    split.status = "open"
    for share in split.shares:
        if share.member_id == split.created_by:
            share.status = "accepted"
            share.accepted_version = split.version
            share.responded_at = utcnow()
        else:
            share.status = "pending"
            share.accepted_version = None
            share.note = None
            share.responded_at = None


def _base_amount(input_data: NewTransactionInput) -> int:
    settings = get_settings()
    if input_data.currency == settings.base_currency:
        if input_data.fx_rate is not None and input_data.fx_rate != Decimal("1"):
            raise HTTPException(status_code=422, detail="本币交易汇率必须等于 1")
        if (
            input_data.base_amount_cents is not None
            and input_data.base_amount_cents != input_data.amount_cents
        ):
            raise HTTPException(status_code=422, detail="本币交易折算金额必须等于交易金额")
        return input_data.amount_cents
    converted = (
        calculate_base_minor(input_data.amount_cents, input_data.fx_rate)
        if input_data.fx_rate is not None
        else None
    )
    if input_data.base_amount_cents is not None:
        if converted is not None and abs(converted - input_data.base_amount_cents) > 1:
            raise HTTPException(status_code=422, detail="汇率与本币折算金额不一致")
        return input_data.base_amount_cents
    if converted is not None:
        return converted
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail="外币交易必须提供实际人民币金额或固定汇率",
    )


def _locked_refund_original(
    db: Session,
    member: Member,
    refund_of_id: str | None,
) -> Transaction:
    original = db.scalar(
        select(Transaction)
        .where(Transaction.id == refund_of_id)
        .options(selectinload(Transaction.lines))
        .with_for_update()
    )
    if not original or original.type != "expense" or original.deleted_at is not None:
        raise HTTPException(status_code=422, detail="退款关联的原支出不存在")
    if not can_view_transaction(db, member, original):
        raise HTTPException(status_code=403, detail="无权关联此原支出")
    return original


def _require_refund_capacity(
    db: Session,
    original: Transaction,
    amount_minor: int,
    *,
    exclude_refund_id: str | None = None,
) -> None:
    conditions = [
        Transaction.refund_of_id == original.id,
        Transaction.type == "refund",
        Transaction.deleted_at.is_(None),
    ]
    if exclude_refund_id is not None:
        conditions.append(Transaction.id != exclude_refund_id)
    refunded_base = db.scalar(
        select(func.coalesce(func.sum(Transaction.base_amount_minor), 0)).where(*conditions)
    )
    if int(refunded_base or 0) + amount_minor > original.base_amount_minor:
        raise HTTPException(status_code=422, detail="累计退款金额不能超过原支出")


def _active_refunds_for_category_sync(db: Session, transaction_id: str) -> list[Transaction]:
    """Lock active refunds in one stable order before changing their versions."""
    return list(
        db.scalars(
            select(Transaction)
            .where(
                Transaction.refund_of_id == transaction_id,
                Transaction.type == "refund",
                Transaction.deleted_at.is_(None),
            )
            .options(selectinload(Transaction.lines))
            .order_by(Transaction.id)
            .with_for_update()
        ).all()
    )


def _account_balance(db: Session, account: Account) -> int:
    signed_native_movement = db.scalar(
        select(
            func.coalesce(
                func.sum(
                    case(
                        (EntryLine.debit_minor > 0, EntryLine.account_amount_minor),
                        else_=-EntryLine.account_amount_minor,
                    )
                ),
                0,
            )
        )
        .join(Transaction, Transaction.id == EntryLine.transaction_id)
        .where(
            EntryLine.account_id == account.id,
            Transaction.deleted_at.is_(None),
        )
    )
    movement = int(signed_native_movement or 0)
    if account.kind == "liability":
        movement = -movement
    return require_safe_signed_minor(
        account.opening_balance_minor + movement,
        detail=f"账户 {account.id} 的当前余额已超出安全范围",
    )


def validate_projected_account_balances(
    db: Session,
    lines: list[EntryLine],
    *,
    multiplier: int = 1,
) -> None:
    """Lock touched accounts and reject a write whose aggregate balance is unsafe."""
    account_ids = sorted({line.account_id for line in lines if line.account_id is not None})
    if not account_ids:
        return
    accounts = db.scalars(
        select(Account).where(Account.id.in_(account_ids)).order_by(Account.id).with_for_update()
    ).all()
    by_id = {account.id: account for account in accounts}
    if len(by_id) != len(account_ids):
        raise HTTPException(status_code=422, detail="分录关联账户不存在")
    changes: dict[str, int] = defaultdict(int)
    for line in lines:
        if line.account_id is None or line.account_amount_minor is None:
            continue
        signed = line.account_amount_minor if line.debit_minor > 0 else -line.account_amount_minor
        if by_id[line.account_id].kind == "liability":
            signed = -signed
        changes[line.account_id] += signed * multiplier
    for account_id in account_ids:
        current = _account_balance(db, by_id[account_id])
        require_safe_signed_minor(
            current + changes[account_id],
            detail=f"账户 {account_id} 入账后余额将超出安全范围",
        )


def list_accounts(db: Session, member: Member):
    accounts = db.scalars(
        select(Account).where(Account.deleted_at.is_(None)).order_by(Account.name)
    ).all()
    result = []
    for account in accounts:
        if account_permission(db, member, account) is None:
            continue
        result.append(present_account(db, member, account, _account_balance(db, account)))
    return result


def list_categories(db: Session, *, include_inactive: bool = False) -> list[Category]:
    statement = select(Category)
    if not include_inactive:
        statement = statement.where(Category.active.is_(True))
    return db.scalars(statement.order_by(Category.kind, Category.parent_id, Category.name)).all()


def _load_transactions(db: Session) -> list[Transaction]:
    return db.scalars(
        select(Transaction)
        .where(Transaction.deleted_at.is_(None))
        .options(selectinload(Transaction.lines))
        .order_by(Transaction.occurred_at.desc(), Transaction.id.desc())
        .limit(5000)
    ).all()


def list_transactions(
    db: Session,
    member: Member,
    *,
    type_filter: str | None = None,
    account_id: str | None = None,
    month: str | None = None,
    keyword: str | None = None,
) -> list[TransactionOut]:
    if account_id:
        require_account_permission(db, member, account_id)
    result: list[TransactionOut] = []
    for transaction in _load_transactions(db):
        if not can_view_transaction(db, member, transaction):
            continue
        if type_filter and transaction.type != type_filter:
            continue
        if account_id and account_id not in {line.account_id for line in transaction.lines}:
            continue
        local_date = transaction.occurred_at.astimezone(SHANGHAI)
        if month and local_date.strftime("%Y-%m") != month:
            continue
        if keyword:
            needle = keyword.casefold()
            partial = transaction_has_hidden_accounts(db, member, transaction)
            visible_payee = "私人账户往来（已脱敏）" if partial else transaction.payee
            visible_note = "" if partial else transaction.note
            if needle not in visible_payee.casefold() and needle not in visible_note.casefold():
                continue
        result.append(present_transaction(db, member, transaction))
    return result


def list_recycled_transactions(db: Session, member: Member) -> list[TransactionOut]:
    records = db.scalars(
        select(Transaction)
        .where(Transaction.deleted_at.is_not(None))
        .options(selectinload(Transaction.lines))
        .order_by(Transaction.deleted_at.desc())
        .limit(500)
    ).all()
    return [
        present_transaction(db, member, item)
        for item in records
        if can_view_transaction(db, member, item)
    ]


def create_transaction(
    db: Session,
    member: Member,
    input_data: NewTransactionInput,
    *,
    source: str = "manual",
    batch_id: str | None = None,
    commit: bool = True,
) -> Transaction:
    acquire_ledger_mutation_guard(db)
    occurred_at = _normalise_datetime(input_data.date)
    _require_unlocked(db, occurred_at)
    source_account = require_account_permission(db, member, input_data.account_id, manage=True)
    base_amount = _base_amount(input_data)

    target_account: Account | None = None
    if input_data.type == "transfer":
        target_account = require_account_permission(
            db, member, input_data.to_account_id or "", manage=True
        )
        if source_account.currency != target_account.currency:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="首版不支持两个不同币种账户直接转账",
            )
        if input_data.currency != source_account.currency:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="转账币种必须与转出、转入账户币种一致",
            )

    refund_of: Transaction | None = None
    if input_data.type == "refund":
        refund_of = _locked_refund_original(db, member, input_data.refund_of)
        _require_refund_capacity(db, refund_of, base_amount)

    category_id = input_data.category_id
    if input_data.type == "refund" and refund_of is not None:
        if category_id is not None and category_id != refund_of.category_id:
            raise HTTPException(status_code=422, detail="退款分类必须继承原支出")
        category_id = refund_of.category_id
    category = _category_for(db, category_id, input_data.type)

    if source_account.currency == input_data.currency:
        account_amount = input_data.amount_cents
    elif source_account.currency == get_settings().base_currency:
        account_amount = base_amount
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="交易币种必须与账户币种一致，或由基础币账户按人民币实扣金额入账",
        )

    transaction = Transaction(
        occurred_at=occurred_at,
        type=input_data.type,
        payee=input_data.payee,
        note=input_data.note,
        category_id=category.id if category else None,
        currency=input_data.currency,
        amount_minor=input_data.amount_cents,
        base_currency=get_settings().base_currency,
        base_amount_minor=base_amount,
        fx_rate=input_data.fx_rate,
        source=source,
        batch_id=batch_id,
        refund_of_id=refund_of.id if refund_of else None,
        created_by=member.id,
    )
    code = _ledger_category_code(input_data.type, category)
    if input_data.type == "transfer":
        transaction.lines = [
            EntryLine(
                account_id=target_account.id,
                debit_minor=base_amount,
                credit_minor=0,
                account_amount_minor=input_data.amount_cents,
            ),
            EntryLine(
                account_id=source_account.id,
                debit_minor=0,
                credit_minor=base_amount,
                account_amount_minor=input_data.amount_cents,
            ),
        ]
    elif input_data.type == "income":
        transaction.lines = [
            EntryLine(
                account_id=source_account.id,
                debit_minor=base_amount,
                credit_minor=0,
                account_amount_minor=account_amount,
            ),
            EntryLine(ledger_code=code, debit_minor=0, credit_minor=base_amount),
        ]
    elif input_data.type == "refund":
        transaction.lines = [
            EntryLine(
                account_id=source_account.id,
                debit_minor=base_amount,
                credit_minor=0,
                account_amount_minor=account_amount,
            ),
            EntryLine(ledger_code=code, debit_minor=0, credit_minor=base_amount),
        ]
    else:
        transaction.lines = [
            EntryLine(ledger_code=code, debit_minor=base_amount, credit_minor=0),
            EntryLine(
                account_id=source_account.id,
                debit_minor=0,
                credit_minor=base_amount,
                account_amount_minor=account_amount,
            ),
        ]
    if sum(line.debit_minor for line in transaction.lines) != sum(
        line.credit_minor for line in transaction.lines
    ):
        raise HTTPException(status_code=500, detail="分录不平衡，拒绝入账")
    validate_projected_account_balances(db, transaction.lines)
    db.add(transaction)
    db.flush()
    record_event(
        db,
        member,
        "transaction.created",
        "transaction",
        transaction.id,
        after={"type": transaction.type, "amount_minor": transaction.amount_minor},
    )
    if commit:
        db.commit()
        db.refresh(transaction)
    return transaction


def get_transaction(
    db: Session, member: Member, transaction_id: str, *, for_update: bool = False
) -> Transaction:
    statement = (
        select(Transaction)
        .where(Transaction.id == transaction_id, Transaction.deleted_at.is_(None))
        .options(selectinload(Transaction.lines))
    )
    if for_update:
        statement = statement.with_for_update()
    transaction = db.scalar(statement)
    if not transaction:
        raise HTTPException(status_code=404, detail="账目不存在")
    if not can_view_transaction(db, member, transaction):
        raise HTTPException(status_code=403, detail="无权查看此账目")
    return transaction


def update_transaction(
    db: Session, member: Member, transaction_id: str, input_data: TransactionUpdateInput
) -> TransactionOut:
    acquire_ledger_mutation_guard(db)
    transaction = get_transaction(db, member, transaction_id, for_update=True)
    if not can_manage_transaction(db, member, transaction):
        raise HTTPException(status_code=403, detail="无权修改此账目")
    _require_unlocked(db, transaction.occurred_at)
    if transaction.version != input_data.expected_version:
        raise HTTPException(status_code=409, detail="账目已被其他人修改，请刷新后重试")
    before = {
        "payee": transaction.payee,
        "note": transaction.note,
        "category_id": transaction.category_id,
        "version": transaction.version,
    }
    changed_for_split = False
    if input_data.payee is not None and input_data.payee != transaction.payee:
        transaction.payee = input_data.payee
        changed_for_split = True
    if input_data.note is not None and input_data.note != transaction.note:
        transaction.note = input_data.note
        changed_for_split = True
    if (
        "category_id" in input_data.model_fields_set
        and input_data.category_id != transaction.category_id
    ):
        if transaction.type == "transfer":
            raise HTTPException(status_code=422, detail="转账不能设置收支分类")
        if transaction.type == "refund":
            raise HTTPException(status_code=422, detail="退款分类由原支出继承")
        category = _category_for(db, input_data.category_id, transaction.type)
        refunds: list[Transaction] = []
        if transaction.type == "expense":
            refunds = _active_refunds_for_category_sync(db, transaction.id)
            for refund in refunds:
                _require_unlocked(db, refund.occurred_at)
        transaction.category_id = input_data.category_id
        _apply_category_to_entry_lines(transaction, category)
        for refund in refunds:
            refund.category_id = input_data.category_id
            _apply_category_to_entry_lines(refund, category)
            refund.version += 1
            refund.updated_at = utcnow()
            _invalidate_transaction_split(db, refund)
        changed_for_split = True
    transaction.version += 1
    transaction.updated_at = utcnow()
    if changed_for_split:
        _invalidate_transaction_split(db, transaction)
    record_event(
        db,
        member,
        "transaction.updated",
        "transaction",
        transaction.id,
        before=before,
        after={
            "payee": transaction.payee,
            "note": transaction.note,
            "category_id": transaction.category_id,
            "version": transaction.version,
        },
    )
    db.commit()
    return present_transaction(db, member, transaction)


def delete_transaction(db: Session, member: Member, transaction_id: str, reason: str) -> None:
    acquire_ledger_mutation_guard(db)
    transaction = get_transaction(db, member, transaction_id, for_update=True)
    if not can_manage_transaction(db, member, transaction):
        raise HTTPException(status_code=403, detail="无权删除此账目")
    _require_unlocked(db, transaction.occurred_at)
    if db.scalar(select(Split.id).where(Split.transaction_id == transaction.id)) is not None:
        raise HTTPException(status_code=409, detail="账目已有分摊，不能直接删除")
    if (
        db.scalar(
            select(Transaction.id).where(
                Transaction.refund_of_id == transaction.id,
                Transaction.type == "refund",
                Transaction.deleted_at.is_(None),
            )
        )
        is not None
    ):
        raise HTTPException(status_code=409, detail="账目已有退款，不能直接删除")
    validate_projected_account_balances(db, transaction.lines, multiplier=-1)
    transaction.deleted_at = utcnow()
    transaction.deleted_by = member.id
    transaction.version += 1
    record_event(
        db,
        member,
        "transaction.recycled",
        "transaction",
        transaction.id,
        before={"deleted": False},
        after={"deleted": True, "reason": reason},
    )
    db.commit()


def restore_transaction(
    db: Session,
    member: Member,
    transaction_id: str,
    expected_version: int,
) -> TransactionOut:
    acquire_ledger_mutation_guard(db)
    transaction = db.scalar(
        select(Transaction)
        .where(Transaction.id == transaction_id, Transaction.deleted_at.is_not(None))
        .options(selectinload(Transaction.lines))
        .with_for_update()
    )
    if not transaction:
        raise HTTPException(status_code=404, detail="回收站中没有此账目")
    if not can_manage_transaction(db, member, transaction):
        raise HTTPException(status_code=403, detail="无权恢复此账目")
    _require_unlocked(db, transaction.occurred_at)
    if transaction.version != expected_version:
        raise HTTPException(status_code=409, detail="账目已被其他人修改，请刷新后重试")
    if transaction.batch_id:
        from ..models import ImportBatch

        batch = db.get(ImportBatch, transaction.batch_id)
        if batch and batch.status == "undone":
            raise HTTPException(status_code=409, detail="导入批次已整批撤销，不能单独恢复")
    if transaction.type == "refund":
        original = _locked_refund_original(db, member, transaction.refund_of_id)
        _require_refund_capacity(
            db,
            original,
            transaction.base_amount_minor,
            exclude_refund_id=transaction.id,
        )
        category = db.get(Category, original.category_id) if original.category_id else None
        if original.category_id is not None and category is None:
            raise HTTPException(status_code=422, detail="退款关联的原支出分类不存在")
        transaction.category_id = original.category_id
        _apply_category_to_entry_lines(transaction, category)
    validate_projected_account_balances(db, transaction.lines)
    transaction.deleted_at = None
    transaction.deleted_by = None
    transaction.version += 1
    record_event(
        db,
        member,
        "transaction.restored",
        "transaction",
        transaction.id,
        before={"deleted": True},
        after={"deleted": False, "version": transaction.version},
    )
    db.commit()
    return present_transaction(db, member, transaction)
