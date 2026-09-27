from __future__ import annotations

from conftest import as_member


def test_transaction_detail_recycle_bin_and_optimistic_restore(client):
    created = client.post(
        "/api/transactions",
        headers=as_member("m-son"),
        json={
            "type": "expense",
            "payee": "可恢复付款",
            "accountId": "a-son-wallet",
            "amountCents": 100,
            "currency": "CNY",
            "date": "2026-08-05T12:00:00+08:00",
        },
    ).json()
    detail = client.get(f"/api/transactions/{created['id']}", headers=as_member("m-son"))
    assert detail.status_code == 200
    assert detail.json()["deletedAt"] is None

    deleted = client.request(
        "DELETE",
        f"/api/transactions/{created['id']}",
        headers=as_member("m-son"),
        json={"reason": "录错了"},
    )
    assert deleted.status_code == 204
    assert (
        client.get(f"/api/transactions/{created['id']}", headers=as_member("m-son")).status_code
        == 404
    )
    recycled = client.get("/api/transactions/recycle-bin", headers=as_member("m-son")).json()
    assert len(recycled) == 1
    assert recycled[0]["deletedAt"] is not None
    assert recycled[0]["version"] == 2

    stale = client.post(
        f"/api/transactions/{created['id']}/restore",
        headers=as_member("m-son"),
        json={"expectedVersion": 1},
    )
    assert stale.status_code == 409
    restored = client.post(
        f"/api/transactions/{created['id']}/restore",
        headers=as_member("m-son"),
        json={"expectedVersion": 2},
    )
    assert restored.status_code == 200
    assert restored.json()["deletedAt"] is None
    assert restored.json()["version"] == 3
