from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from fastapi.testclient import TestClient


def test_health_is_public(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_successful_ingestion(
    client: TestClient,
    auth_headers: dict[str, str],
    valid_payload: dict[str, str],
) -> None:
    response = client.post("/api/v1/briefs", headers=auth_headers, json=valid_payload)

    assert response.status_code == 201
    body = response.json()
    assert body["source_id"] == valid_payload["source_id"]
    assert body["client_id"] == valid_payload["client_id"]
    assert body["status"] == "received"
    received_at = datetime.fromisoformat(body["received_at"])
    assert received_at.tzinfo == timezone.utc


def test_brief_persists_and_can_be_retrieved(
    client: TestClient,
    auth_headers: dict[str, str],
    valid_payload: dict[str, str],
) -> None:
    created = client.post(
        "/api/v1/briefs", headers=auth_headers, json=valid_payload
    ).json()

    response = client.get(
        f"/api/v1/briefs/{created['id']}", headers=auth_headers
    )

    assert response.status_code == 200
    assert response.json() == created


def test_missing_required_field_is_rejected(
    client: TestClient,
    auth_headers: dict[str, str],
    valid_payload: dict[str, str],
) -> None:
    valid_payload.pop("message")

    response = client.post("/api/v1/briefs", headers=auth_headers, json=valid_payload)

    assert response.status_code == 422


def test_invalid_field_type_is_rejected(
    client: TestClient,
    auth_headers: dict[str, str],
    valid_payload: dict[str, str],
) -> None:
    valid_payload["client_id"] = 123  # type: ignore[assignment]

    response = client.post("/api/v1/briefs", headers=auth_headers, json=valid_payload)

    assert response.status_code == 422


def test_oversized_message_is_rejected(
    client: TestClient,
    auth_headers: dict[str, str],
    valid_payload: dict[str, str],
) -> None:
    valid_payload["message"] = "x" * 20_001

    response = client.post("/api/v1/briefs", headers=auth_headers, json=valid_payload)

    assert response.status_code == 422


def test_empty_trimmed_value_is_rejected(
    client: TestClient,
    auth_headers: dict[str, str],
    valid_payload: dict[str, str],
) -> None:
    valid_payload["project_name"] = "   "

    response = client.post("/api/v1/briefs", headers=auth_headers, json=valid_payload)

    assert response.status_code == 422


def test_missing_and_invalid_authentication(
    client: TestClient,
    valid_payload: dict[str, str],
) -> None:
    missing = client.post("/api/v1/briefs", json=valid_payload)
    invalid = client.post(
        "/api/v1/briefs", headers={"X-API-Key": "wrong"}, json=valid_payload
    )

    assert missing.status_code == 401
    assert invalid.status_code == 401


def test_retrieval_requires_authentication(client: TestClient) -> None:
    response = client.get("/api/v1/briefs/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 401


def test_identical_duplicate_returns_existing_brief(
    client: TestClient,
    auth_headers: dict[str, str],
    valid_payload: dict[str, str],
) -> None:
    first = client.post("/api/v1/briefs", headers=auth_headers, json=valid_payload)
    duplicate = client.post(
        "/api/v1/briefs", headers=auth_headers, json=valid_payload
    )

    assert first.status_code == 201
    assert duplicate.status_code == 201
    assert duplicate.json()["id"] == first.json()["id"]
    assert duplicate.json()["received_at"] == first.json()["received_at"]


def test_conflicting_duplicate_returns_409(
    client: TestClient,
    auth_headers: dict[str, str],
    valid_payload: dict[str, str],
) -> None:
    first = client.post("/api/v1/briefs", headers=auth_headers, json=valid_payload)
    valid_payload["message"] = "This content is different."

    conflict = client.post(
        "/api/v1/briefs", headers=auth_headers, json=valid_payload
    )

    assert first.status_code == 201
    assert conflict.status_code == 409


def test_concurrent_identical_requests_create_one_brief(
    client: TestClient,
    auth_headers: dict[str, str],
    valid_payload: dict[str, str],
) -> None:
    def ingest() -> tuple[int, str]:
        response = client.post(
            "/api/v1/briefs", headers=auth_headers, json=valid_payload
        )
        return response.status_code, response.json()["id"]

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _index: ingest(), range(16)))

    assert {status_code for status_code, _brief_id in results} == {201}
    assert len({brief_id for _status_code, brief_id in results}) == 1

