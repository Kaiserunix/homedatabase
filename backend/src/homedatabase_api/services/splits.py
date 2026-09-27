from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..models import Member, Split, SplitShare, Transaction, utcnow
from ..schemas import NewSplitInput, SplitResponseInput
from .audit import record_event
from .ledger import get_transaction
from .permissions import can_manage_transaction, can_view_transaction
from .presenters import present_split


def _load_split(db: Session, split_id: str, *, for_update: bool = False) -> Split:
    statement = select(Split).where(Split.id == split_id).options(selectinload(Split.shares))
    if for_update:
        statement = statement.with_for_update()
    split = db.scalar(statement)
    if not split:
        raise HTTPException(status_code=404, detail="分摊不存在")
    return split


def _load_split_after_transaction_lock(
    db: Session,
    member: Member,
    split_id: str,
) -> tuple[Split, Transaction]:
    """Use the global Transaction -> Split lock order for cross-entity writes."""
    transaction_id = db.scalar(select(Split.transaction_id).where(Split.id == split_id))
    if transaction_id is None:
        raise HTTPException(status_code=404, detail="分摊不存在")
    transaction = get_transaction(db, member, transaction_id, for_update=True)
    split = _load_split(db, split_id, for_update=True)
    if split.transaction_id != transaction.id:
        raise HTTPException(status_code=409, detail="分摊关联账目已变化，请刷新后重试")
    return split, transaction


def _recompute_status(split: Split) -> None:
    if any(share.status == "returned" for share in split.shares):
        split.status = "returned"
    elif all(
        share.status == "accepted" and share.accepted_version == split.version
        for share in split.shares
    ):
        split.status = "settled"
    else:
        split.status = "open"


def list_splits(db: Session, member: Member):
    splits = db.scalars(
        select(Split).options(selectinload(Split.shares)).order_by(Split.created_at.desc())
    ).all()
    result = []
    for split in splits:
        transaction = db.get(Transaction, split.transaction_id)
        if not transaction or transaction.deleted_at is not None:
            continue
        involved = split.created_by == member.id or any(
            share.member_id == member.id for share in split.shares
        )
        if involved or can_view_transaction(db, member, transaction):
            result.append(present_split(db, split))
    return result


def create_split(db: Session, member: Member, input_data: NewSplitInput):
    transaction = get_transaction(db, member, input_data.txn_id, for_update=True)
    if not can_manage_transaction(db, member, transaction):
        raise HTTPException(status_code=403, detail="无权分摊此账目")
    if transaction.type != "expense":
        raise HTTPException(status_code=422, detail="只有支出可以分摊")
    if input_data.total_cents != transaction.amount_minor:
        raise HTTPException(status_code=422, detail="分摊总额必须等于原交易金额")
    existing = db.scalar(select(Split).where(Split.transaction_id == transaction.id))
    if existing:
        raise HTTPException(status_code=409, detail="该账目已经存在分摊")
    member_ids = {share.member_id for share in input_data.shares}
    active_member_ids = set(
        db.scalars(
            select(Member.id).where(Member.id.in_(member_ids), Member.active.is_(True))
        ).all()
    )
    if active_member_ids != member_ids:
        raise HTTPException(status_code=422, detail="分摊成员不存在或已停用")
    if member.id not in member_ids:
        raise HTTPException(status_code=422, detail="分摊必须保留发起人的份额")
    split = Split(transaction_id=transaction.id, created_by=member.id)
    split.shares = [
        SplitShare(
            member_id=share.member_id,
            share_minor=share.share_cents,
            status="accepted" if share.member_id == member.id else "pending",
            accepted_version=1 if share.member_id == member.id else None,
            responded_at=utcnow() if share.member_id == member.id else None,
        )
        for share in input_data.shares
    ]
    db.add(split)
    db.flush()
    _recompute_status(split)
    record_event(
        db,
        member,
        "split.created",
        "split",
        split.id,
        after={"transaction_id": transaction.id, "version": 1},
    )
    db.commit()
    return present_split(db, split)


def respond_to_split(db: Session, member: Member, split_id: str, input_data: SplitResponseInput):
    split = _load_split(db, split_id, for_update=True)
    transaction = db.get(Transaction, split.transaction_id)
    if not transaction or transaction.deleted_at is not None:
        raise HTTPException(status_code=409, detail="原账目已删除，不能处理分摊")
    if split.version != input_data.expected_version:
        raise HTTPException(status_code=409, detail="分摊已被修改，请刷新后重新审阅")
    share = next((item for item in split.shares if item.member_id == member.id), None)
    if not share:
        raise HTTPException(status_code=403, detail="这笔分摊没有分给你")
    if share.status != "pending":
        raise HTTPException(status_code=409, detail="这笔分摊已经处理")
    share.status = "accepted" if input_data.accept else "returned"
    share.note = input_data.note
    share.responded_at = utcnow()
    share.accepted_version = split.version if input_data.accept else None
    _recompute_status(split)
    record_event(
        db,
        member,
        "split.accepted" if input_data.accept else "split.returned",
        "split",
        split.id,
        after={"member_id": member.id, "version": split.version, "note": input_data.note},
    )
    db.commit()
    return present_split(db, split)


def update_share(
    db: Session,
    member: Member,
    split_id: str,
    target_member_id: str,
    share_cents: int,
    expected_version: int,
):
    split, transaction = _load_split_after_transaction_lock(db, member, split_id)
    if split.created_by != member.id:
        raise HTTPException(status_code=403, detail="只有分摊发起人可以修改")
    if split.version != expected_version:
        raise HTTPException(status_code=409, detail="分摊已被修改，请刷新后重试")
    if not can_manage_transaction(db, member, transaction):
        raise HTTPException(status_code=403, detail="已无权修改原账目，不能调整分摊")
    target = next((item for item in split.shares if item.member_id == target_member_id), None)
    creator_share = next((item for item in split.shares if item.member_id == member.id), None)
    if not target:
        raise HTTPException(status_code=404, detail="分摊成员不存在")
    if target.member_id == member.id or creator_share is None:
        raise HTTPException(status_code=422, detail="请保留发起人的份额用于平衡差额")
    delta = share_cents - target.share_minor
    if creator_share.share_minor - delta <= 0:
        raise HTTPException(status_code=422, detail="修改后发起人的份额必须大于零")
    before = {"member_id": target.member_id, "share_minor": target.share_minor}
    target.share_minor = share_cents
    creator_share.share_minor -= delta
    split.version += 1
    for share in split.shares:
        share.note = None
        if share.member_id == member.id:
            share.status = "accepted"
            share.accepted_version = split.version
            share.responded_at = utcnow()
        else:
            share.status = "pending"
            share.accepted_version = None
            share.responded_at = None
    creator_share.status = "accepted"
    creator_share.accepted_version = split.version
    split.status = "open"
    record_event(
        db,
        member,
        "split.share_updated",
        "split",
        split.id,
        before=before,
        after={
            "member_id": target.member_id,
            "share_minor": target.share_minor,
            "version": split.version,
        },
    )
    db.commit()
    return present_split(db, split)
