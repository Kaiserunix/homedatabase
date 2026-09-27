from __future__ import annotations

from types import SimpleNamespace

import pytest
from conftest import as_member
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from homedatabase_api.database import get_session_factory
from homedatabase_api.models import Category
from homedatabase_api.schemas import AccountUpdateInput, CategoryUpdateInput
from homedatabase_api.services import catalog as catalog_service


def test_personal_account_grants_preserve_owner_control(client):
    created = client.post(
        "/api/accounts",
        headers=as_member("m-dad"),
        json={
            "name": "爸爸微信",
            "kind": "asset",
            "scope": "personal",
            "currency": "CNY",
            "openingBalanceCents": 1200,
        },
    )
    assert created.status_code == 201
    account = created.json()
    assert account["ownerId"] == "m-dad"
    assert account["permission"] == "manage"
    assert account["version"] == 1

    mom_before = client.get("/api/accounts", headers=as_member("m-mom")).json()
    assert account["id"] not in {item["id"] for item in mom_before}

    view_grant = client.put(
        f"/api/accounts/{account['id']}/grants/m-mom",
        headers=as_member("m-dad"),
        json={"permission": "view"},
    )
    assert view_grant.status_code == 200
    mom_account = next(
        item
        for item in client.get("/api/accounts", headers=as_member("m-mom")).json()
        if item["id"] == account["id"]
    )
    assert mom_account["permission"] == "view"
    denied_entry = client.post(
        "/api/transactions",
        headers=as_member("m-mom"),
        json={
            "type": "expense",
            "payee": "测试付款",
            "accountId": account["id"],
            "amountCents": 100,
            "currency": "CNY",
            "date": "2026-08-05T12:00:00+08:00",
        },
    )
    assert denied_entry.status_code == 403

    manage_grant = client.put(
        f"/api/accounts/{account['id']}/grants/m-mom",
        headers=as_member("m-dad"),
        json={"permission": "manage"},
    )
    assert manage_grant.status_code == 200
    assert denied_entry.request.url.path == "/api/transactions"
    accepted_entry = client.post(
        "/api/transactions",
        headers=as_member("m-mom"),
        json={
            "type": "expense",
            "payee": "授权后付款",
            "accountId": account["id"],
            "amountCents": 100,
            "currency": "CNY",
            "date": "2026-08-05T12:00:00+08:00",
        },
    )
    assert accepted_entry.status_code == 201

    deleted = client.delete(
        f"/api/accounts/{account['id']}/grants/m-mom",
        headers=as_member("m-dad"),
    )
    assert deleted.status_code == 204
    assert account["id"] not in {
        item["id"] for item in client.get("/api/accounts", headers=as_member("m-mom")).json()
    }


def test_family_account_defaults_to_view_and_admin_controls_manage_grants(client):
    assert (
        client.post(
            "/api/accounts",
            headers=as_member("m-dad"),
            json={
                "name": "非管理员公共账户",
                "kind": "asset",
                "scope": "family",
            },
        ).status_code
        == 403
    )
    account = client.post(
        "/api/accounts",
        headers=as_member("m-son"),
        json={"name": "家庭备用金", "kind": "asset", "scope": "family"},
    ).json()
    dad = next(
        item
        for item in client.get("/api/accounts", headers=as_member("m-dad")).json()
        if item["id"] == account["id"]
    )
    assert dad["permission"] == "view"
    assert (
        client.patch(
            f"/api/accounts/{account['id']}",
            headers=as_member("m-dad"),
            json={"expectedVersion": 1, "name": "越权改名"},
        ).status_code
        == 403
    )
    client.put(
        f"/api/accounts/{account['id']}/grants/m-dad",
        headers=as_member("m-son"),
        json={"permission": "manage"},
    )
    updated = client.patch(
        f"/api/accounts/{account['id']}",
        headers=as_member("m-dad"),
        json={"expectedVersion": 1, "name": "家庭应急金"},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "家庭应急金"
    assert updated.json()["version"] == 2


def test_admin_can_create_two_level_categories_with_optimistic_updates(client):
    denied = client.post(
        "/api/categories",
        headers=as_member("m-dad"),
        json={"name": "不应创建", "kind": "expense"},
    )
    assert denied.status_code == 403

    root = client.post(
        "/api/categories",
        headers=as_member("m-son"),
        json={"name": "家庭服务", "kind": "expense"},
    )
    assert root.status_code == 201
    child = client.post(
        "/api/categories",
        headers=as_member("m-son"),
        json={
            "name": "保洁",
            "kind": "expense",
            "parentId": root.json()["id"],
        },
    )
    assert child.status_code == 201
    assert (
        client.post(
            "/api/categories",
            headers=as_member("m-son"),
            json={
                "name": "第三层",
                "kind": "expense",
                "parentId": child.json()["id"],
            },
        ).status_code
        == 422
    )
    updated = client.patch(
        f"/api/categories/{child.json()['id']}",
        headers=as_member("m-son"),
        json={"expectedVersion": 1, "name": "家政保洁"},
    )
    assert updated.status_code == 200
    assert updated.json()["version"] == 2
    stale = client.patch(
        f"/api/categories/{child.json()['id']}",
        headers=as_member("m-son"),
        json={"expectedVersion": 1, "active": False},
    )
    assert stale.status_code == 409

    deactivated = client.patch(
        f"/api/categories/{child.json()['id']}",
        headers=as_member("m-son"),
        json={"expectedVersion": 2, "active": False},
    )
    assert deactivated.status_code == 200
    assert deactivated.json()["active"] is False

    active_only = client.get("/api/categories", headers=as_member("m-son"))
    assert active_only.status_code == 200
    assert child.json()["id"] not in {item["id"] for item in active_only.json()}

    all_categories = client.get(
        "/api/categories?includeInactive=true",
        headers=as_member("m-son"),
    )
    assert all_categories.status_code == 200
    inactive_child = next(
        item for item in all_categories.json() if item["id"] == child.json()["id"]
    )
    assert inactive_child["active"] is False
    assert inactive_child["version"] == 3

    denied_inactive = client.get(
        "/api/categories?includeInactive=true",
        headers=as_member("m-dad"),
    )
    assert denied_inactive.status_code == 403


def test_optimistic_catalog_updates_lock_the_versioned_row():
    captured = []

    class CaptureSession:
        def scalar(self, statement):
            captured.append(statement)
            return None

    session = CaptureSession()
    member = SimpleNamespace(id="m-son", is_admin=True)
    with pytest.raises(HTTPException) as missing_account:
        catalog_service.update_account(
            session,
            member,
            "account-race",
            AccountUpdateInput.model_validate({"expectedVersion": 1, "name": "新版名称"}),
        )
    assert missing_account.value.status_code == 404

    with pytest.raises(HTTPException) as missing_category:
        catalog_service.update_category(
            session,
            member,
            "category-race",
            CategoryUpdateInput.model_validate({"expectedVersion": 1, "name": "新版分类"}),
        )
    assert missing_category.value.status_code == 404

    sql = [str(statement.compile(dialect=postgresql.dialect())) for statement in captured]
    assert len(sql) == 2
    assert all("FOR UPDATE" in statement for statement in sql)


def test_database_rejects_duplicate_root_category_names():
    with get_session_factory()() as db:
        db.add_all(
            [
                Category(name="并发根分类", kind="expense", parent_id=None),
                Category(name="并发根分类", kind="expense", parent_id=None),
            ]
        )
        with pytest.raises(IntegrityError):
            db.commit()
