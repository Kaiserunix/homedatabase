from __future__ import annotations

from types import SimpleNamespace

from conftest import as_member


def test_passkey_registration_and_authentication_ceremonies(client, monkeypatch):
    from homedatabase_api.services import passkeys as service

    registration_options = client.post(
        "/api/auth/passkeys/registration/options",
        headers=as_member("m-son"),
    )
    assert registration_options.status_code == 200
    registration = registration_options.json()
    assert registration["options"]["rp"]["id"] == "localhost"
    assert registration["options"]["user"]["name"] == "son"

    monkeypatch.setattr(
        service,
        "verify_registration_response",
        lambda **_kwargs: SimpleNamespace(
            credential_id=b"credential-id",
            credential_public_key=b"public-key",
            sign_count=0,
            credential_device_type=SimpleNamespace(value="multi_device"),
            credential_backed_up=True,
        ),
    )
    verified = client.post(
        "/api/auth/passkeys/registration/verify",
        headers=as_member("m-son"),
        json={
            "ceremonyId": registration["ceremonyId"],
            "name": "这台手机",
            "credential": {
                "id": "Y3JlZGVudGlhbC1pZA",
                "response": {"transports": ["internal", "hybrid"]},
            },
        },
    )
    assert verified.status_code == 201
    assert verified.json()["name"] == "这台手机"

    authentication_options = client.post(
        "/api/auth/passkeys/authentication/options", json={"username": "son"}
    )
    assert authentication_options.status_code == 200
    authentication = authentication_options.json()
    assert authentication["options"]["rpId"] == "localhost"

    monkeypatch.setattr(
        service,
        "verify_authentication_response",
        lambda **_kwargs: SimpleNamespace(
            new_sign_count=1,
            credential_device_type=SimpleNamespace(value="multi_device"),
            credential_backed_up=True,
        ),
    )
    logged_in = client.post(
        "/api/auth/passkeys/authentication/verify",
        json={
            "ceremonyId": authentication["ceremonyId"],
            "credential": {"id": "Y3JlZGVudGlhbC1pZA", "response": {}},
        },
    )
    assert logged_in.status_code == 200
    assert logged_in.json()["member"]["id"] == "m-son"
    assert logged_in.cookies.get("hd_session")

    replay = client.post(
        "/api/auth/passkeys/authentication/verify",
        json={
            "ceremonyId": authentication["ceremonyId"],
            "credential": {"id": "Y3JlZGVudGlhbC1pZA", "response": {}},
        },
    )
    assert replay.status_code == 400


def test_passkey_cannot_be_deleted_by_another_member(client, monkeypatch):
    from homedatabase_api.services import passkeys as service

    options = client.post(
        "/api/auth/passkeys/registration/options", headers=as_member("m-son")
    ).json()
    monkeypatch.setattr(
        service,
        "verify_registration_response",
        lambda **_kwargs: SimpleNamespace(
            credential_id=b"only-son",
            credential_public_key=b"public-key",
            sign_count=0,
            credential_device_type=SimpleNamespace(value="single_device"),
            credential_backed_up=False,
        ),
    )
    passkey = client.post(
        "/api/auth/passkeys/registration/verify",
        headers=as_member("m-son"),
        json={
            "ceremonyId": options["ceremonyId"],
            "name": "儿子的电脑",
            "credential": {"id": "b25seS1zb24", "response": {}},
        },
    ).json()
    denied = client.delete(f"/api/passkeys/{passkey['id']}", headers=as_member("m-dad"))
    assert denied.status_code == 404
    deleted = client.delete(f"/api/passkeys/{passkey['id']}", headers=as_member("m-son"))
    assert deleted.status_code == 204
