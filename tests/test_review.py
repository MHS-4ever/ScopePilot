from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import socket

import pytest
from fastapi.testclient import TestClient

from scopepilot.config import Settings
from scopepilot.main import create_app


API_KEY = "phase3a-api-key"
REVIEW_KEY = "phase3a-review-key"


@pytest.fixture
def review_client(tmp_path: Path):
    database_url = f"sqlite:///{(tmp_path / 'review-test.db').as_posix()}"
    app = create_app(
        Settings(
            api_key=API_KEY,
            database_url=database_url,
            review_key=REVIEW_KEY,
        )
    )
    with TestClient(app) as client:
        yield client


@pytest.fixture
def api_headers() -> dict[str, str]:
    return {"X-API-Key": API_KEY}


@pytest.fixture
def review_headers() -> dict[str, str]:
    return {"X-Review-Key": REVIEW_KEY}


def analysis_result() -> dict:
    return {
        "requirements": [
            {
                "id": "R1",
                "type": "feature",
                "description": "Add library search",
                "source_quote": "add a search box",
                "needs_clarification": True,
            },
            {
                "id": "R2",
                "type": "bug",
                "description": "Fix login button",
                "source_quote": "fix the broken login button",
                "needs_clarification": False,
            },
        ],
        "clarification_questions": ["Which fields should search cover?"],
        "proposed_tasks": [
            {
                "title": "Implement library search",
                "description": "Add a search field to the library page.",
                "type": "feature",
                "requirement_ids": ["R1"],
                "acceptance_criteria": ["Users can submit a search query"],
                "blocked_by_clarification": True,
            },
            {
                "title": "Repair login button",
                "description": "Restore the broken login action.",
                "type": "bug",
                "requirement_ids": ["R2"],
                "acceptance_criteria": ["Login button starts authentication"],
                "blocked_by_clarification": False,
            },
        ],
        "scope_assessment": {
            "status": "unknown",
            "reason": "No original scope was supplied.",
            "related_requirement_ids": [],
        },
        "deadline_quote": None,
    }


def create_brief(client: TestClient, api_headers: dict[str, str]) -> dict:
    response = client.post(
        "/api/v1/briefs",
        headers=api_headers,
        json={
            "source_id": "review-source-1",
            "client_id": "review-client-1",
            "project_name": "Library application",
            "message": "Please add a search box and fix the broken login button.",
        },
    )
    assert response.status_code == 201
    return response.json()


def complete_analysis(
    client: TestClient, api_headers: dict[str, str], brief: dict
) -> dict:
    claim = client.post(
        f"/api/v1/briefs/{brief['id']}/analysis/claim",
        headers=api_headers,
        json={},
    )
    assert claim.status_code == 201
    response = client.post(
        f"/api/v1/briefs/{brief['id']}/analysis/complete",
        headers=api_headers,
        json={
            "claim_token": claim.json()["claim_token"],
            "model_id": "gemini-3.5-flash-lite",
            "prompt_version": "brief-extraction-v1",
            "schema_version": "1",
            "result": analysis_result(),
        },
    )
    assert response.status_code == 200
    return response.json()


def completed_brief(client: TestClient, api_headers: dict[str, str]) -> dict:
    brief = create_brief(client, api_headers)
    complete_analysis(client, api_headers, brief)
    return brief


def initialize(
    client: TestClient,
    review_headers: dict[str, str],
    brief: dict,
) -> dict:
    response = client.post(
        f"/api/v1/briefs/{brief['id']}/review", headers=review_headers
    )
    assert response.status_code == 201
    return response.json()


def patch_task(
    client: TestClient,
    review_headers: dict[str, str],
    brief_id: str,
    review: dict,
    task_index: int,
    **changes,
):
    task = review["tasks"][task_index]
    return client.patch(
        f"/api/v1/briefs/{brief_id}/review/tasks/{task['id']}",
        headers=review_headers,
        json={
            "review_revision": review["revision"],
            "task_revision": task["revision"],
            **changes,
        },
    )


def test_review_endpoints_require_separate_reviewer_authentication(
    review_client: TestClient,
    api_headers: dict[str, str],
    review_headers: dict[str, str],
) -> None:
    missing = review_client.get("/api/v1/review-queue")
    api_key_only = review_client.get("/api/v1/review-queue", headers=api_headers)
    wrong = review_client.get(
        "/api/v1/review-queue", headers={"X-Review-Key": "wrong"}
    )

    assert missing.status_code == 401
    assert api_key_only.status_code == 401
    assert wrong.status_code == 401

    brief = completed_brief(review_client, api_headers)
    review = initialize(review_client, review_headers, brief)
    task = review["tasks"][1]
    api_key_cannot_approve = review_client.patch(
        f"/api/v1/briefs/{brief['id']}/review/tasks/{task['id']}",
        headers=api_headers,
        json={
            "review_revision": review["revision"],
            "task_revision": task["revision"],
            "decision": "approved",
        },
    )
    assert api_key_cannot_approve.status_code == 401


