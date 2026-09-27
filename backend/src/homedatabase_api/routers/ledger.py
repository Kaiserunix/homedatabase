from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import get_current_member, require_admin
from ..models import Member
from ..schemas import (
    AccountCreateInput,
    AccountGrantInput,
    AccountGrantOut,
    AccountOut,
    AccountUpdateInput,
    CategoryCreateInput,
    CategoryOut,
    CategoryUpdateInput,
    DeleteInput,
    NewTransactionInput,
    TransactionOut,
    TransactionRestoreInput,
    TransactionUpdateInput,
)
from ..services import ledger as service
from ..services.presenters import present_transaction

router = APIRouter(tags=["ledger"])


@router.get("/accounts", response_model=list[AccountOut])
def accounts(db: Session = Depends(get_db), member: Member = Depends(get_current_member)):
    return service.list_accounts(db, member)


@router.post("/accounts", response_model=AccountOut, status_code=201)
def create_account(
    payload: AccountCreateInput,
    db: Session = Depends(get_db),
    member: Member = Depends(get_current_member),
):
    from ..services.catalog import create_account as create_account_service

    return create_account_service(db, member, payload)


@router.patch("/accounts/{account_id}", response_model=AccountOut)
def update_account(
    account_id: str,
    payload: AccountUpdateInput,
    db: Session = Depends(get_db),
    member: Member = Depends(get_current_member),
):
    from ..services.catalog import update_account as update_account_service

    return update_account_service(db, member, account_id, payload)


@router.get("/accounts/{account_id}/grants", response_model=list[AccountGrantOut])
def account_grants(
    account_id: str,
    db: Session = Depends(get_db),
    member: Member = Depends(get_current_member),
):
    from ..services.catalog import list_grants

    return list_grants(db, member, account_id)


@router.put("/accounts/{account_id}/grants/{member_id}", response_model=AccountGrantOut)
def set_account_grant(
    account_id: str,
    member_id: str,
    payload: AccountGrantInput,
    db: Session = Depends(get_db),
    member: Member = Depends(get_current_member),
):
    from ..services.catalog import set_grant

    return set_grant(db, member, account_id, member_id, payload.permission)


@router.delete("/accounts/{account_id}/grants/{member_id}", status_code=204)
def delete_account_grant(
    account_id: str,
    member_id: str,
    db: Session = Depends(get_db),
    member: Member = Depends(get_current_member),
) -> Response:
    from ..services.catalog import delete_grant

    delete_grant(db, member, account_id, member_id)
    return Response(status_code=204)


@router.get("/categories", response_model=list[CategoryOut])
def categories(
    include_inactive: bool = Query(default=False, alias="includeInactive"),
    db: Session = Depends(get_db),
    member: Member = Depends(get_current_member),
):
    if include_inactive:
        require_admin(member)
    return [
        CategoryOut.model_validate(item)
        for item in service.list_categories(db, include_inactive=include_inactive)
    ]


@router.post("/categories", response_model=CategoryOut, status_code=201)
def create_category(
    payload: CategoryCreateInput,
    db: Session = Depends(get_db),
    member: Member = Depends(get_current_member),
):
    from ..services.catalog import create_category as create_category_service

    require_admin(member)
    return create_category_service(db, member, payload)


@router.patch("/categories/{category_id}", response_model=CategoryOut)
def update_category(
    category_id: str,
    payload: CategoryUpdateInput,
    db: Session = Depends(get_db),
    member: Member = Depends(get_current_member),
):
    from ..services.catalog import update_category as update_category_service

    require_admin(member)
    return update_category_service(db, member, category_id, payload)


@router.get("/transactions", response_model=list[TransactionOut])
def transactions(
    type: str | None = Query(default=None),
    account_id: str | None = Query(default=None, alias="accountId"),
    month: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}$"),
    keyword: str | None = Query(default=None, max_length=200),
    db: Session = Depends(get_db),
    member: Member = Depends(get_current_member),
):
    return service.list_transactions(
        db,
        member,
        type_filter=type,
        account_id=account_id,
        month=month,
        keyword=keyword,
    )


@router.post("/transactions", response_model=TransactionOut, status_code=201)
def create_transaction(
    payload: NewTransactionInput,
    db: Session = Depends(get_db),
    member: Member = Depends(get_current_member),
):
    transaction = service.create_transaction(db, member, payload)
    return present_transaction(db, member, transaction)


@router.get("/transactions/recycle-bin", response_model=list[TransactionOut])
def recycled_transactions(
    db: Session = Depends(get_db),
    member: Member = Depends(get_current_member),
):
    return service.list_recycled_transactions(db, member)


@router.get("/transactions/{transaction_id}", response_model=TransactionOut)
def transaction_detail(
    transaction_id: str,
    db: Session = Depends(get_db),
    member: Member = Depends(get_current_member),
):
    return present_transaction(db, member, service.get_transaction(db, member, transaction_id))


@router.post("/transactions/{transaction_id}/restore", response_model=TransactionOut)
def restore_transaction(
    transaction_id: str,
    payload: TransactionRestoreInput,
    db: Session = Depends(get_db),
    member: Member = Depends(get_current_member),
):
    return service.restore_transaction(db, member, transaction_id, payload.expected_version)


@router.patch("/transactions/{transaction_id}", response_model=TransactionOut)
def update_transaction(
    transaction_id: str,
    payload: TransactionUpdateInput,
    db: Session = Depends(get_db),
    member: Member = Depends(get_current_member),
):
    return service.update_transaction(db, member, transaction_id, payload)


@router.delete("/transactions/{transaction_id}", status_code=204)
def delete_transaction(
    transaction_id: str,
    payload: DeleteInput,
    db: Session = Depends(get_db),
    member: Member = Depends(get_current_member),
) -> Response:
    service.delete_transaction(db, member, transaction_id, payload.reason)
    return Response(status_code=204)
