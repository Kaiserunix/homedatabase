"""Standalone FastAPI router for read-only Layer-3 Ask Your Money & Household Digest."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, status
from fastapi.routing import APIRoute
from pydantic import ValidationError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..dependencies import get_current_member
from ..models import Member
from .adapters import DeterministicOfflineCFOAdapter
from .protocols import CFOAdapter
from .repository import SqlAlchemyLedgerReadRepository
from .schemas import (
    AskQuestionInput,
    AskYourMoneyResponse,
    CfoCapabilitiesResponse,
    DigestQueryInput,
    HouseholdDigestResponse,
    ScopeFilter,
)
from .service import NLQCFOService

DEFAULT_NLQ_ROUTER_PREFIX = "/nlq-cfo"
_DEFAULT_ADAPTER = DeterministicOfflineCFOAdapter()


def get_cfo_adapter() -> CFOAdapter:
    """FastAPI dependency returning the active CFO adapter (overridable in tests/deploy)."""
    return _DEFAULT_ADAPTER


def create_nlq_router(
    *,
    adapter_dependency: Callable[[], CFOAdapter] = get_cfo_adapter,
    router_prefix: str = DEFAULT_NLQ_ROUTER_PREFIX,
) -> APIRouter:
    """Create a decoupled, read-only APIRouter mountable on HomeDatabase FastAPI apps."""
    api = APIRouter(prefix=router_prefix, tags=["nlq-cfo"])

    @api.get("/capabilities", response_model=CfoCapabilitiesResponse)
    def cfo_capabilities(
        db: Session = Depends(get_db),
        member: Member = Depends(get_current_member),
        adapter: CFOAdapter = Depends(adapter_dependency),
    ) -> CfoCapabilitiesResponse:
        service = NLQCFOService(SqlAlchemyLedgerReadRepository(db), adapter)
        return service.capabilities(member.id)

    @api.post("/ask", response_model=AskYourMoneyResponse)
    def ask_your_money(
        payload: AskQuestionInput,
        db: Session = Depends(get_db),
        member: Member = Depends(get_current_member),
        adapter: CFOAdapter = Depends(adapter_dependency),
    ) -> AskYourMoneyResponse:
        service = NLQCFOService(SqlAlchemyLedgerReadRepository(db), adapter)
        return service.ask(member.id, payload)

    @api.get("/digest", response_model=HouseholdDigestResponse)
    def household_digest(
        month: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}$"),
        starts_on: date | None = Query(default=None, alias="startsOn"),
        ends_on: date | None = Query(default=None, alias="endsOn"),
        as_of_date: date | None = Query(default=None, alias="asOfDate"),
        scope: ScopeFilter = Query(default="all_visible"),
        max_highlights: int = Query(default=5, alias="maxHighlights", ge=1, le=20),
        max_citations: int = Query(default=30, alias="maxCitations", ge=1, le=200),
        db: Session = Depends(get_db),
        member: Member = Depends(get_current_member),
        adapter: CFOAdapter = Depends(adapter_dependency),
    ) -> HouseholdDigestResponse:
        try:
            query = DigestQueryInput(
                month=month,
                startsOn=starts_on,
                endsOn=ends_on,
                asOfDate=as_of_date,
                scope=scope,
                maxHighlights=max_highlights,
                maxCitations=max_citations,
            )
        except ValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=exc.errors()[0]["msg"],
            ) from None
        service = NLQCFOService(SqlAlchemyLedgerReadRepository(db), adapter)
        return service.digest(member.id, query)

    return api


router = create_nlq_router()


def collect_app_route_keys(app: FastAPI) -> list[tuple[str, str]]:
    """Collect all (HTTP_METHOD, path) route keys across direct and included FastAPI routers."""
    keys: list[tuple[str, str]] = []

    def _walk(routes: list[object], prefix: str) -> None:
        for route in routes:
            if isinstance(route, APIRoute):
                for method in sorted(route.methods or ()):
                    keys.append((method.upper(), f"{prefix}{route.path}"))
            elif hasattr(route, "original_router") and hasattr(route, "include_context"):
                nested_prefix = f"{prefix}{route.include_context.prefix}"
                _walk(list(route.original_router.routes), nested_prefix)

    _walk(list(app.routes), "")
    return keys


def mount_nlq_cfo(
    app: FastAPI,
    *,
    prefix: str | None = None,
    router_prefix: str = DEFAULT_NLQ_ROUTER_PREFIX,
    adapter_dependency: Callable[[], CFOAdapter] = get_cfo_adapter,
) -> APIRouter:
    """Mount the standalone read-only NLQ CFO router onto an existing FastAPI application."""
    api_prefix = get_settings().api_prefix if prefix is None else prefix
    nlq_router = create_nlq_router(
        adapter_dependency=adapter_dependency,
        router_prefix=router_prefix,
    )
    existing = set(collect_app_route_keys(app))
    incoming = {
        (method.upper(), f"{api_prefix}{route.path}")
        for route in nlq_router.routes
        if isinstance(route, APIRoute)
        for method in (route.methods or ())
    }
    collisions = sorted(existing & incoming)
    if collisions:
        raise ValueError(f"Duplicate FastAPI route(s) detected when mounting nlq_cfo: {collisions}")
    app.include_router(nlq_router, prefix=api_prefix)
    return nlq_router
