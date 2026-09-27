from __future__ import annotations

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__
from .config import get_settings
from .database import create_schema, get_session_factory
from .routers import auth, imports, ledger, splits, system
from .seed import seed_demo_data


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    if settings.auto_create_schema:
        create_schema()
    if settings.seed_demo:
        if not settings.bootstrap_password:
            raise RuntimeError(
                "HOMEDATABASE_BOOTSTRAP_PASSWORD is required when demo seeding is enabled"
            )
        with get_session_factory()() as db:
            seed_demo_data(db, password=settings.bootstrap_password)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="HomeDatabase API",
        version=__version__,
        docs_url=f"{settings.api_prefix}/docs",
        openapi_url=f"{settings.api_prefix}/openapi.json",
        lifespan=lifespan,
    )
    if settings.environment in {"local", "test"}:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.webauthn_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    else:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)

    @app.middleware("http")
    async def private_api_headers(request: Request, call_next):
        content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
        content_length = request.headers.get("content-length")
        unsafe = request.method in {"POST", "PUT", "PATCH", "DELETE"}
        is_import_upload = (
            request.url.path == f"{settings.api_prefix}/imports"
            and request.method == "POST"
            and content_type == "multipart/form-data"
        )
        if unsafe and not is_import_upload and content_length:
            try:
                too_large = int(content_length) > settings.max_json_body_bytes
            except ValueError:
                too_large = True
            if too_large:
                return JSONResponse(status_code=413, content={"detail": "JSON 请求体过大"})
        if unsafe and not is_import_upload:
            # Content-Length is optional (for example with chunked transfer).
            # Read incrementally and stop at the configured ceiling before
            # FastAPI/Starlette can buffer or parse an unbounded anonymous body.
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > settings.max_json_body_bytes:
                    return JSONResponse(status_code=413, content={"detail": "JSON 请求体过大"})
            request._body = bytes(body)
        has_session = bool(request.cookies.get(settings.session_cookie_name))
        origin = request.headers.get("origin")
        if (
            settings.environment == "production"
            and unsafe
            and has_session
            and origin not in settings.webauthn_origins
        ):
            return JSONResponse(status_code=403, content={"detail": "请求来源校验失败"})
        response = await call_next(request)
        if request.url.path.startswith(settings.api_prefix):
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
            if settings.environment == "production":
                response.headers["Strict-Transport-Security"] = (
                    "max-age=31536000; includeSubDomains"
                )
        return response

    for router in (auth.router, ledger.router, imports.router, splits.router, system.router):
        app.include_router(router, prefix=settings.api_prefix)

    def documented_openapi():
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            routes=app.routes,
        )
        security_schemes = schema.setdefault("components", {}).setdefault("securitySchemes", {})
        if "SessionCookie" in security_schemes:
            security_schemes["SessionCookie"]["name"] = settings.session_cookie_name
        for path_item in schema.get("paths", {}).values():
            for operation in path_item.values():
                if not isinstance(operation, dict) or not operation.get("security"):
                    continue
                responses = operation.setdefault("responses", {})
                responses.setdefault("401", {"description": "未登录或会话已过期"})
                responses.setdefault("403", {"description": "已登录但权限不足"})
        app.openapi_schema = schema
        return schema

    app.openapi = documented_openapi
    return app


app = create_app()


def run() -> None:
    uvicorn.run("homedatabase_api.main:app", host="127.0.0.1", port=8000, reload=False)
