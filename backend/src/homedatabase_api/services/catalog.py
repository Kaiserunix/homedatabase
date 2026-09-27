from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Account, AccountGrant, Category, Member, PeriodLock
from ..schemas import (
    AccountCreateInput,
    AccountGrantOut,
    AccountUpdateInput,
    CategoryCreateInput,
    CategoryOut,
    CategoryUpdateInput,
)
from .audit import record_event
from .locking import acquire_ledger_mutation_guard
from .permissions import account_permission, require_account_permission
from .presenters import present_account


def create_account(db: Session, member: Member, input_data: AccountCreateInput):
    if input_data.scope == "family" and not member.is_admin:
        raise HTTPException(status_code=403, detail="只有家庭管理员可以新建公共账户")
    if input_data.opening_balance_cents != 0:
        acquire_ledger_mutation_guard(db)
        if db.scalar(select(PeriodLock.id).limit(1)) is not None:
            raise HTTPException(
                status_code=409,
                detail="已有锁定期间，不能再用非零期初余额新建账户",
            )
    owner_id = member.id if input_data.scope == "personal" else None
    account = Account(
        name=input_data.name,
        kind=input_data.kind,
        scope=input_data.scope,
        owner_id=owner_id,
        currency=input_data.currency,
        opening_balance_minor=input_data.opening_balance_cents,
        icon=input_data.icon,
    )
    db.add(account)
    db.flush()
    if input_data.scope == "family":
        db.add(
            AccountGrant(
                account_id=account.id,
                member_id=member.id,
                permission="manage",
            )
        )
        db.flush()
    record_event(
        db,
        member,
        "account.created",
        "account",
        account.id,
        after={"name": account.name, "scope": account.scope, "kind": account.kind},
    )
    db.commit()
    db.refresh(account)
    return present_account(db, member, account, account.opening_balance_minor)


def update_account(
    db: Session,
    member: Member,
    account_id: str,
    input_data: AccountUpdateInput,
):
    account = db.scalar(select(Account).where(Account.id == account_id).with_for_update())
    if not account or account.deleted_at is not None:
        raise HTTPException(status_code=404, detail="账户不存在")
    if account_permission(db, member, account) != "manage":
        raise HTTPException(status_code=403, detail="无权操作此账户")
    if account.version != input_data.expected_version:
        raise HTTPException(status_code=409, detail="账户已被其他人修改，请刷新后重试")
    before = {"name": account.name, "icon": account.icon, "version": account.version}
    if input_data.name is not None:
        account.name = input_data.name
    if input_data.icon is not None:
        account.icon = input_data.icon
    account.version += 1
    record_event(
        db,
        member,
        "account.updated",
        "account",
        account.id,
        before=before,
        after={"name": account.name, "icon": account.icon, "version": account.version},
    )
    db.commit()
    from .ledger import list_accounts

    return next(item for item in list_accounts(db, member) if item.id == account.id)


def _can_administer_grants(db: Session, member: Member, account: Account) -> bool:
    if account.scope == "personal":
        return account.owner_id == member.id
    return member.is_admin and account_permission(db, member, account) == "manage"


def list_grants(db: Session, member: Member, account_id: str) -> list[AccountGrantOut]:
    account = require_account_permission(db, member, account_id)
    if not _can_administer_grants(db, member, account):
        raise HTTPException(status_code=403, detail="无权管理此账户的共享授权")
    grants = db.scalars(
        select(AccountGrant)
        .where(AccountGrant.account_id == account.id)
        .order_by(AccountGrant.member_id)
    ).all()
    return [
        AccountGrantOut(memberId=item.member_id, permission=item.permission)
        for item in grants
        if item.member_id != account.owner_id
    ]


