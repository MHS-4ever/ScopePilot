from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from scopepilot.config import Settings
from scopepilot.github_service import (
    GitHubAmbiguousError,
    GitHubDefinitiveError,
    GitHubIssue,
)
from scopepilot.main import create_app
from scopepilot.models import Brief, BriefAnalysis, BriefReview, ReviewTask


REVIEW_KEY = "phase4-review-key"
GITHUB_TOKEN = "github-secret-that-must-not-leak"
REPOSITORY = "scopepilot-owner/scopepilot-repo"


def _settings(path: Path, configured: bool = True) -> Settings:
    return Settings(
        api_key="phase4-api-key",
        database_url=f"sqlite:///{path.as_posix()}",
        review_key=REVIEW_KEY,
        github_token=GITHUB_TOKEN if configured else None,
        github_repo=REPOSITORY if configured else None,
    )


def _seed_review(app, review_status: str, decisions: list[str]) -> tuple[str, list[str]]:
    now = datetime.now(timezone.utc).isoformat()
    brief_id = str(uuid4())
    analysis_id = str(uuid4())
    review_id = str(uuid4())
    task_ids = [str(uuid4()) for _ in decisions]
    with app.state.session_factory() as session:
        session.add(
            Brief(
                id=brief_id,
                source_id=str(uuid4()),
                client_id="phase4-client",
                project_name="Phase 4 publishing",
                message="Create the approved work items.",
                received_at=now,
                status="received",
                payload_fingerprint="a" * 64,
            )
        )
        session.add(
            BriefAnalysis(
                id=analysis_id,
                brief_id=brief_id,
                status="draft",
                claim_token=str(uuid4()),
                attempt_count=1,
                claimed_at=now,
                lease_expires_at=now,
                created_at=now,
                updated_at=now,
                completed_at=now,
                model_id="test-model",
                prompt_version="test-prompt",
                schema_version="1",
                result_json=json.dumps(
                    {
                        "requirements": [],
                        "clarification_questions": [],
                        "proposed_tasks": [],
                        "scope_assessment": {
                            "status": "unknown",
                            "reason": "Test fixture",
                            "related_requirement_ids": [],
                        },
                        "deadline_quote": None,
                    }
                ),
            )
        )
        session.add(
            BriefReview(
                id=review_id,
                brief_id=brief_id,
                analysis_id=analysis_id,
                status=review_status,
                revision=4,
                created_at=now,
                updated_at=now,
                finalized_at=now if review_status == "finalized" else None,
            )
        )
        for position, (task_id, decision) in enumerate(zip(task_ids, decisions)):
            session.add(
                ReviewTask(
                    id=task_id,
                    review_id=review_id,
                    position=position,
                    task_type="feature",
                    title=f"Reviewed task {position + 1}",
                    description=f"Final description {position + 1}",
                    requirement_ids_json='["R1"]',
                    acceptance_criteria_json='["Criterion one","Criterion two"]',
                    original_blocked_by_clarification=False,
                    decision=decision,
                    clarification_resolved=False,
                    revision=2,
                    created_at=now,
                    updated_at=now,
                )
            )
        session.commit()
    return brief_id, task_ids


class RecordingGitHubClient:
    create_calls: list[tuple[str, str]] = []

    def __init__(self, token: str, repository: str) -> None:
        assert token == GITHUB_TOKEN
        assert repository == REPOSITORY

    def create_issue(self, title: str, body: str) -> GitHubIssue:
        self.create_calls.append((title, body))
        return GitHubIssue(number=42)

    def find_issue(self, marker: str) -> GitHubIssue | None:
        return None


