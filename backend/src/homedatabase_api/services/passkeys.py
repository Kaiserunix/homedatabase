from __future__ import annotations

import base64
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from webauthn import (
    base64url_to_bytes,
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from ..config import Settings
from ..models import Member, Passkey, WebAuthnChallenge, utcnow
from ..schemas import WebAuthnCeremonyOut
from .audit import record_event


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _enum_value(value: object) -> str:
    return str(getattr(value, "value", value))


def _new_ceremony(
    db: Session,
    member: Member,
    *,
    kind: str,
    challenge: bytes,
    settings: Settings,
) -> WebAuthnChallenge:
    ceremony = WebAuthnChallenge(
        kind=kind,
        member_id=member.id,
        challenge=_b64url(challenge),
        expires_at=datetime.now(UTC) + timedelta(seconds=settings.webauthn_challenge_ttl_seconds),
    )
    db.add(ceremony)
    db.commit()
    return ceremony


def _load_ceremony(
    db: Session,
    ceremony_id: str,
    *,
    kind: str,
    member_id: str | None = None,
) -> WebAuthnChallenge:
    # Serialize verification attempts for the same challenge. Without the row
    # lock, two concurrent requests can both observe used_at=NULL and replay a
    # single signed assertion into two sessions.
    ceremony = db.scalar(
        select(WebAuthnChallenge).where(WebAuthnChallenge.id == ceremony_id).with_for_update()
    )
    invalid = (
        not ceremony
        or ceremony.kind != kind
        or ceremony.used_at is not None
        or _aware(ceremony.expires_at) <= datetime.now(UTC)
        or (member_id is not None and ceremony.member_id != member_id)
    )
    if invalid:
        raise HTTPException(status_code=400, detail="Passkey 请求已失效，请重新开始")
    return ceremony


def begin_registration(db: Session, member: Member, settings: Settings) -> WebAuthnCeremonyOut:
    challenge = secrets.token_bytes(32)
    existing = db.scalars(select(Passkey).where(Passkey.member_id == member.id)).all()
    options = generate_registration_options(
        rp_id=settings.webauthn_rp_id,
        rp_name=settings.webauthn_rp_name,
        user_id=member.id.encode("utf-8"),
        user_name=member.username,
        user_display_name=member.name,
        challenge=challenge,
        timeout=settings.webauthn_challenge_ttl_seconds * 1000,
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(item.credential_id))
            for item in existing
        ],
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            require_resident_key=True,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
    )
    ceremony = _new_ceremony(
        db, member, kind="registration", challenge=challenge, settings=settings
    )
    return WebAuthnCeremonyOut(
        ceremonyId=ceremony.id,
        options=json.loads(options_to_json(options)),
    )


def finish_registration(
    db: Session,
    member: Member,
    settings: Settings,
    *,
    ceremony_id: str,
    name: str,
    credential: dict[str, Any],
) -> Passkey:
    ceremony = _load_ceremony(db, ceremony_id, kind="registration", member_id=member.id)
    try:
        verification = verify_registration_response(
            credential=credential,
            expected_challenge=base64url_to_bytes(ceremony.challenge),
            expected_rp_id=settings.webauthn_rp_id,
            expected_origin=settings.webauthn_origins,
            require_user_verification=True,
        )
    except (WebAuthnException, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Passkey 注册验证失败") from exc

    credential_id = _b64url(verification.credential_id)
    if db.scalar(select(Passkey.id).where(Passkey.credential_id == credential_id)):
        raise HTTPException(status_code=409, detail="该 Passkey 已经注册")
    response = credential.get("response") or {}
    passkey = Passkey(
        member_id=member.id,
        name=name,
        credential_id=credential_id,
        public_key=_b64url(verification.credential_public_key),
        sign_count=verification.sign_count,
        transports=list(response.get("transports") or []),
        device_type=_enum_value(verification.credential_device_type),
        backed_up=bool(verification.credential_backed_up),
    )
    ceremony.used_at = utcnow()
    db.add(passkey)
    db.flush()
    record_event(
        db,
        member,
        "passkey.registered",
        "passkey",
        passkey.id,
        after={"name": name, "device_type": passkey.device_type},
    )
    db.commit()
    db.refresh(passkey)
    return passkey


def begin_authentication(db: Session, username: str, settings: Settings) -> WebAuthnCeremonyOut:
    member = db.scalar(select(Member).where(Member.username == username, Member.active.is_(True)))
    if not member:
        raise HTTPException(status_code=400, detail="此用户尚未配置可用的 Passkey")
    passkeys = db.scalars(select(Passkey).where(Passkey.member_id == member.id)).all()
    if not passkeys:
        raise HTTPException(status_code=400, detail="此用户尚未配置可用的 Passkey")
    challenge = secrets.token_bytes(32)
    options = generate_authentication_options(
        rp_id=settings.webauthn_rp_id,
        challenge=challenge,
        timeout=settings.webauthn_challenge_ttl_seconds * 1000,
        allow_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(item.credential_id))
            for item in passkeys
        ],
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    ceremony = _new_ceremony(
        db, member, kind="authentication", challenge=challenge, settings=settings
    )
    return WebAuthnCeremonyOut(
        ceremonyId=ceremony.id,
        options=json.loads(options_to_json(options)),
    )


def finish_authentication(
    db: Session,
    settings: Settings,
    *,
    ceremony_id: str,
    credential: dict[str, Any],
) -> Member:
    ceremony = _load_ceremony(db, ceremony_id, kind="authentication")
    credential_id = credential.get("id")
    passkey = db.scalar(
        select(Passkey).where(
            Passkey.credential_id == credential_id,
            Passkey.member_id == ceremony.member_id,
        )
    )
    if not passkey:
        raise HTTPException(status_code=400, detail="Passkey 登录验证失败")
    try:
        verification = verify_authentication_response(
            credential=credential,
            expected_challenge=base64url_to_bytes(ceremony.challenge),
            expected_rp_id=settings.webauthn_rp_id,
            expected_origin=settings.webauthn_origins,
            credential_public_key=base64url_to_bytes(passkey.public_key),
            credential_current_sign_count=passkey.sign_count,
            require_user_verification=True,
        )
    except (WebAuthnException, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Passkey 登录验证失败") from exc
    member = db.get(Member, ceremony.member_id)
    if not member or not member.active:
        raise HTTPException(status_code=401, detail="用户不可用")
    passkey.sign_count = verification.new_sign_count
    passkey.device_type = _enum_value(verification.credential_device_type)
    passkey.backed_up = bool(verification.credential_backed_up)
    passkey.last_used_at = utcnow()
    ceremony.used_at = utcnow()
    record_event(
        db,
        member,
        "passkey.authenticated",
        "passkey",
        passkey.id,
    )
    db.commit()
    return member


def delete_passkey(db: Session, member: Member, passkey_id: str) -> None:
    passkey = db.get(Passkey, passkey_id)
    if not passkey or passkey.member_id != member.id:
        raise HTTPException(status_code=404, detail="Passkey 不存在")
    record_event(
        db,
        member,
        "passkey.deleted",
        "passkey",
        passkey.id,
        before={"name": passkey.name},
    )
    db.delete(passkey)
    db.commit()