def test_missing_configured_review_key_fails_closed_without_breaking_health(
    tmp_path: Path,
) -> None:
    app = create_app(
        Settings(
            api_key=API_KEY,
            database_url=f"sqlite:///{(tmp_path / 'no-review-key.db').as_posix()}",
        )
    )
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        response = client.get(
            "/api/v1/review-queue", headers={"X-Review-Key": "anything"}
        )
        assert response.status_code == 401


def test_review_initialization_copies_tasks_and_context(
    review_client: TestClient,
    api_headers: dict[str, str],
    review_headers: dict[str, str],
) -> None:
    brief = completed_brief(review_client, api_headers)
    review = initialize(review_client, review_headers, brief)

    assert review["status"] == "in_review"
    assert review["revision"] == 1
    assert review["brief"]["message"] == brief["message"]
    assert len(review["analysis"]["requirements"]) == 2
    assert len(review["tasks"]) == 2
    assert review["tasks"][0]["decision"] == "pending"
    assert review["tasks"][0]["original_blocked_by_clarification"] is True
    assert review["tasks"][1]["original_blocked_by_clarification"] is False
    assert review["tasks"][0]["id"] != review["tasks"][1]["id"]


def test_repeated_initialization_returns_same_review_and_stable_task_ids(
    review_client: TestClient,
    api_headers: dict[str, str],
    review_headers: dict[str, str],
) -> None:
    brief = completed_brief(review_client, api_headers)
    first = initialize(review_client, review_headers, brief)

    repeated = review_client.post(
        f"/api/v1/briefs/{brief['id']}/review", headers=review_headers
    )

    assert repeated.status_code == 200
    assert repeated.json()["id"] == first["id"]
    assert [task["id"] for task in repeated.json()["tasks"]] == [
        task["id"] for task in first["tasks"]
    ]


