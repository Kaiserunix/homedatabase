from __future__ import annotations

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyCookie, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .database import get_db
from .models import Member
from .services.auth import resolve_dev_member, resolve_session_member

session_cookie_scheme = APIKeyCookie(
    name="hd_session",
    scheme_name="SessionCookie",
    description="HttpOnly 会话 Cookie；浏览器请求必须使用 credentials: include。",
    auto_error=False,
)
bearer_scheme = HTTPBearer(
    scheme_name="BearerToken",
    description="仅供受控 CLI/运维客户端使用的同一会话令牌。",
    auto_error=False,
)


def get_current_member(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    cookie_token: str | None = Security(session_cookie_scheme),
    bearer: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
) -> Member:
    # `cookie_token` exists so FastAPI documents the cookie security scheme.
    # Runtime lookup uses the configured cookie name, which may differ from the
    # default shown by the dependency object.
    _ = cookie_token
    token = request.cookies.get(settings.session_cookie_name)
    if not token and bearer:
        token = bearer.credentials.strip()
    if token:
        member = resolve_session_member(db, token)
        if member is not None:
            return member

    if settings.environment in {"local", "test"}:
        dev_id = request.headers.get("x-dev-member-id") or settings.dev_member_id
        if dev_id:
            member = resolve_dev_member(db, dev_id)
            if member is not None:
                return member

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")


def require_admin(member: Member = Depends(get_current_member)) -> Member:
    if not member.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要家庭管理员权限")
    return member
