from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import LoginSession, Member, Passkey


class AuthRepository:
    """Persistence access for members, login sessions, and WebAuthn passkeys."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def get_active_member(self, member_id: str) -> Member | None:
        member = self.db.get(Member, member_id)
        if member and member.active:
            return member
        return None

    def get_active_member_by_username(self, username: str) -> Member | None:
        return self.db.scalar(
            select(Member).where(Member.username == username, Member.active.is_(True))
        )

    def list_active_members(self) -> list[Member]:
        return list(self.db.scalars(select(Member).where(Member.active.is_(True))).all())

    def create_login_session(
        self,
        *,
        member_id: str,
        token_hash: str,
        expires_at: datetime,
        user_agent: str,
    ) -> LoginSession:
        session = LoginSession(
            member_id=member_id,
            token_hash=token_hash,
            expires_at=expires_at,
            user_agent=user_agent[:300],
        )
        self.db.add(session)
        return session

    def get_active_session_by_hash(self, token_hash: str) -> LoginSession | None:
        return self.db.scalar(
            select(LoginSession).where(
                LoginSession.token_hash == token_hash,
                LoginSession.revoked_at.is_(None),
            )
        )

    def revoke_session_by_hash(self, token_hash: str, *, revoked_at: datetime) -> bool:
        login_session = self.db.scalar(
            select(LoginSession).where(LoginSession.token_hash == token_hash)
        )
        if login_session and login_session.revoked_at is None:
            login_session.revoked_at = revoked_at
            return True
        return False

    def list_member_passkeys(self, member_id: str) -> list[Passkey]:
        return list(
            self.db.scalars(
                select(Passkey)
                .where(Passkey.member_id == member_id)
                .order_by(Passkey.created_at.desc())
            ).all()
        )