def set_grant(
    db: Session,
    member: Member,
    account_id: str,
    target_member_id: str,
    permission: str,
) -> AccountGrantOut:
    account = require_account_permission(db, member, account_id)
    if not _can_administer_grants(db, member, account):
        raise HTTPException(status_code=403, detail="无权管理此账户的共享授权")
    target = db.get(Member, target_member_id)
    if not target or not target.active:
        raise HTTPException(status_code=404, detail="家庭成员不存在")
    if target.id == account.owner_id:
        raise HTTPException(status_code=422, detail="账户所有者天然具有管理权限")
    grant = db.get(AccountGrant, (account.id, target.id))
    before = {"permission": grant.permission} if grant else None
    if grant:
        grant.permission = permission
    else:
        grant = AccountGrant(
            account_id=account.id,
            member_id=target.id,
            permission=permission,
        )
        db.add(grant)
    record_event(
        db,
        member,
        "account.grant_set",
        "account",
        account.id,
        before=before,
        after={"member_id": target.id, "permission": permission},
    )
    db.commit()
    return AccountGrantOut(memberId=target.id, permission=permission)


def delete_grant(db: Session, member: Member, account_id: str, target_member_id: str) -> None:
    account = require_account_permission(db, member, account_id)
    if not _can_administer_grants(db, member, account):
        raise HTTPException(status_code=403, detail="无权管理此账户的共享授权")
    grant = db.get(AccountGrant, (account.id, target_member_id))
    if not grant:
        raise HTTPException(status_code=404, detail="共享授权不存在")
    record_event(
        db,
        member,
        "account.grant_deleted",
        "account",
        account.id,
        before={"member_id": target_member_id, "permission": grant.permission},
    )
    db.delete(grant)
    db.commit()


def _validate_parent(db: Session, parent_id: str | None, kind: str) -> None:
    if parent_id is None:
        return
    parent = db.get(Category, parent_id)
    if not parent or not parent.active or parent.kind != kind:
        raise HTTPException(status_code=422, detail="上级分类不存在或收支类型不匹配")
    if parent.parent_id is not None:
        raise HTTPException(status_code=422, detail="分类最多支持两级")


def create_category(db: Session, member: Member, input_data: CategoryCreateInput) -> CategoryOut:
    _validate_parent(db, input_data.parent_id, input_data.kind)
    duplicate = db.scalar(
        select(Category.id).where(
            Category.kind == input_data.kind,
            Category.parent_id == input_data.parent_id,
            Category.name == input_data.name,
        )
    )
    if duplicate:
        raise HTTPException(status_code=409, detail="同级分类名称已存在")
    category = Category(
        name=input_data.name,
        kind=input_data.kind,
        parent_id=input_data.parent_id,
        icon=input_data.icon,
    )
    db.add(category)
    try:
        db.flush()
        record_event(
            db,
            member,
            "category.created",
            "category",
            category.id,
            after={"name": category.name, "kind": category.kind},
        )
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail="同级分类名称已存在") from error
    db.refresh(category)
    return CategoryOut.model_validate(category)


def update_category(
    db: Session,
    member: Member,
    category_id: str,
    input_data: CategoryUpdateInput,
) -> CategoryOut:
    category = db.scalar(select(Category).where(Category.id == category_id).with_for_update())
    if not category:
        raise HTTPException(status_code=404, detail="分类不存在")
    if category.version != input_data.expected_version:
        raise HTTPException(status_code=409, detail="分类已被其他人修改，请刷新后重试")
    before = {
        "name": category.name,
        "icon": category.icon,
        "active": category.active,
        "version": category.version,
    }
    new_name = input_data.name if input_data.name is not None else category.name
    duplicate = db.scalar(
        select(Category.id).where(
            Category.id != category.id,
            Category.kind == category.kind,
            Category.parent_id == category.parent_id,
            Category.name == new_name,
        )
    )
    if duplicate:
        raise HTTPException(status_code=409, detail="同级分类名称已存在")
    if input_data.name is not None:
        category.name = input_data.name
    if input_data.icon is not None:
        category.icon = input_data.icon
    if input_data.active is not None:
        category.active = input_data.active
    category.version += 1
    record_event(
        db,
        member,
        "category.updated",
        "category",
        category.id,
        before=before,
        after={
            "name": category.name,
            "icon": category.icon,
            "active": category.active,
            "version": category.version,
        },
    )
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail="同级分类名称已存在") from error
    db.refresh(category)
    return CategoryOut.model_validate(category)
