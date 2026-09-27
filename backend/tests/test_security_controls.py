from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from homedatabase_api.services import rate_limit


class FakeRedis:
    def __init__(self):
        self.counts: dict[str, int] = {}
        self.ttls: dict[str, int] = {}

    def eval(self, _script, key_count, *args):
        keys = args[:key_count]
        window, *limits = [int(value) for value in args[key_count:]]
        for index, (key, limit) in enumerate(zip(keys, limits, strict=True), start=1):
            self.counts[key] = self.counts.get(key, 0) + 1
            self.ttls[key] = window
            if self.counts[key] > limit:
                return [index, self.ttls[key]]
        return [0, 0]


def test_production_auth_rate_limit_is_distributed_and_returns_retry_after(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(rate_limit, "_client", lambda _url: fake)
    settings = SimpleNamespace(
        environment="production",
        redis_url="redis://private/0",
        auth_rate_limit_attempts=2,
        auth_rate_limit_ip_attempts=20,
        auth_rate_limit_subject_attempts=30,
        auth_rate_limit_window_seconds=60,
    )
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/auth/login",
            "headers": [],
            "client": ("192.0.2.10", 12345),
        }
    )
    rate_limit.enforce_auth_rate_limit(request, "son", settings)
    rate_limit.enforce_auth_rate_limit(request, "son", settings)
    with pytest.raises(HTTPException) as captured:
        rate_limit.enforce_auth_rate_limit(request, "son", settings)
    assert captured.value.status_code == 429
    assert captured.value.headers == {"Retry-After": "60"}


def test_json_request_body_limit_runs_before_validation(client):
    response = client.post(
        "/api/auth/login",
        content=b'"' + b"x" * (1024 * 1024) + b'"',
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413
    assert response.json()["detail"] == "JSON 请求体过大"

    chunked = client.post(
        "/api/auth/login",
        content=iter([b'"', b"x" * (1024 * 1024), b'"']),
        headers={"Content-Type": "application/json"},
    )
    assert chunked.status_code == 413
    assert chunked.json()["detail"] == "JSON 请求体过大"


def test_local_cors_allows_the_frontend_development_origin(client):
    response = client.options(
        "/api/auth/me",
        headers={
            "Origin": "http://localhost:5200",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5200"
    assert response.headers["access-control-allow-credentials"] == "true"


def test_openapi_declares_cookie_or_bearer_security_without_dev_headers(client):
    schema = client.get("/api/openapi.json").json()
    schemes = schema["components"]["securitySchemes"]
    assert schemes["SessionCookie"] == {
        "type": "apiKey",
        "description": "HttpOnly 会话 Cookie；浏览器请求必须使用 credentials: include。",
        "in": "cookie",
        "name": "hd_session",
    }
    assert schemes["BearerToken"]["scheme"] == "bearer"
    protected = schema["paths"]["/api/transactions"]["get"]
    assert {next(iter(item)) for item in protected["security"]} == {
        "SessionCookie",
        "BearerToken",
    }
    assert "401" in protected["responses"] and "403" in protected["responses"]
    parameter_names = {item["name"].casefold() for item in protected.get("parameters", [])}
    assert "authorization" not in parameter_names
    assert "x-dev-member-id" not in parameter_names
    assert "security" not in schema["paths"]["/api/auth/login"]["post"]
