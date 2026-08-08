from __future__ import annotations

import os
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_database_file = tempfile.NamedTemporaryFile(prefix="devplatform-tests-", suffix=".sqlite", delete=False)
_database_file.close()
os.environ["DEVPLATFORM_DATABASE_URL"] = f"sqlite:///{_database_file.name}"
os.environ["DEVPLATFORM_WORKER_ENABLED"] = "false"

from app.core.bootstrap import bootstrap  # noqa: E402
from app.core.registry import registry  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.init_db import init_db  # noqa: E402
from app.db.session import engine  # noqa: E402
from app.external.registry import external_backends  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_database() -> Generator[None, None, None]:
    Base.metadata.drop_all(bind=engine)
    init_db()
    registry.agents.clear()
    registry.tools.clear()
    external_backends.clear()
    bootstrap()
    yield


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    with TestClient(app) as test_client:
        yield test_client


def pytest_sessionfinish() -> None:
    engine.dispose()
    Path(_database_file.name).unlink(missing_ok=True)
