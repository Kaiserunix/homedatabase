from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..database import get_db
from ..dependencies import get_current_member
from ..models import Member
from ..schemas import (
    CashflowRadarInput,
    CashflowRadarOut,
    CfoAskInput,
    CfoAskOut,
    CfoCapabilitiesOut,
)
from ..services import cfo as service

router = APIRouter(prefix="/cfo", tags=["cfo"])


@router.get("/capabilities", response_model=CfoCapabilitiesOut)
def capabilities(
    _member: Member = Depends(get_current_member),
    settings: Settings = Depends(get_settings),
) -> CfoCapabilitiesOut:
    return service.get_capabilities(settings)


@router.post("/ask", response_model=CfoAskOut)
def ask(
    payload: CfoAskInput,
    db: Session = Depends(get_db),
    member: Member = Depends(get_current_member),
    settings: Settings = Depends(get_settings),
) -> CfoAskOut:
    return service.ask_cfo(db, member, payload, settings=settings)


@router.post("/cashflow-radar", response_model=CashflowRadarOut)
def cashflow_radar(
    payload: CashflowRadarInput,
    db: Session = Depends(get_db),
    member: Member = Depends(get_current_member),
    settings: Settings = Depends(get_settings),
) -> CashflowRadarOut:
    return service.build_cashflow_radar(db, member, payload, settings=settings)
