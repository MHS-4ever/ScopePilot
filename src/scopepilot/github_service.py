from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
import socket
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus
from urllib.request import Request, urlopen
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from scopepilot.config import Settings
from scopepilot.github_schemas import PublicationResult, ReviewPublishResponse
from scopepilot.models import BriefReview, ReviewTask, TaskPublication


REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class GitHubDefinitiveError(Exception):
    pass


class GitHubAmbiguousError(Exception):
    pass


@dataclass(frozen=True)
class GitHubIssue:
    number: int


class GitHubClient:
    def __init__(self, token: str, repository: str, timeout: float = 10.0) -> None:
        self._token = token
        self.repository = repository
        self._timeout = timeout

    def _request(
        self, method: str, url: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(
            url,
            data=data,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "User-Agent": "ScopePilot",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urlopen(request, timeout=self._timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code >= 500 or exc.code in {408, 429}:
                raise GitHubAmbiguousError("GitHub returned an uncertain response") from exc
            raise GitHubDefinitiveError(
                f"GitHub rejected the request with HTTP {exc.code}"
            ) from exc
        except (TimeoutError, socket.timeout, URLError, OSError) as exc:
            raise GitHubAmbiguousError("GitHub request outcome is uncertain") from exc
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise GitHubAmbiguousError("GitHub returned an unreadable response") from exc

    def create_issue(self, title: str, body: str) -> GitHubIssue:
        result = self._request(
            "POST",
            f"https://api.github.com/repos/{self.repository}/issues",
            {"title": title, "body": body},
        )
        number = result.get("number")
        if not isinstance(number, int) or number < 1:
            raise GitHubAmbiguousError("GitHub issue number was missing")
        return GitHubIssue(number=number)

    def find_issue(self, marker: str) -> GitHubIssue | None:
        query = quote_plus(f'repo:{self.repository} is:issue "{marker}"')
        result = self._request("GET", f"https://api.github.com/search/issues?q={query}")
        for item in result.get("items", []):
            if marker in (item.get("body") or "") and isinstance(item.get("number"), int):
                return GitHubIssue(number=item["number"])
        return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _marker(task_id: str) -> str:
    return f"scopepilot-task-id:{task_id}"


def _issue_url(repository: str, number: int) -> str:
    return f"https://github.com/{repository}/issues/{number}"


def _issue_body(review: BriefReview, task: ReviewTask) -> str:
    requirements = json.loads(task.requirement_ids_json)
    criteria = json.loads(task.acceptance_criteria_json)
    requirement_lines = "\n".join(f"- `{item}`" for item in requirements)
    criterion_lines = "\n".join(f"- [ ] {item}" for item in criteria)
    return (
        "## Description\n\n"
        f"{task.description}\n\n"
        "## Linked requirements\n\n"
        f"{requirement_lines}\n\n"
        "## Acceptance criteria\n\n"
        f"{criterion_lines}\n\n"
        "---\n"
        f"ScopePilot review: `{review.id}`  \n"
        f"ScopePilot task: `{task.id}`\n\n"
        f"<!-- {_marker(task.id)} -->"
    )


def _result(
    task: ReviewTask,
    result_status: str,
    publication: TaskPublication | None = None,
    detail: str | None = None,
) -> PublicationResult:
    return PublicationResult(
        task_id=UUID(task.id),
        task_title=task.title,
        status=result_status,  # type: ignore[arg-type]
        issue_number=publication.github_issue_number if publication else None,
        issue_url=publication.github_issue_url if publication else None,
        detail=detail,
    )


def _save_status(
    session: Session,
    publication: TaskPublication,
    publication_status: str,
    repository: str,
    issue: GitHubIssue | None = None,
) -> None:
    publication.status = publication_status
    publication.updated_at = _now_iso()
    if issue is not None:
        publication.github_issue_number = issue.number
        publication.github_issue_url = _issue_url(repository, issue.number)
    session.commit()


def _claim_publication(
    session: Session, task: ReviewTask, repository: str
) -> tuple[TaskPublication, bool]:
    existing = session.scalar(
        select(TaskPublication).where(
            TaskPublication.review_task_id == task.id,
            TaskPublication.target_repository == repository,
        )
    )
    if existing is not None:
        return existing, False
    now = _now_iso()
    publication = TaskPublication(
        id=str(uuid4()),
        review_task_id=task.id,
        target_repository=repository,
        status="publishing",
        created_at=now,
        updated_at=now,
    )
    session.add(publication)
    try:
        session.commit()
        return publication, True
    except IntegrityError:
        session.rollback()
        concurrent = session.scalar(
            select(TaskPublication).where(
                TaskPublication.review_task_id == task.id,
                TaskPublication.target_repository == repository,
            )
        )
        if concurrent is None:
            raise
        return concurrent, False


def _publish_task(
    session: Session,
    review: BriefReview,
    task: ReviewTask,
    repository: str,
    client: GitHubClient,
) -> PublicationResult:
    publication, claimed = _claim_publication(session, task, repository)
    if publication.status == "published":
        return _result(task, "already_published", publication)

    if not claimed and publication.status in {"publishing", "requires_reconciliation"}:
        try:
            issue = client.find_issue(_marker(task.id))
        except (GitHubAmbiguousError, GitHubDefinitiveError):
            issue = None
        if issue is None:
            _save_status(session, publication, "requires_reconciliation", repository)
            return _result(
                task,
                "requires_reconciliation",
                publication,
                "Issue creation may have completed; reconcile again before retrying.",
            )
        _save_status(session, publication, "published", repository, issue)
        return _result(task, "already_published", publication)

    if not claimed and publication.status == "failed":
        publication.status = "publishing"
        publication.updated_at = _now_iso()
        session.commit()

    try:
        issue = client.create_issue(task.title, _issue_body(review, task))
    except GitHubDefinitiveError as exc:
        _save_status(session, publication, "failed", repository)
        return _result(task, "failed", publication, str(exc))
    except GitHubAmbiguousError:
        _save_status(session, publication, "requires_reconciliation", repository)
        return _result(
            task,
            "requires_reconciliation",
            publication,
            "GitHub response was uncertain; reconcile before retrying.",
        )

    _save_status(session, publication, "published", repository, issue)
    return _result(task, "published", publication)


def publish_review(
    session: Session, brief_id: UUID, settings: Settings
) -> ReviewPublishResponse:
    review = session.scalar(
        select(BriefReview).where(BriefReview.brief_id == str(brief_id))
    )
    if review is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review not found")
    if review.status != "finalized":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only finalized reviews can be published",
        )
    if (
        not settings.github_token
        or not settings.github_repo
        or not REPOSITORY_PATTERN.fullmatch(settings.github_repo)
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GitHub publishing is not configured",
        )

    tasks = session.scalars(
        select(ReviewTask)
        .where(
            ReviewTask.review_id == review.id,
            ReviewTask.decision == "approved",
        )
        .order_by(ReviewTask.position)
    ).all()
    for task in tasks:
        if task.original_blocked_by_clarification and (
            not task.clarification_resolved or not task.clarification_resolution
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Approved tasks must resolve mandatory clarification before publishing",
            )

    client = GitHubClient(settings.github_token, settings.github_repo)
    results = [
        _publish_task(session, review, task, settings.github_repo, client)
        for task in tasks
    ]
    return ReviewPublishResponse(
        review_id=UUID(review.id),
        target_repository=settings.github_repo,
        results=results,
    )
