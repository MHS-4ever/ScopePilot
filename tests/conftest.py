from collections.abc import Generator
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("SCOPEPILOT_API_KEY", "test-import-api-key")
os.environ.setdefault("SCOPEPILOT_DATABASE_URL", "sqlite:///:memory:")

from scopepilot.config import Settings
from scopepilot.main import create_app


TEST_API_KEY = "test-api-key"


@pytest.fixture
def client(tmp_path: Path) -> Generator[TestClient, None, None]:
    database_url = f"sqlite:///{(tmp_path / 'scopepilot-test.db').as_posix()}"
    app = create_app(Settings(api_key=TEST_API_KEY, database_url=database_url))
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"X-API-Key": TEST_API_KEY}


@pytest.fixture
def valid_payload() -> dict[str, str]:
    return {
        "source_id": "email-2026-001",
        "client_id": "client-42",
        "project_name": "Customer portal refresh",
        "message": "Please rebuild the customer portal and preserve SSO.",
        "original_scope": "Discovery and implementation",
    }

