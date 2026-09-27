from __future__ import annotations

import importlib.util
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def test_demo_has_no_credentials_and_only_fictional_balanced_transactions():
    spec = importlib.util.spec_from_file_location(
        "check_public_demo", BACKEND / "scripts/check_public_demo.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.check()


def test_local_demo_setup_ignores_inherited_database_and_refuses_overwrite(tmp_path):
    from homedatabase_api.security import verify_password

    sentinel = tmp_path / "do-not-touch.sqlite3"
    sentinel.write_bytes(b"not a database; must never be opened")
    directory = tmp_path / "local copy"
    environment = os.environ.copy()
    environment["HOMEDATABASE_DATABASE_URL"] = f"sqlite+pysqlite:///{sentinel.as_posix()}"
    command = [
        sys.executable,
        str(BACKEND / "scripts/setup_local.py"),
        "--mode",
        "demo",
        "--directory",
        str(directory),
    ]
    result = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    credentials = dict(
        line.strip().split(": ", 1) for line in result.stdout.splitlines() if line.startswith("  ")
    )
    assert len(set(credentials.values())) == 3
    with closing(sqlite3.connect(directory / "ledger.sqlite3")) as db:
        assert db.execute("SELECT COUNT(*) FROM transactions").fetchone() == (6,)
        for username, hashed in db.execute("SELECT username, password_hash FROM members"):
            assert verify_password(credentials[username], hashed)
    config = (directory / ".env").read_text(encoding="utf-8")
    assert "HOMEDATABASE_DEV_MEMBER_ID=\n" in config
    assert str(sentinel) not in config
    original = (directory / "ledger.sqlite3").read_bytes()
    repeated = subprocess.run(command, env=environment, capture_output=True, timeout=30)
    assert repeated.returncode != 0
    assert (directory / "ledger.sqlite3").read_bytes() == original
    assert sentinel.read_bytes() == b"not a database; must never be opened"


def test_public_csv_can_be_uploaded_reviewed_and_confirmed(client):
    assert client.get("/api/auth/me").status_code == 401
    login = client.post("/api/auth/login", json={"username": "son", "password": "test-password"})
    assert login.status_code == 200
    uploaded = client.post(
        "/api/imports",
        data={"targetAccountId": "a-family", "source": "CSV template"},
        files=[
            (
                "files",
                (
                    "transactions.csv",
                    (BACKEND.parent / "examples/transactions.csv").read_bytes(),
                    "text/csv",
                ),
            )
        ],
    )
    assert uploaded.status_code == 201, uploaded.text
    batch = uploaded.json()
    assert len(batch["candidates"]) == 4
    assert client.get("/api/transactions").json() == []
    confirmed = client.post(
        f"/api/import-batches/{batch['id']}/confirm",
        json={"candidateIds": [row["id"] for row in batch["candidates"]]},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "posted"
    assert sorted(row["amountCents"] for row in client.get("/api/transactions").json()) == [
        400,
        2850,
        15680,
        600000,
    ]
