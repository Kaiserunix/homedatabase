from __future__ import annotations

from datetime import datetime

from conftest import as_member

from homedatabase_api.database import get_session_factory
from homedatabase_api.services.ledger import period_is_locked


def test_admin_period_lock_blocks_posting_until_audited_unlock(client):
    denied = client.post(
        "/api/period-locks",
        headers=as_member("m-dad"),
        json={"startsOn": "2026-08-01", "endsOn": "2026-08-31", "reason": "月结"},
    )
    assert denied.status_code == 403
    created = client.post(
        "/api/period-locks",
        headers=as_member("m-son"),
        json={"startsOn": "2026-08-01", "endsOn": "2026-08-31", "reason": "月结"},
    )
    assert created.status_code == 201
    period = created.json()
    assert period["lockedBy"] == "m-son"

    entry = {
        "type": "expense",
        "payee": "锁定期测试",
        "accountId": "a-son-wallet",
        "amountCents": 100,
        "currency": "CNY",
        "date": "2026-08-05T12:00:00+08:00",
    }
    assert (
        client.post("/api/transactions", headers=as_member("m-son"), json=entry).status_code == 409
    )
    overlapping = client.post(
        "/api/period-locks",
        headers=as_member("m-son"),
        json={"startsOn": "2026-08-15", "endsOn": "2026-09-15", "reason": "重复"},
    )
    assert overlapping.status_code == 409

    unlocked = client.request(
        "DELETE",
        f"/api/period-locks/{period['id']}",
        headers=as_member("m-son"),
        json={"reason": "发现遗漏，重新开放"},
    )
    assert unlocked.status_code == 204
    assert (
        client.post("/api/transactions", headers=as_member("m-son"), json=entry).status_code == 201
    )


def test_period_lock_treats_a_naive_database_value_as_utc(client):
    created = client.post(
        "/api/period-locks",
        headers=as_member("m-son"),
        json={"startsOn": "2026-08-01", "endsOn": "2026-08-01", "reason": "日界线"},
    )
    assert created.status_code == 201

    # SQLite stores UTC without an offset.  16:30 UTC is 00:30 on August 1
    # in Shanghai and must not depend on the host machine's local timezone.
    with get_session_factory()() as db:
        assert period_is_locked(db, datetime(2026, 7, 31, 16, 30))


def test_nonzero_opening_balance_cannot_rewrite_history_after_a_period_is_locked(client):
    locked = client.post(
        "/api/period-locks",
        headers=as_member("m-son"),
        json={"startsOn": "2026-08-01", "endsOn": "2026-08-31", "reason": "月结"},
    )
    assert locked.status_code == 201, locked.text

    rejected = client.post(
        "/api/accounts",
        headers=as_member("m-son"),
        json={
            "name": "追溯期初账户",
            "kind": "asset",
            "scope": "personal",
            "currency": "CNY",
            "openingBalanceCents": 100,
        },
    )
    assert rejected.status_code == 409, rejected.text

    zero_opening = client.post(
        "/api/accounts",
        headers=as_member("m-son"),
        json={
            "name": "零期初账户",
            "kind": "asset",
            "scope": "personal",
            "currency": "CNY",
            "openingBalanceCents": 0,
        },
    )
    assert zero_opening.status_code == 201, zero_opening.text
