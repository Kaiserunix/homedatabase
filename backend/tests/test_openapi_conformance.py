from __future__ import annotations

import importlib.util
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import as_member
from fastapi.testclient import TestClient
from pydantic import ValidationError as PydanticValidationError

from homedatabase_api import schemas as api_schemas
from homedatabase_api.main import app
from homedatabase_api.money import MAX_SAFE_MINOR

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent


def _load_drift_validator():
    script_path = BACKEND_DIR / "scripts/validate_contract_drift.py"
    spec = importlib.util.spec_from_file_location("validate_contract_drift", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DRIFT = _load_drift_validator()


def _load_committed_openapi() -> dict:
    return DRIFT.load_json_file(REPO_ROOT / "openapi.yaml")


def _load_schema_fixtures() -> dict:
    return DRIFT.load_json_file(REPO_ROOT / "contracts/fixtures/schemas.json")


def _load_endpoints_manifest() -> dict:
    return DRIFT.load_json_file(REPO_ROOT / "contracts/fixtures/endpoints.json")


def _assert_response_conforms(
    spec: dict,
    response,
    *,
    method: str,
    openapi_path: str,
    expected_status: int,
    expected_content_type: str | None = "application/json",
    exercised: set[tuple[str, str]] | None = None,
) -> object:
    assert response.status_code == expected_status, (
        f"{method.upper()} {openapi_path}: expected {expected_status}, "
        f"got {response.status_code} ({response.text})"
    )
    if openapi_path.startswith("/api/"):
        assert response.headers.get("cache-control") == "no-store"
        assert response.headers.get("x-content-type-options") == "nosniff"
        assert response.headers.get("referrer-policy") == "no-referrer"

    op = spec["paths"][openapi_path][method.lower()]
    assert str(expected_status) in op["responses"], (
        f"Status {expected_status} undocumented for {method.upper()} {openapi_path}"
    )

    if exercised is not None:
        exercised.add((method.upper(), openapi_path))

    if expected_status == 204:
        assert response.content == b""
        return None

    content_type = response.headers.get("content-type", "")
    if expected_content_type and expected_content_type != "application/json":
        assert expected_content_type in content_type, (
            f"{method.upper()} {openapi_path}: expected {expected_content_type}, got {content_type}"
        )
        return response.content

    assert "application/json" in content_type, (
        f"{method.upper()} {openapi_path}: expected application/json, got {content_type}"
    )
    payload = response.json()
    resp_schema = (
        op["responses"][str(expected_status)]
        .get("content", {})
        .get("application/json", {})
        .get("schema", {})
    )
    errors = DRIFT.validate_against_schema(
        payload,
        resp_schema,
        spec,
        allow_extra_properties=False,
        path=f"{method.upper()} {openapi_path} [{expected_status}]",
    )
    assert not errors, "OpenAPI response conformance failed:\n" + "\n".join(errors)
    return payload


def test_cross_stack_contract_drift_validator_is_green():
    errors = DRIFT.run_all_checks(REPO_ROOT)
    assert not errors, "Contract drift detected:\n" + "\n".join(errors)


def test_drift_validator_catches_injected_schema_and_fixture_drift(tmp_path):
    spec = _load_committed_openapi()

    # 1. Missing endpoint path in OpenAPI parity check
    mutated_live = deepcopy(spec)
    mutated_live["paths"].pop("/api/splits")
    parity_errors = DRIFT.validate_live_openapi_parity(mutated_live, spec)
    assert any("OpenAPI paths drifted" in err for err in parity_errors)

    # 2. Missing MAX_SAFE_MINOR bound on *Cents field
    mutated_cents = deepcopy(spec)
    mutated_cents["components"]["schemas"]["AccountOut"]["properties"]["balanceCents"].pop(
        "maximum"
    )
    invariant_errors = DRIFT.validate_openapi_invariants(mutated_cents)
    assert any("AccountOut.balanceCents" in err for err in invariant_errors)

    # 3. Undocumented extra property on strict response validation
    extra_errors = DRIFT.validate_against_schema(
        {
            "id": "m-son",
            "identity": "son",
            "name": "儿子",
            "username": "son",
            "isAdmin": True,
            "color": "bg-teal-600",
            "undocumentedField": "leak",
        },
        {"$ref": "#/components/schemas/MemberOut"},
        spec,
        allow_extra_properties=False,
    )
    assert any("undocumentedField" in err for err in extra_errors)

    # 4. Legacy non-decimal confidence string rejected by OpenAPI pattern
    conf_errors = DRIFT.validate_against_schema(
        {
            "id": "cd-1",
            "date": "2026-08-01T12:00:00Z",
            "payee": "测试",
            "direction": "expense",
            "amountCents": 100,
            "currency": "CNY",
            "suggestedCategoryId": None,
            "originalCategory": None,
            "targetAccountId": "a-cmb",
            "duplicateOfTxnId": None,
            "status": "pending",
            "validationStatus": "valid",
            "confidence": "high",
            "version": 1,
        },
        {"$ref": "#/components/schemas/ImportCandidateOut"},
        spec,
    )
    assert any("confidence" in err for err in conf_errors)

    # 5. Frontend schema.gen.ts property drift caught against openapi.yaml
    (tmp_path / "frontend/src/api").mkdir(parents=True)
    (tmp_path / "frontend/src/data").mkdir(parents=True)
    schema_gen = (REPO_ROOT / "frontend/src/api/schema.gen.ts").read_text(encoding="utf-8")
    (tmp_path / "frontend/src/api/schema.gen.ts").write_text(
        schema_gen.replace("            isAdmin: boolean;", "            isAdminChanged: boolean;"),
        encoding="utf-8",
    )
    for rel in (
        "frontend/src/data/types.ts",
        "frontend/src/api/resources.ts",
        "frontend/src/api/http.ts",
    ):
        (tmp_path / rel).write_text((REPO_ROOT / rel).read_text(encoding="utf-8"), encoding="utf-8")
    drift_errors = DRIFT.validate_frontend_contract_drift(
        tmp_path, spec, _load_endpoints_manifest()
    )
    assert any("MemberOut" in err for err in drift_errors)


def test_canonical_fixtures_conform_to_openapi_and_pydantic_models():
    spec = _load_committed_openapi()
    fixtures = _load_schema_fixtures()

    fixture_errors = DRIFT.validate_fixture_corpus(spec, fixtures)
    assert not fixture_errors, "Fixture corpus conformance failed:\n" + "\n".join(fixture_errors)

    for schema_name, cases in fixtures["valid"].items():
        model_cls = getattr(api_schemas, schema_name, None)
        if model_cls is None:
            continue
        for case in cases:
            parsed = model_cls.model_validate(case["data"])
            dumped = parsed.model_dump(by_alias=True, mode="json")
            revalidated = DRIFT.validate_against_schema(
                dumped,
                {"$ref": f"#/components/schemas/{schema_name}"},
                spec,
                allow_extra_properties=False,
                path=f"roundtrip:{schema_name}[{case['name']}]",
            )
            assert not revalidated, revalidated

    for schema_name, cases in fixtures["invalid"].items():
        model_cls = getattr(api_schemas, schema_name, None)
        if model_cls is None:
            continue
        for case in cases:
            schema_errs = DRIFT.validate_against_schema(
                case["data"],
                {"$ref": f"#/components/schemas/{schema_name}"},
                spec,
                allow_extra_properties=False,
            )
            with pytest.raises(PydanticValidationError):
                model_cls.model_validate(case["data"])
            assert schema_errs, f"Expected OpenAPI schema error for {schema_name}[{case['name']}]"


def test_all_48_live_endpoints_conform_to_openapi_responses(client, monkeypatch):
    from homedatabase_api.services import passkeys as passkey_service

    spec = _load_committed_openapi()
    exercised: set[tuple[str, str]] = set()

    # 1. Health & unauthenticated 401 + 422 HTTPValidationError
    health = _assert_response_conforms(
        spec,
        client.get("/api/health"),
        method="GET",
        openapi_path="/api/health",
        expected_status=200,
        exercised=exercised,
    )
    assert health["status"] == "ok"

    unauth = _assert_response_conforms(
        spec,
        client.get("/api/auth/me"),
        method="GET",
        openapi_path="/api/auth/me",
        expected_status=401,
    )
    assert isinstance(unauth["detail"], str)

    bad_login = _assert_response_conforms(
        spec,
        client.post("/api/auth/login", json={"username": "son", "password": ""}),
        method="POST",
        openapi_path="/api/auth/login",
        expected_status=422,
    )
    assert isinstance(bad_login["detail"], list)

    # 2. Password login -> session cookie -> /auth/me -> /members
    auth_out = _assert_response_conforms(
        spec,
        client.post("/api/auth/login", json={"username": "son", "password": "test-password"}),
        method="POST",
        openapi_path="/api/auth/login",
        expected_status=200,
        exercised=exercised,
    )
    assert auth_out["member"]["id"] == "m-son"

    _assert_response_conforms(
        spec,
        client.get("/api/auth/me"),
        method="GET",
        openapi_path="/api/auth/me",
        expected_status=200,
        exercised=exercised,
    )
    _assert_response_conforms(
        spec,
        client.get("/api/members"),
        method="GET",
        openapi_path="/api/members",
        expected_status=200,
        exercised=exercised,
    )

    # 3. Passkey registration, list, authentication, deletion
    reg_ceremony = _assert_response_conforms(
        spec,
        client.post("/api/auth/passkeys/registration/options"),
        method="POST",
        openapi_path="/api/auth/passkeys/registration/options",
        expected_status=200,
        exercised=exercised,
    )
    monkeypatch.setattr(
        passkey_service,
        "verify_registration_response",
        lambda **_kwargs: SimpleNamespace(
            credential_id=b"openapi-cred-id",
            credential_public_key=b"openapi-pub-key",
            sign_count=0,
            credential_device_type=SimpleNamespace(value="multi_device"),
            credential_backed_up=True,
        ),
    )
    passkey = _assert_response_conforms(
        spec,
        client.post(
            "/api/auth/passkeys/registration/verify",
            json={
                "ceremonyId": reg_ceremony["ceremonyId"],
                "name": "契约测试密钥",
                "credential": {"id": "b3BlbmFwaS1jcmVkLWlk", "response": {}},
            },
        ),
        method="POST",
        openapi_path="/api/auth/passkeys/registration/verify",
        expected_status=201,
        exercised=exercised,
    )
    _assert_response_conforms(
        spec,
        client.get("/api/passkeys"),
        method="GET",
        openapi_path="/api/passkeys",
        expected_status=200,
        exercised=exercised,
    )
    auth_ceremony = _assert_response_conforms(
        spec,
        client.post("/api/auth/passkeys/authentication/options", json={"username": "son"}),
        method="POST",
        openapi_path="/api/auth/passkeys/authentication/options",
        expected_status=200,
        exercised=exercised,
    )
    monkeypatch.setattr(
        passkey_service,
        "verify_authentication_response",
        lambda **_kwargs: SimpleNamespace(
            new_sign_count=1,
            credential_device_type=SimpleNamespace(value="multi_device"),
            credential_backed_up=True,
        ),
    )
    _assert_response_conforms(
        spec,
        client.post(
            "/api/auth/passkeys/authentication/verify",
            json={
                "ceremonyId": auth_ceremony["ceremonyId"],
                "credential": {"id": "b3BlbmFwaS1jcmVkLWlk", "response": {}},
            },
        ),
        method="POST",
        openapi_path="/api/auth/passkeys/authentication/verify",
        expected_status=200,
        exercised=exercised,
    )
    _assert_response_conforms(
        spec,
        client.delete(f"/api/passkeys/{passkey['id']}"),
        method="DELETE",
        openapi_path="/api/passkeys/{passkey_id}",
        expected_status=204,
        exercised=exercised,
    )

    # 4. Accounts + Grants
    _assert_response_conforms(
        spec,
        client.get("/api/accounts"),
        method="GET",
        openapi_path="/api/accounts",
        expected_status=200,
        exercised=exercised,
    )
    usd_acct = _assert_response_conforms(
        spec,
        client.post(
            "/api/accounts",
            json={
                "name": "契约美元卡",
                "kind": "asset",
                "scope": "personal",
                "currency": "USD",
                "openingBalanceCents": 10000,
                "icon": "dollar-sign",
            },
        ),
        method="POST",
        openapi_path="/api/accounts",
        expected_status=201,
        exercised=exercised,
    )
    cny_acct = _assert_response_conforms(
        spec,
        client.post(
            "/api/accounts",
            json={
                "name": "契约备用人民币卡",
                "kind": "asset",
                "scope": "personal",
                "currency": "CNY",
                "openingBalanceCents": 50000,
                "icon": "landmark",
            },
        ),
        method="POST",
        openapi_path="/api/accounts",
        expected_status=201,
    )
    _assert_response_conforms(
        spec,
        client.post(
            "/api/accounts",
            json={
                "name": "超界账户",
                "kind": "asset",
                "scope": "personal",
                "openingBalanceCents": MAX_SAFE_MINOR + 1,
            },
        ),
        method="POST",
        openapi_path="/api/accounts",
        expected_status=422,
    )
    _assert_response_conforms(
        spec,
        client.patch(
            f"/api/accounts/{usd_acct['id']}",
            json={"expectedVersion": usd_acct["version"], "name": "契约美元卡(改名)"},
        ),
        method="PATCH",
        openapi_path="/api/accounts/{account_id}",
        expected_status=200,
        exercised=exercised,
    )
    _assert_response_conforms(
        spec,
        client.put(
            f"/api/accounts/{usd_acct['id']}/grants/m-mom",
            json={"permission": "view"},
        ),
        method="PUT",
        openapi_path="/api/accounts/{account_id}/grants/{member_id}",
        expected_status=200,
        exercised=exercised,
    )
    _assert_response_conforms(
        spec,
        client.get(f"/api/accounts/{usd_acct['id']}/grants"),
        method="GET",
        openapi_path="/api/accounts/{account_id}/grants",
        expected_status=200,
        exercised=exercised,
    )
    _assert_response_conforms(
        spec,
        client.delete(f"/api/accounts/{usd_acct['id']}/grants/m-mom"),
        method="DELETE",
        openapi_path="/api/accounts/{account_id}/grants/{member_id}",
        expected_status=204,
        exercised=exercised,
    )

    # 5. Categories (admin + non-admin 403)
    _assert_response_conforms(
        spec,
        client.get("/api/categories", params={"includeInactive": "true"}),
        method="GET",
        openapi_path="/api/categories",
        expected_status=200,
        exercised=exercised,
    )
    with TestClient(app) as mom_client:
        _assert_response_conforms(
            spec,
            mom_client.get(
                "/api/categories", headers=as_member("m-mom"), params={"includeInactive": "true"}
            ),
            method="GET",
            openapi_path="/api/categories",
            expected_status=403,
        )
    cat = _assert_response_conforms(
        spec,
        client.post(
            "/api/categories",
            json={"name": "契约子分类", "kind": "expense", "parentId": "c-food", "icon": "coffee"},
        ),
        method="POST",
        openapi_path="/api/categories",
        expected_status=201,
        exercised=exercised,
    )
    _assert_response_conforms(
        spec,
        client.patch(
            f"/api/categories/{cat['id']}",
            json={"expectedVersion": cat["version"], "active": False},
        ),
        method="PATCH",
        openapi_path="/api/categories/{category_id}",
        expected_status=200,
        exercised=exercised,
    )

    # 6. Transactions (CNY, USD FX, transfer, refund, patch, recycle-bin, restore)
    _assert_response_conforms(
        spec,
        client.post(
            "/api/transactions",
            json={
                "type": "expense",
                "payee": "非法汇率",
                "accountId": usd_acct["id"],
                "amountCents": 100,
                "currency": "USD",
                "fxRate": "bad-rate",
                "date": "2026-08-05T12:00:00+08:00",
            },
        ),
        method="POST",
        openapi_path="/api/transactions",
        expected_status=422,
    )
    cny_txn = _assert_response_conforms(
        spec,
        client.post(
            "/api/transactions",
            json={
                "type": "expense",
                "payee": "契约家庭支出",
                "note": "含分摊准备",
                "categoryId": "c-food",
                "accountId": "a-family",
                "amountCents": 10000,
                "currency": "CNY",
                "date": "2026-08-05T12:00:00+08:00",
            },
        ),
        method="POST",
        openapi_path="/api/transactions",
        expected_status=201,
        exercised=exercised,
    )
    usd_txn = _assert_response_conforms(
        spec,
        client.post(
            "/api/transactions",
            json={
                "type": "expense",
                "payee": "Apple Store",
                "categoryId": "c-other",
                "accountId": usd_acct["id"],
                "amountCents": 2900,
                "currency": "USD",
                "fxRate": "7.16",
                "date": "2026-08-05T13:00:00+08:00",
            },
        ),
        method="POST",
        openapi_path="/api/transactions",
        expected_status=201,
    )
    assert Decimal(usd_txn["fxRate"]) == Decimal("7.16")
    assert usd_txn["baseAmountCents"] == 20764
    _assert_response_conforms(
        spec,
        client.post(
            "/api/transactions",
            json={
                "type": "transfer",
                "payee": "账户调拨",
                "accountId": cny_acct["id"],
                "toAccountId": "a-family",
                "amountCents": 5000,
                "currency": "CNY",
                "date": "2026-08-05T14:00:00+08:00",
            },
        ),
        method="POST",
        openapi_path="/api/transactions",
        expected_status=201,
    )
    refund_txn = _assert_response_conforms(
        spec,
        client.post(
            "/api/transactions",
            json={
                "type": "refund",
                "payee": "Apple Store Refund",
                "accountId": usd_acct["id"],
                "refundOf": usd_txn["id"],
                "amountCents": 1000,
                "currency": "USD",
                "fxRate": "7.16",
                "date": "2026-08-06T09:00:00+08:00",
            },
        ),
        method="POST",
        openapi_path="/api/transactions",
        expected_status=201,
    )
    _assert_response_conforms(
        spec,
        client.get(
            "/api/transactions",
            params={"type": "expense", "month": "2026-08", "keyword": "Apple"},
        ),
        method="GET",
        openapi_path="/api/transactions",
        expected_status=200,
        exercised=exercised,
    )
    _assert_response_conforms(
        spec,
        client.get(f"/api/transactions/{cny_txn['id']}"),
        method="GET",
        openapi_path="/api/transactions/{transaction_id}",
        expected_status=200,
        exercised=exercised,
    )
    patched_refund = _assert_response_conforms(
        spec,
        client.patch(
            f"/api/transactions/{refund_txn['id']}",
            json={"expectedVersion": refund_txn["version"], "note": "部分退款完成"},
        ),
        method="PATCH",
        openapi_path="/api/transactions/{transaction_id}",
        expected_status=200,
        exercised=exercised,
    )
    _assert_response_conforms(
        spec,
        client.request(
            "DELETE",
            f"/api/transactions/{patched_refund['id']}",
            json={"reason": "测试回收站与恢复"},
        ),
        method="DELETE",
        openapi_path="/api/transactions/{transaction_id}",
        expected_status=204,
        exercised=exercised,
    )
    recycled = _assert_response_conforms(
        spec,
        client.get("/api/transactions/recycle-bin"),
        method="GET",
        openapi_path="/api/transactions/recycle-bin",
        expected_status=200,
        exercised=exercised,
    )
    recycled_version = next(
        item["version"] for item in recycled if item["id"] == patched_refund["id"]
    )
    _assert_response_conforms(
        spec,
        client.post(
            f"/api/transactions/{patched_refund['id']}/restore",
            json={"expectedVersion": recycled_version},
        ),
        method="POST",
        openapi_path="/api/transactions/{transaction_id}/restore",
        expected_status=200,
        exercised=exercised,
    )

    # 7. Imports (upload, status, list, detail, patch FX, skip-duplicates, confirm, undo)
    csv_payload = (
        "交易时间,交易对方,收支类型,金额,币种,交易分类\n"
        "2026-08-07 10:00:00,海外商店,支出,15.00,USD,其他\n"
        "2026-08-07 11:00:00,本地早餐,支出,12.50,CNY,餐饮\n"
        "2026-08-07 12:00:00,重复测试,支出,9.90,CNY,餐饮\n"
    ).encode("utf-8-sig")
    batch = _assert_response_conforms(
        spec,
        client.post(
            "/api/imports",
            data={"source": "契约CSV", "targetAccountId": "a-family"},
            files=[("files", ("contract-bill.csv", csv_payload, "text/csv"))],
        ),
        method="POST",
        openapi_path="/api/imports",
        expected_status=201,
        exercised=exercised,
    )
    _assert_response_conforms(
        spec,
        client.get(f"/api/imports/{batch['id']}/status"),
        method="GET",
        openapi_path="/api/imports/{batch_id}/status",
        expected_status=200,
        exercised=exercised,
    )
    _assert_response_conforms(
        spec,
        client.get("/api/import-batches"),
        method="GET",
        openapi_path="/api/import-batches",
        expected_status=200,
        exercised=exercised,
    )
    _assert_response_conforms(
        spec,
        client.get(f"/api/import-batches/{batch['id']}"),
        method="GET",
        openapi_path="/api/import-batches/{batch_id}",
        expected_status=200,
        exercised=exercised,
    )
    usd_cand = next(item for item in batch["candidates"] if item["currency"] == "USD")
    cny_cand = next(item for item in batch["candidates"] if item["payee"] == "本地早餐")
    skip_cand = next(item for item in batch["candidates"] if item["payee"] == "重复测试")
    assert usd_cand["validationStatus"] == "needs_fx_review"
    assert usd_cand["confidence"] is not None

    patched_cand = _assert_response_conforms(
        spec,
        client.patch(
            f"/api/import-batches/{batch['id']}/candidates/{usd_cand['id']}",
            json={
                "expectedVersion": usd_cand["version"],
                "fxRate": "7.20",
                "baseAmountCents": 10800,
                "targetAccountId": usd_acct["id"],
            },
        ),
        method="PATCH",
        openapi_path="/api/import-batches/{batch_id}/candidates/{candidate_id}",
        expected_status=200,
        exercised=exercised,
    )
    assert patched_cand["validationStatus"] == "valid"

    _assert_response_conforms(
        spec,
        client.post(
            f"/api/import-batches/{batch['id']}/skip-duplicates",
            json={"candidateIds": [skip_cand["id"]]},
        ),
        method="POST",
        openapi_path="/api/import-batches/{batch_id}/skip-duplicates",
        expected_status=200,
        exercised=exercised,
    )
    _assert_response_conforms(
        spec,
        client.post(
            f"/api/import-batches/{batch['id']}/confirm",
            json={"candidateIds": [patched_cand["id"], cny_cand["id"]]},
        ),
        method="POST",
        openapi_path="/api/import-batches/{batch_id}/confirm",
        expected_status=200,
        exercised=exercised,
    )
    _assert_response_conforms(
        spec,
        client.post(f"/api/import-batches/{batch['id']}/undo"),
        method="POST",
        openapi_path="/api/import-batches/{batch_id}/undo",
        expected_status=200,
        exercised=exercised,
    )

    # 8. Splits (create, list, update-share, respond)
    split = _assert_response_conforms(
        spec,
        client.post(
            "/api/splits",
            json={
                "txnId": cny_txn["id"],
                "totalCents": 10000,
                "shares": [
                    {"memberId": "m-son", "shareCents": 6000},
                    {"memberId": "m-mom", "shareCents": 4000},
                ],
            },
        ),
        method="POST",
        openapi_path="/api/splits",
        expected_status=201,
        exercised=exercised,
    )
    _assert_response_conforms(
        spec,
        client.get("/api/splits"),
        method="GET",
        openapi_path="/api/splits",
        expected_status=200,
        exercised=exercised,
    )
    updated_split = _assert_response_conforms(
        spec,
        client.patch(
            f"/api/splits/{split['id']}/shares/m-mom",
            json={"expectedVersion": split["version"], "shareCents": 5000},
        ),
        method="PATCH",
        openapi_path="/api/splits/{split_id}/shares/{member_id}",
        expected_status=200,
        exercised=exercised,
    )
    with TestClient(app) as mom_client:
        _assert_response_conforms(
            spec,
            mom_client.post(
                f"/api/splits/{updated_split['id']}/respond",
                headers=as_member("m-mom"),
                json={"expectedVersion": updated_split["version"], "accept": True},
            ),
            method="POST",
            openapi_path="/api/splits/{split_id}/respond",
            expected_status=200,
            exercised=exercised,
        )

    # 9. Backups (create, list, verify, download binary)
    backup = _assert_response_conforms(
        spec,
        client.post("/api/backups"),
        method="POST",
        openapi_path="/api/backups",
        expected_status=201,
        exercised=exercised,
    )
    _assert_response_conforms(
        spec,
        client.get("/api/backups"),
        method="GET",
        openapi_path="/api/backups",
        expected_status=200,
        exercised=exercised,
    )
    _assert_response_conforms(
        spec,
        client.post(f"/api/backups/{backup['id']}/verify"),
        method="POST",
        openapi_path="/api/backups/{backup_id}/verify",
        expected_status=200,
        exercised=exercised,
    )
    backup_bytes = _assert_response_conforms(
        spec,
        client.get(f"/api/backups/{backup['id']}/download"),
        method="GET",
        openapi_path="/api/backups/{backup_id}/download",
        expected_status=200,
        expected_content_type="application/octet-stream",
        exercised=exercised,
    )
    assert isinstance(backup_bytes, bytes) and backup_bytes.startswith(b"HDBACKUP1")

    # 10. Period locks (create, list, delete)
    lock = _assert_response_conforms(
        spec,
        client.post(
            "/api/period-locks",
            json={"startsOn": "2026-07-01", "endsOn": "2026-07-31", "reason": "七月核对"},
        ),
        method="POST",
        openapi_path="/api/period-locks",
        expected_status=201,
        exercised=exercised,
    )
    _assert_response_conforms(
        spec,
        client.get("/api/period-locks"),
        method="GET",
        openapi_path="/api/period-locks",
        expected_status=200,
        exercised=exercised,
    )
    _assert_response_conforms(
        spec,
        client.request(
            "DELETE",
            f"/api/period-locks/{lock['id']}",
            json={"reason": "测试完成解锁"},
        ),
        method="DELETE",
        openapi_path="/api/period-locks/{period_lock_id}",
        expected_status=204,
        exercised=exercised,
    )

    # 11. Report tiers + XLSX export
    _assert_response_conforms(
        spec,
        client.get("/api/report-tiers"),
        method="GET",
        openapi_path="/api/report-tiers",
        expected_status=200,
        exercised=exercised,
    )
    xlsx_bytes = _assert_response_conforms(
        spec,
        client.get(
            "/api/reports/export",
            params={"tier": 5, "from": "2026-08-01", "to": "2026-08-31"},
        ),
        method="GET",
        openapi_path="/api/reports/export",
        expected_status=200,
        expected_content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        exercised=exercised,
    )
    assert isinstance(xlsx_bytes, bytes) and xlsx_bytes.startswith(b"PK")

    # 12. AI CFO endpoints (capabilities, ask, cashflow-radar)
    _assert_response_conforms(
        spec,
        client.get("/api/cfo/capabilities"),
        method="GET",
        openapi_path="/api/cfo/capabilities",
        expected_status=200,
        exercised=exercised,
    )
    _assert_response_conforms(
        spec,
        client.post(
            "/api/cfo/ask",
            json={"question": "2026年8月花了多少", "asOfDate": "2026-08-31"},
        ),
        method="POST",
        openapi_path="/api/cfo/ask",
        expected_status=200,
        exercised=exercised,
    )
    _assert_response_conforms(
        spec,
        client.post(
            "/api/cfo/cashflow-radar",
            json={"asOfDate": "2026-08-31", "horizonDays": 90},
        ),
        method="POST",
        openapi_path="/api/cfo/cashflow-radar",
        expected_status=200,
        exercised=exercised,
    )

    # 13. Logout
    _assert_response_conforms(
        spec,
        client.post("/api/auth/logout"),
        method="POST",
        openapi_path="/api/auth/logout",
        expected_status=204,
        exercised=exercised,
    )

    # Verify 100% of OpenAPI endpoints were exercised & validated!
    all_ops = {
        (method.upper(), path)
        for path, methods in spec["paths"].items()
        for method in methods
        if method in DRIFT.HTTP_METHODS
    }
    assert exercised == all_ops, f"Unexercised OpenAPI operations: {sorted(all_ops - exercised)}"
