from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..dependencies import get_current_member
from ..models import Member
from ..schemas import NewSplitInput, SplitOut, SplitResponseInput, SplitShareUpdateInput
from ..services import splits as service

router = APIRouter(prefix="/splits", tags=["splits"])


@router.get("", response_model=list[SplitOut])
def list_splits(db: Session = Depends(get_db), member: Member = Depends(get_current_member)):
    return service.list_splits(db, member)


@router.post("", response_model=SplitOut, status_code=201)
def create_split(
    payload: NewSplitInput,
    db: Session = Depends(get_db),
    member: Member = Depends(get_current_member),
):
    return service.create_split(db, member, payload)


@router.post("/{split_id}/respond", response_model=SplitOut)
def respond(
    split_id: str,
    payload: SplitResponseInput,
    db: Session = Depends(get_db),
    member: Member = Depends(get_current_member),
):
    return service.respond_to_split(db, member, split_id, payload)


@router.patch("/{split_id}/shares/{member_id}", response_model=SplitOut)
def update_share(
    split_id: str,
    member_id: str,
    payload: SplitShareUpdateInput,
    db: Session = Depends(get_db),
    member: Member = Depends(get_current_member),
):
    return service.update_share(
        db,
        member,
        split_id,
        member_id,
        payload.share_cents,
        payload.expected_version,
    )
