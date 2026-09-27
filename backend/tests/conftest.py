from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

TEST_BACKUP_DIR = Path(tempfile.gettempdir()) / f"homedatabase-backups-{os.getpid()}"
TEST_SOURCE_DIR = Path(tempfile.gettempdir()) / f"homedatabase-sources-{os.getpid()}"

os.environ["HOMEDATABASE_ENVIRONMENT"] = "test"
os.environ["HOMEDATABASE_DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
os.environ["HOMEDATABASE_DEV_MEMBER_ID"] = ""
os.environ["HOMEDATABASE_AUTO_CREATE_SCHEMA"] = "true"
os.environ["HOMEDATABASE_SEED_DEMO"] = "false"
os.environ["HOMEDATABASE_SESSION_COOKIE_SECURE"] = "false"
os.environ["HOMEDATABASE_BACKUP_ENCRYPTION_KEY"] = "QkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQkJCQkI"
os.environ["HOMEDATABASE_BACKUP_DIR"] = str(TEST_BACKUP_DIR)
os.environ["HOMEDATABASE_BACKUP_LOCATION_KIND"] = "local"
os.environ["HOMEDATABASE_SOURCE_ARCHIVE_DIR"] = str(TEST_SOURCE_DIR)
os.environ["HOMEDATABASE_SOURCE_ENCRYPTION_KEY"] = "QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUE"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from homedatabase_api.database import Base, get_engine, get_session_factory  # noqa: E402
from homedatabase_api.main import app  # noqa: E402
from homedatabase_api.seed import seed_demo_data  # noqa: E402


def _recreate_schema(engine) -> None:
    # The schema has intentional self/cross references (refunds, imports). SQLite
    # cannot topologically DROP those tables while FK checks are enabled.
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        Base.metadata.drop_all(connection)
        Base.metadata.create_all(connection)
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")


@pytest.fixture(autouse=True)
def reset_database():
    shutil.rmtree(TEST_BACKUP_DIR, ignore_errors=True)
    shutil.rmtree(TEST_SOURCE_DIR, ignore_errors=True)
    engine = get_engine()
    _recreate_schema(engine)
    with get_session_factory()() as db:
        seed_demo_data(db, password="test-password")
    yield


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_backups():
    shutil.rmtree(TEST_BACKUP_DIR, ignore_errors=True)
    shutil.rmtree(TEST_SOURCE_DIR, ignore_errors=True)
    yield
    shutil.rmtree(TEST_BACKUP_DIR, ignore_errors=True)
    shutil.rmtree(TEST_SOURCE_DIR, ignore_errors=True)


def as_member(member_id: str) -> dict[str, str]:
    return {"X-Dev-Member-Id": member_id}
