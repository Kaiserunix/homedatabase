from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..database import get_db
from ..dependencies import get_current_member
from ..models import Member
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
from ..services import auth as auth_service
from ..services import passkeys as passkey_service
from ..services.presenters import present_passkey
from ..services.rate_limit import enforce_auth_rate_limit

router = APIRouter(prefix="/auth", tags=["auth"])


def _start_session(
    db: Session,
    member: Member,
    request: Request,
    response: Response,
    settings: Settings,
) -> AuthOut:
    token, auth_out = auth_service.issue_login_session(
        db,
        member=member,
        user_agent=request.headers.get("user-agent", ""),
        settings=settings,
    )
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="strict",
        path="/",
    )
    return auth_out


@router.post("/login", response_model=AuthOut)
def login(
    payload: LoginInput,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AuthOut:
    enforce_auth_rate_limit(request, payload.username, settings)
    member = auth_service.authenticate_password(
        db,
        username=payload.username,
        password=payload.password,
    )
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
    return auth_service.present_member(member)


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
    auth_service.revoke_session_token(db, token)
    response.delete_cookie(settings.session_cookie_name, path="/")
