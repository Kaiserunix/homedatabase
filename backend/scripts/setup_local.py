"""Create an isolated local ledger; never modify an existing directory or database."""

from __future__ import annotations

import argparse
import base64
import getpass
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "src"))


def setup(mode: str, output: Path) -> None:
    output = output.resolve()
    if output.exists():
        raise SystemExit("Directory already exists; choose a NEW --directory. Nothing was changed.")
    passwords: dict[str, str] = {}
    for identity in ("dad", "mom", "son"):
        if mode == "demo":
            passwords[identity] = secrets.token_urlsafe(18)
        else:
            password = getpass.getpass(f"Set {identity} password (12+ characters): ")
            confirm = getpass.getpass("Confirm password: ")
            if password != confirm or len(password) < 12 or password in passwords.values():
                raise SystemExit(
                    "Passwords must match, have 12+ characters, and differ per member."
                )
            passwords[identity] = password
    output.mkdir(parents=True, exist_ok=False)
    database = output / "ledger.sqlite3"
    if mode == "demo":
        shutil.copyfile(BACKEND.parent / "examples/demo.sqlite3", database)
    replacements = {
        "HOMEDATABASE_ENVIRONMENT": "local",
        "HOMEDATABASE_DATABASE_URL": f"sqlite+pysqlite:///{database.as_posix()}",
        "HOMEDATABASE_DEV_MEMBER_ID": "",
        "HOMEDATABASE_AUTO_CREATE_SCHEMA": "false",
        "HOMEDATABASE_SEED_DEMO": "false",
        "HOMEDATABASE_BOOTSTRAP_PASSWORD": "",
        "HOMEDATABASE_DATABASE_ECHO": "false",
        "HOMEDATABASE_TASK_ALWAYS_EAGER": "true",
        "HOMEDATABASE_OCR_ENABLED": "false",
        "HOMEDATABASE_SOURCE_ARCHIVE_DIR": (output / "source-files").as_posix(),
        "HOMEDATABASE_BACKUP_DIR": (output / "backups").as_posix(),
        "HOMEDATABASE_OCR_JOBS_DIR": (output / "ocr-jobs").as_posix(),
        "HOMEDATABASE_SOURCE_ENCRYPTION_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32))
        .decode()
        .rstrip("="),
        "HOMEDATABASE_BACKUP_ENCRYPTION_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32))
        .decode()
        .rstrip("="),
    }
    lines = []
    settings = {}
    for line in (BACKEND / ".env.example").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            value = replacements.get(key, value)
            settings[key] = value
            line = f"{key}={value}"
        lines.append(line)
    config = output / ".env"
    config.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if os.name != "nt":
        output.chmod(0o700)
        config.chmod(0o600)
    os.environ.update(settings)
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(BACKEND / "alembic.ini"), "upgrade", "head"],
        cwd=BACKEND,
        check=True,
    )
    from homedatabase_api.database import get_session_factory
    from homedatabase_api.models import Member
    from homedatabase_api.security import hash_password
    from homedatabase_api.seed import seed_family_data

    with get_session_factory()() as db:
        if mode == "personal":
            if not seed_family_data(db, passwords=passwords):
                raise SystemExit("Expected an empty personal database")
        else:
            for identity, password in passwords.items():
                db.get(Member, f"m-{identity}").password_hash = hash_password(password)
            db.commit()
    print(f"Created {mode} ledger. Config: {config}")
    if mode == "demo":
        print("New passwords for THIS LOCAL COPY only; keep them private:")
        for identity, password in passwords.items():
            print(f"  {identity}: {password}")
    print(
        "Start from backend/: uv run --env-file <config-path> uvicorn "
        "homedatabase_api.main:app --app-dir src --host 127.0.0.1 --port 8000"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("personal", "demo"), required=True)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    setup(args.mode, args.directory)