def test_concurrent_initialization_creates_one_review(
    review_client: TestClient,
    api_headers: dict[str, str],
    review_headers: dict[str, str],
) -> None:
    brief = completed_brief(review_client, api_headers)

    def initialize_once(_index: int):
        return review_client.post(
            f"/api/v1/briefs/{brief['id']}/review", headers=review_headers
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        responses = list(executor.map(initialize_once, range(8)))

    assert sorted(response.status_code for response in responses) == [
        200,
        200,
        200,
        200,
        200,
        200,
        200,
        201,
    ]
    assert len({response.json()["id"] for response in responses}) == 1
    assert len({tuple(task["id"] for task in response.json()["tasks"]) for response in responses}) == 1


def test_only_completed_drafts_are_eligible_for_review(
    review_client: TestClient,
    api_headers: dict[str, str],
    review_headers: dict[str, str],
) -> None:
    brief = create_brief(review_client, api_headers)
    missing = review_client.post(
        f"/api/v1/briefs/{brief['id']}/review", headers=review_headers
    )
    assert missing.status_code == 409

    claim = review_client.post(
        f"/api/v1/briefs/{brief['id']}/analysis/claim",
        headers=api_headers,
        json={},
    ).json()
    processing = review_client.post(
        f"/api/v1/briefs/{brief['id']}/review", headers=review_headers
    )
    assert processing.status_code == 409

    review_client.post(
        f"/api/v1/briefs/{brief['id']}/analysis/fail",
        headers=api_headers,
        json={"claim_token": claim["claim_token"], "error_code": "model_failed"},
    )
    failed = review_client.post(
        f"/api/v1/briefs/{brief['id']}/review", headers=review_headers
    )
    assert failed.status_code == 409


def test_task_fields_and_valid_requirement_links_are_editable(
    review_client: TestClient,
    api_headers: dict[str, str],
    review_headers: dict[str, str],
) -> None:
    brief = completed_brief(review_client, api_headers)
    review = initialize(review_client, review_headers, brief)

    response = patch_task(
        review_client,
        review_headers,
        brief["id"],
        review,
        1,
        title="Repair customer login",
        description="Restore and verify the customer login action.",
        requirement_ids=["R1", "R2"],
        acceptance_criteria=["Login starts authentication", "Search remains available"],
        review_note="Combined verification is intentional.",
    )

    assert response.status_code == 200
    updated = response.json()["tasks"][1]
    assert updated["title"] == "Repair customer login"
    assert updated["requirement_ids"] == ["R1", "R2"]
    assert updated["review_note"] == "Combined verification is intentional."


def test_invalid_requirement_reference_is_rejected(
    review_client: TestClient,
    api_headers: dict[str, str],
    review_headers: dict[str, str],
) -> None:
    brief = completed_brief(review_client, api_headers)
    review = initialize(review_client, review_headers, brief)

    response = patch_task(
        review_client,
        review_headers,
        brief["id"],
        review,
        1,
        requirement_ids=["R99"],
    )

    assert response.status_code == 422
    assert "R99" in response.json()["detail"]

    null_title = patch_task(
        review_client,
        review_headers,
        brief["id"],
        review,
        1,
        title=None,
    )
    assert null_title.status_code == 422

    task = review["tasks"][1]
    extra_field = review_client.patch(
        f"/api/v1/briefs/{brief['id']}/review/tasks/{task['id']}",
        headers=review_headers,
        json={
            "review_revision": review["revision"],
            "task_revision": task["revision"],
            "unexpected": True,
        },
    )
    assert extra_field.status_code == 422


def test_clarification_gate_blocks_approval_until_explicitly_resolved(
    review_client: TestClient,
    api_headers: dict[str, str],
    review_headers: dict[str, str],
) -> None:
    brief = completed_brief(review_client, api_headers)
    review = initialize(review_client, review_headers, brief)

    blocked = patch_task(
        review_client,
        review_headers,
        brief["id"],
        review,
        0,
        decision="approved",
    )
    assert blocked.status_code == 422

    resolved = patch_task(
        review_client,
        review_headers,
        brief["id"],
        review,
        0,
        decision="approved",
        clarification_resolved=True,
        clarification_resolution="Client confirmed search covers titles and authors.",
    )
    assert resolved.status_code == 200
    task = resolved.json()["tasks"][0]
    assert task["decision"] == "approved"
    assert task["clarification_resolved"] is True
    assert task["original_blocked_by_clarification"] is True


def test_blocked_task_can_be_rejected_without_clarification_resolution(
    review_client: TestClient,
    api_headers: dict[str, str],
    review_headers: dict[str, str],
) -> None:
    brief = completed_brief(review_client, api_headers)
    review = initialize(review_client, review_headers, brief)

    response = patch_task(
        review_client,
        review_headers,
        brief["id"],
        review,
        0,
        decision="rejected",
        review_note="Client removed this request.",
    )

    assert response.status_code == 200
    assert response.json()["tasks"][0]["decision"] == "rejected"


def test_incomplete_review_cannot_be_finalized(
    review_client: TestClient,
    api_headers: dict[str, str],
    review_headers: dict[str, str],
) -> None:
    brief = completed_brief(review_client, api_headers)
    review = initialize(review_client, review_headers, brief)

    response = review_client.post(
        f"/api/v1/briefs/{brief['id']}/review/finalize",
        headers=review_headers,
        json={"review_revision": review["revision"]},
    )

    assert response.status_code == 409


def test_all_rejected_review_finalizes_and_duplicate_finalize_is_idempotent(
    review_client: TestClient,
    api_headers: dict[str, str],
    review_headers: dict[str, str],
) -> None:
    brief = completed_brief(review_client, api_headers)
    review = initialize(review_client, review_headers, brief)
    for task_index in range(2):
        response = patch_task(
            review_client,
            review_headers,
            brief["id"],
            review,
            task_index,
            decision="rejected",
        )
        assert response.status_code == 200
        review = response.json()

    finalized = review_client.post(
        f"/api/v1/briefs/{brief['id']}/review/finalize",
        headers=review_headers,
        json={"review_revision": review["revision"]},
    )
    assert finalized.status_code == 200
    assert finalized.json()["status"] == "finalized"

    repeated = review_client.post(
        f"/api/v1/briefs/{brief['id']}/review/finalize",
        headers=review_headers,
        json={"review_revision": review["revision"]},
    )
    assert repeated.status_code == 200
    assert repeated.json()["id"] == finalized.json()["id"]
    assert repeated.json()["finalized_at"] == finalized.json()["finalized_at"]


def test_tasks_are_immutable_after_finalization(
    review_client: TestClient,
    api_headers: dict[str, str],
    review_headers: dict[str, str],
) -> None:
    brief = completed_brief(review_client, api_headers)
    review = initialize(review_client, review_headers, brief)
    for task_index in range(2):
        review = patch_task(
            review_client,
            review_headers,
            brief["id"],
            review,
            task_index,
            decision="rejected",
        ).json()
    review = review_client.post(
        f"/api/v1/briefs/{brief['id']}/review/finalize",
        headers=review_headers,
        json={"review_revision": review["revision"]},
    ).json()

    response = patch_task(
        review_client,
        review_headers,
        brief["id"],
        review,
        0,
        title="Forbidden edit",
    )
    assert response.status_code == 409


def test_concurrent_updates_allow_only_one_revision_winner(
    review_client: TestClient,
    api_headers: dict[str, str],
    review_headers: dict[str, str],
) -> None:
    brief = completed_brief(review_client, api_headers)
    review = initialize(review_client, review_headers, brief)
    task = review["tasks"][1]

    def update_title(index: int):
        return review_client.patch(
            f"/api/v1/briefs/{brief['id']}/review/tasks/{task['id']}",
            headers=review_headers,
            json={
                "review_revision": review["revision"],
                "task_revision": task["revision"],
                "title": f"Concurrent title {index}",
            },
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(update_title, range(2)))

    assert sorted(response.status_code for response in responses) == [200, 409]


def test_original_ai_result_remains_unchanged_after_review_edits(
    review_client: TestClient,
    api_headers: dict[str, str],
    review_headers: dict[str, str],
) -> None:
    brief = completed_brief(review_client, api_headers)
    original = review_client.get(
        f"/api/v1/briefs/{brief['id']}/analysis", headers=api_headers
    ).json()["result"]
    review = initialize(review_client, review_headers, brief)
    response = patch_task(
        review_client,
        review_headers,
        brief["id"],
        review,
        1,
        title="Human-edited task title",
        decision="approved",
    )
    assert response.status_code == 200

    after = review_client.get(
        f"/api/v1/briefs/{brief['id']}/analysis", headers=api_headers
    ).json()["result"]
    assert after == original
    assert after["proposed_tasks"][1]["title"] == "Repair login button"


def test_review_queue_tracks_ready_and_in_review_and_excludes_finalized(
    review_client: TestClient,
    api_headers: dict[str, str],
    review_headers: dict[str, str],
) -> None:
    brief = completed_brief(review_client, api_headers)
    ready = review_client.get("/api/v1/review-queue", headers=review_headers)
    assert ready.json()["items"][0]["review_status"] == "ready"

    review = initialize(review_client, review_headers, brief)
    active = review_client.get("/api/v1/review-queue", headers=review_headers)
    assert active.json()["items"][0]["review_status"] == "in_review"

    for index in range(2):
        review = patch_task(
            review_client,
            review_headers,
            brief["id"],
            review,
            index,
            decision="rejected",
        ).json()
    review_client.post(
        f"/api/v1/briefs/{brief['id']}/review/finalize",
        headers=review_headers,
        json={"review_revision": review["revision"]},
    )
    final_queue = review_client.get("/api/v1/review-queue", headers=review_headers)
    assert final_queue.json() == {"items": []}


def test_review_persists_across_application_sessions(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'persistent-review.db').as_posix()}"
    settings = Settings(
        api_key=API_KEY, database_url=database_url, review_key=REVIEW_KEY
    )
    with TestClient(create_app(settings)) as first_client:
        brief = completed_brief(first_client, {"X-API-Key": API_KEY})
        review = initialize(first_client, {"X-Review-Key": REVIEW_KEY}, brief)

    with TestClient(create_app(settings)) as second_client:
        response = second_client.get(
            f"/api/v1/briefs/{brief['id']}/review",
            headers={"X-Review-Key": REVIEW_KEY},
        )
        assert response.status_code == 200
        assert response.json()["id"] == review["id"]
        assert [task["id"] for task in response.json()["tasks"]] == [
            task["id"] for task in review["tasks"]
        ]


def test_finalization_has_no_external_publishing_side_effect(
    review_client: TestClient,
    api_headers: dict[str, str],
    review_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    brief = completed_brief(review_client, api_headers)
    review = initialize(review_client, review_headers, brief)
    for index in range(2):
        review = patch_task(
            review_client,
            review_headers,
            brief["id"],
            review,
            index,
            decision="rejected",
        ).json()

    def fail_network(*_args, **_kwargs):
        raise AssertionError("Review finalization attempted external network access")

    monkeypatch.setattr(socket, "create_connection", fail_network)
    response = review_client.post(
        f"/api/v1/briefs/{brief['id']}/review/finalize",
        headers=review_headers,
        json={"review_revision": review["revision"]},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "finalized"