def test_publishes_only_approved_tasks_with_safe_payload_and_prevents_duplicates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app(_settings(tmp_path / "publish.db"))
    RecordingGitHubClient.create_calls = []
    monkeypatch.setattr(
        "scopepilot.github_service.GitHubClient", RecordingGitHubClient
    )
    with TestClient(app) as client:
        brief_id, task_ids = _seed_review(
            app, "finalized", ["approved", "rejected", "pending"]
        )
        url = f"/api/v1/briefs/{brief_id}/review/publish"
        first = client.post(url, headers={"X-Review-Key": REVIEW_KEY})
        repeated = client.post(url, headers={"X-Review-Key": REVIEW_KEY})

    assert first.status_code == 200
    assert [item["status"] for item in first.json()["results"]] == ["published"]
    assert repeated.json()["results"][0]["status"] == "already_published"
    assert repeated.json()["results"][0]["issue_url"] == (
        f"https://github.com/{REPOSITORY}/issues/42"
    )
    assert len(RecordingGitHubClient.create_calls) == 1
    title, body = RecordingGitHubClient.create_calls[0]
    assert title == "Reviewed task 1"
    assert "Final description 1" in body
    assert "- `R1`" in body
    assert "- [ ] Criterion one" in body
    assert f"ScopePilot task: `{task_ids[0]}`" in body
    assert f"<!-- scopepilot-task-id:{task_ids[0]} -->" in body
    assert GITHUB_TOKEN not in first.text + repeated.text + body


def test_rejects_unfinalized_review_before_any_github_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app(_settings(tmp_path / "unfinalized.db"))
    RecordingGitHubClient.create_calls = []
    monkeypatch.setattr(
        "scopepilot.github_service.GitHubClient", RecordingGitHubClient
    )
    with TestClient(app) as client:
        brief_id, _ = _seed_review(app, "in_review", ["approved"])
        response = client.post(
            f"/api/v1/briefs/{brief_id}/review/publish",
            headers={"X-Review-Key": REVIEW_KEY},
        )

    assert response.status_code == 409
    assert RecordingGitHubClient.create_calls == []


def test_publication_auth_configuration_and_github_auth_failures_are_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unconfigured = create_app(_settings(tmp_path / "unconfigured.db", configured=False))
    with TestClient(unconfigured) as client:
        brief_id, _ = _seed_review(unconfigured, "finalized", ["approved"])
        url = f"/api/v1/briefs/{brief_id}/review/publish"
        assert client.post(url).status_code == 401
        assert client.post(url, headers={"X-Review-Key": REVIEW_KEY}).status_code == 503

    class UnauthorizedClient(RecordingGitHubClient):
        def create_issue(self, title: str, body: str) -> GitHubIssue:
            raise GitHubDefinitiveError("GitHub rejected the request with HTTP 401")

    configured = create_app(_settings(tmp_path / "unauthorized.db"))
    monkeypatch.setattr("scopepilot.github_service.GitHubClient", UnauthorizedClient)
    with TestClient(configured) as client:
        brief_id, _ = _seed_review(configured, "finalized", ["approved"])
        response = client.post(
            f"/api/v1/briefs/{brief_id}/review/publish",
            headers={"X-Review-Key": REVIEW_KEY},
        )

    assert response.status_code == 200
    assert response.json()["results"][0]["status"] == "failed"
    assert GITHUB_TOKEN not in response.text


def test_ambiguous_creation_requires_reconciliation_then_finds_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class AmbiguousThenFoundClient(RecordingGitHubClient):
        create_count = 0

        def create_issue(self, title: str, body: str) -> GitHubIssue:
            type(self).create_count += 1
            raise GitHubAmbiguousError("timeout")

        def find_issue(self, marker: str) -> GitHubIssue | None:
            assert marker.startswith("scopepilot-task-id:")
            return GitHubIssue(number=77)

    app = create_app(_settings(tmp_path / "reconcile.db"))
    AmbiguousThenFoundClient.create_count = 0
    monkeypatch.setattr(
        "scopepilot.github_service.GitHubClient", AmbiguousThenFoundClient
    )
    with TestClient(app) as client:
        brief_id, _ = _seed_review(app, "finalized", ["approved"])
        url = f"/api/v1/briefs/{brief_id}/review/publish"
        first = client.post(url, headers={"X-Review-Key": REVIEW_KEY})
        reconciled = client.post(url, headers={"X-Review-Key": REVIEW_KEY})

    assert first.json()["results"][0]["status"] == "requires_reconciliation"
    assert reconciled.json()["results"][0]["status"] == "already_published"
    assert reconciled.json()["results"][0]["issue_number"] == 77
    assert AmbiguousThenFoundClient.create_count == 1
