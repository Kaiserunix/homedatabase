from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    Security,
    UploadFile,
    status,
)
from fastapi.routing import APIRoute
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from starlette.responses import Response

from ..config import Settings, get_settings
from ..database import get_db, get_session_factory
from ..dependencies import bearer_scheme, get_current_member, session_cookie_scheme
from ..models import Member
from ..schemas import (
    CandidateIdsInput,
    ImportBatchOut,
    ImportCandidateOut,
    ImportCandidatePatch,
    ImportJobOut,
)
from ..services import imports as service
from ..services.presenters import present_batch


def _install_upload_limit(request: Request, settings: Settings) -> None:
    maximum = settings.source_max_total_bytes
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            declared_bytes = int(declared)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Content-Length 格式无效") from exc
        if declared_bytes < 0:
            raise HTTPException(status_code=400, detail="Content-Length 不能为负数")
        if declared_bytes > maximum:
            raise HTTPException(
                status_code=413, detail="上传请求总大小（含 multipart 开销）超过限制"
            )

    original_receive = request._receive
    received_bytes = 0

    async def limited_receive():
        nonlocal received_bytes
        message = await original_receive()
        if message["type"] == "http.request":
            received_bytes += len(message.get("body", b""))
            if received_bytes > maximum:
                raise HTTPException(
                    status_code=413,
                    detail="上传请求总大小（含 multipart 开销）超过限制",
                )
        return message

    # The custom route installs this before FastAPI enters request_body_to_args,
    # so chunked requests and a false Content-Length are limited as well.
    request._receive = limited_receive


async def _settings_for_request(request: Request) -> Settings:
    provider = request.app.dependency_overrides.get(get_settings, get_settings)
    value = provider()
    if inspect.isawaitable(value):
        value = await value
    return value


class AuthenticatedMultipartRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        original = super().get_route_handler()
        if self.name != "upload_import":
            return original

        async def authenticated_handler(request: Request) -> Response:
            settings = await _settings_for_request(request)
            authorization = request.headers.get("authorization", "")
            scheme, _, credentials = authorization.partition(" ")
            bearer = (
                HTTPAuthorizationCredentials(scheme=scheme, credentials=credentials.strip())
                if scheme.casefold() == "bearer" and credentials.strip()
                else None
            )
            with get_session_factory()() as db:
                member = get_current_member(
                    request,
                    db=db,
                    settings=settings,
                    cookie_token=None,
                    bearer=bearer,
                )
                request.state.upload_member_id = member.id
            _install_upload_limit(request, settings)
            return await original(request)

        return authenticated_handler


def _preauthenticated_upload_member(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    _cookie_token: Annotated[str | None, Security(session_cookie_scheme)],
    _bearer: Annotated[HTTPAuthorizationCredentials | None, Security(bearer_scheme)],
) -> Member:
    member_id = getattr(request.state, "upload_member_id", None)
    member = db.get(Member, member_id) if member_id else None
    if not member or not member.active:
        raise HTTPException(status_code=401, detail="请先登录")
    return member


router = APIRouter(tags=["imports"], route_class=AuthenticatedMultipartRoute)


@router.post(
    "/imports",
    response_model=ImportBatchOut,
    status_code=status.HTTP_201_CREATED,
    description=(
        "上传一个或多个账单文件。总大小限制作用于完整 multipart 请求体，"
        "包含边界、文件头和普通表单字段。"
    ),
)
async def upload_import(
    files: Annotated[list[UploadFile], File(description="一个或多个账单文件")],
    db: Annotated[Session, Depends(get_db)],
    member: Annotated[Member, Depends(_preauthenticated_upload_member)],
    settings: Annotated[Settings, Depends(get_settings)],
    source: Annotated[str, Form()] = "unknown",
    target_account_id: Annotated[str | None, Form(alias="targetAccountId")] = None,
):
    return await service.create_import_batch(
        db,
        member,
        files,
        source=source,
        target_account_id=target_account_id,
        settings=settings,
    )


@router.get("/imports/{batch_id}/status", response_model=ImportJobOut)
def get_import_status(
    batch_id: str,
    db: Session = Depends(get_db),
    member: Member = Depends(get_current_member),
):
    return service.get_import_job(db, member, batch_id)


@router.get("/import-batches", response_model=list[ImportBatchOut])
def list_batches(db: Session = Depends(get_db), member: Member = Depends(get_current_member)):
    return service.list_batches(db, member)


@router.get("/import-batches/{batch_id}", response_model=ImportBatchOut)
def get_batch(
    batch_id: str, db: Session = Depends(get_db), member: Member = Depends(get_current_member)
):
    return present_batch(service.get_batch(db, member, batch_id))


@router.patch(
    "/import-batches/{batch_id}/candidates/{candidate_id}",
    response_model=ImportCandidateOut,
)
def patch_candidate(
    batch_id: str,
    candidate_id: str,
    payload: ImportCandidatePatch,
    db: Session = Depends(get_db),
    member: Member = Depends(get_current_member),
):
    return service.patch_candidate(db, member, batch_id, candidate_id, payload)


@router.post("/import-batches/{batch_id}/confirm", response_model=ImportBatchOut)
def confirm(
    batch_id: str,
    payload: CandidateIdsInput,
    db: Session = Depends(get_db),
    member: Member = Depends(get_current_member),
):
    return service.confirm_candidates(db, member, batch_id, payload.candidate_ids)


@router.post("/import-batches/{batch_id}/skip-duplicates", response_model=ImportBatchOut)
def skip_duplicates(
    batch_id: str,
    payload: CandidateIdsInput,
    db: Session = Depends(get_db),
    member: Member = Depends(get_current_member),
):
    return service.skip_candidates(db, member, batch_id, payload.candidate_ids)


@router.post("/import-batches/{batch_id}/undo", response_model=ImportBatchOut)
def undo(
    batch_id: str, db: Session = Depends(get_db), member: Member = Depends(get_current_member)
):
    return service.undo_batch(db, member, batch_id)
