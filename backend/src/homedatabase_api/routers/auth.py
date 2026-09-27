from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..database import get_db
from ..dependencies import get_current_member
from ..models import LoginSession, Member
from ..schemas import (
    AuthOut,
    LoginInput,
    MemberOut,
    PasskeyAuthenticationBeginInput,
    PasskeyAuthenticationFinishInput,
    PasskeyOut,
    PasskeyRegistrationFinishInput,
    WebAuthnCeremonyOut,
)
from ..security import hash_token, new_session_token, verify_password
from ..services import passkeys as passkey_service
from ..services.presenters import present_passkey
from ..services.rate_limit import enforce_auth_rate_limit

router = APIRouter(prefix="/auth", tags=["auth"])


def _member_out(member: Member) -> MemberOut:
    return MemberOut.model_validate(member)


def _start_session(
    db: Session,
    member: Member,
    request: Request,
    response: Response,
    settings: Settings,
) -> AuthOut:
    token = new_session_token()
    expires_at = datetime.now(UTC) + timedelta(seconds=settings.session_ttl_seconds)
    db.add(
        LoginSession(
            member_id=member.id,
            token_hash=hash_token(token),
            expires_at=expires_at,
            user_agent=request.headers.get("user-agent", "")[:300],
        )
    )
    db.commit()
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="strict",
        path="/",
    )
    return AuthOut(member=_member_out(member), expiresAt=expires_at)


@router.post("/login", response_model=AuthOut)
def login(
    payload: LoginInput,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AuthOut:
    enforce_auth_rate_limit(request, payload.username, settings)
    member = db.scalar(
        select(Member).where(Member.username == payload.username, Member.active.is_(True))
    )
    if not member or not verify_password(payload.password, member.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    return _start_session(db, member, request, response, settings)


@router.post("/passkeys/registration/options", response_model=WebAuthnCeremonyOut)
def registration_options(
    db: Session = Depends(get_db),
    member: Member = Depends(get_current_member),
    settings: Settings = Depends(get_settings),
):
    return passkey_service.begin_registration(db, member, settings)


@router.post("/passkeys/registration/verify", response_model=PasskeyOut, status_code=201)
def registration_verify(
    payload: PasskeyRegistrationFinishInput,
    db: Session = Depends(get_db),
    member: Member = Depends(get_current_member),
    settings: Settings = Depends(get_settings),
):
    passkey = passkey_service.finish_registration(
        db,
        member,
        settings,
        ceremony_id=payload.ceremony_id,
        name=payload.name,
        credential=payload.credential,
    )
    return present_passkey(passkey)


@router.post("/passkeys/authentication/options", response_model=WebAuthnCeremonyOut)
def authentication_options(
    payload: PasskeyAuthenticationBeginInput,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    enforce_auth_rate_limit(request, payload.username, settings)
    return passkey_service.begin_authentication(db, payload.username, settings)


@router.post("/passkeys/authentication/verify", response_model=AuthOut)
def authentication_verify(
    payload: PasskeyAuthenticationFinishInput,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    enforce_auth_rate_limit(request, payload.ceremony_id, settings)
    member = passkey_service.finish_authentication(
        db,
        settings,
        ceremony_id=payload.ceremony_id,
        credential=payload.credential,
    )
    return _start_session(db, member, request, response, settings)


@router.get("/me", response_model=MemberOut)
def me(member: Member = Depends(get_current_member)) -> MemberOut:
    return _member_out(member)


@router.post("/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    _member: Member = Depends(get_current_member),
) -> None:
    token = request.cookies.get(settings.session_cookie_name)
    authorization = request.headers.get("authorization", "")
    if not token and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if token:
        login_session = db.scalar(
            select(LoginSession).where(LoginSession.token_hash == hash_token(token))
        )
        if login_session and login_session.revoked_at is None:
            login_session.revoked_at = datetime.now(UTC)
            db.commit()
    response.delete_cookie(settings.session_cookie_name, path="/")
