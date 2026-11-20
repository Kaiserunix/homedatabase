from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import Member
from ..repositories.auth import AuthRepository
from ..schemas import AuthOut, MemberOut
from ..security import hash_token, new_session_token, verify_password


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def present_member(member: Member) -> MemberOut:
    return MemberOut.model_validate(member)


def authenticate_password(db: Session, *, username: str, password: str) -> Member:
    repo = AuthRepository(db)
    member = repo.get_active_member_by_username(username)
    if not member or not verify_password(password, member.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    return member


def issue_login_session(
    db: Session,
    *,
    member: Member,
    user_agent: str,
    settings: Settings,
) -> tuple[str, AuthOut]:
    token = new_session_token()
    expires_at = datetime.now(UTC) + timedelta(seconds=settings.session_ttl_seconds)
    repo = AuthRepository(db)
    repo.create_login_session(
        member_id=member.id,
        token_hash=hash_token(token),
        expires_at=expires_at,
        user_agent=user_agent,
    )
    db.commit()
    return token, AuthOut(member=present_member(member), expiresAt=expires_at)


def resolve_session_member(db: Session, token: str) -> Member | None:
    repo = AuthRepository(db)
    login = repo.get_active_session_by_hash(hash_token(token))
    if not login or _aware(login.expires_at) <= datetime.now(UTC):
        return None
    member = repo.get_active_member(login.member_id)
    if not member:
        return None
    login.last_used_at = datetime.now(UTC)
    db.commit()
    return member


def resolve_dev_member(db: Session, member_id: str) -> Member | None:
    return AuthRepository(db).get_active_member(member_id)


def revoke_session_token(db: Session, token: str | None) -> None:
    if not token:
        return
    repo = AuthRepository(db)
    if repo.revoke_session_by_hash(hash_token(token), revoked_at=datetime.now(UTC)):
        db.commit()
