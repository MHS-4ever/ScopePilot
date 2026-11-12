from datetime import datetime, timezone
import json
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from scopepilot.analysis_schemas import AnalysisResult
from scopepilot.models import (
    Brief,
    BriefAnalysis,
    BriefReview,
    ReviewTask,
    TaskPublication,
)
from scopepilot.review_schemas import (
    ReviewAnalysisContext,
    ReviewBriefContext,
    ReviewFinalizeRequest,
    ReviewQueueItem,
    ReviewQueueResponse,
    ReviewResponse,
    ReviewTaskPatch,
    ReviewTaskResponse,
    TaskPublicationResponse,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_list(value: list[str]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _analysis_result(analysis: BriefAnalysis) -> AnalysisResult:
    if analysis.status != "draft" or not analysis.result_json:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only completed, valid analysis drafts can be reviewed",
        )
    try:
        return AnalysisResult.model_validate_json(analysis.result_json)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The completed analysis draft is invalid",
        ) from exc


def _get_brief(session: Session, brief_id: UUID) -> Brief:
    brief = session.get(Brief, str(brief_id))
    if brief is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Brief not found"
        )
    return brief


def _get_review(session: Session, brief_id: UUID) -> BriefReview:
    review = session.scalar(
        select(BriefReview).where(BriefReview.brief_id == str(brief_id))
    )
    if review is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Review not found"
        )
    return review


def _review_response(
    session: Session, review: BriefReview, brief: Brief | None = None
) -> ReviewResponse:
    brief = brief or session.get(Brief, review.brief_id)
    analysis = session.get(BriefAnalysis, review.analysis_id)
    if brief is None or analysis is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Review context is incomplete",
        )
    result = _analysis_result(analysis)
    tasks = session.scalars(
        select(ReviewTask)
        .where(ReviewTask.review_id == review.id)
        .order_by(ReviewTask.position)
    ).all()
    task_ids = [task.id for task in tasks]
    publications = (
        session.scalars(
            select(TaskPublication).where(
                TaskPublication.review_task_id.in_(task_ids)
            )
        ).all()
        if task_ids
        else []
    )
    publications_by_task: dict[str, list[TaskPublication]] = {}
    for publication in publications:
        publications_by_task.setdefault(publication.review_task_id, []).append(
            publication
        )
    return ReviewResponse(
        id=UUID(review.id),
        status=review.status,  # type: ignore[arg-type]
        revision=review.revision,
        created_at=datetime.fromisoformat(review.created_at),
        updated_at=datetime.fromisoformat(review.updated_at),
        finalized_at=(
            datetime.fromisoformat(review.finalized_at)
            if review.finalized_at
            else None
        ),
        brief=ReviewBriefContext(
            id=UUID(brief.id),
            client_id=brief.client_id,
            project_name=brief.project_name,
            message=brief.message,
            original_scope=brief.original_scope,
            received_at=datetime.fromisoformat(brief.received_at),
        ),
        analysis=ReviewAnalysisContext(
            id=UUID(analysis.id),
            model_id=analysis.model_id or "unknown",
            prompt_version=analysis.prompt_version or "unknown",
            schema_version=analysis.schema_version or "unknown",
            completed_at=datetime.fromisoformat(analysis.completed_at),  # type: ignore[arg-type]
            requirements=result.requirements,
            clarification_questions=result.clarification_questions,
            scope_assessment=result.scope_assessment,
            deadline_quote=result.deadline_quote,
        ),
        tasks=[
            ReviewTaskResponse(
                id=UUID(task.id),
                position=task.position,
                type=task.task_type,  # type: ignore[arg-type]
                title=task.title,
                description=task.description,
                requirement_ids=json.loads(task.requirement_ids_json),
                acceptance_criteria=json.loads(task.acceptance_criteria_json),
                original_blocked_by_clarification=task.original_blocked_by_clarification,
                decision=task.decision,  # type: ignore[arg-type]
                review_note=task.review_note,
                clarification_resolved=task.clarification_resolved,
                clarification_resolution=task.clarification_resolution,
                revision=task.revision,
                updated_at=datetime.fromisoformat(task.updated_at),
                publications=[
                    TaskPublicationResponse(
                        target_repository=publication.target_repository,
                        status=publication.status,  # type: ignore[arg-type]
                        github_issue_number=publication.github_issue_number,
                        github_issue_url=publication.github_issue_url,
                        updated_at=datetime.fromisoformat(publication.updated_at),
                    )
                    for publication in publications_by_task.get(task.id, [])
                ],
            )
            for task in tasks
        ],
    )


def initialize_review(
    session: Session, brief_id: UUID
) -> tuple[int, ReviewResponse]:
    brief = _get_brief(session, brief_id)
    analysis = session.scalar(
        select(BriefAnalysis).where(BriefAnalysis.brief_id == str(brief_id))
    )
    if analysis is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only completed, valid analysis drafts can be reviewed",
        )
    result = _analysis_result(analysis)

    existing = session.scalar(
        select(BriefReview).where(BriefReview.brief_id == str(brief_id))
    )
    if existing is not None:
        return status.HTTP_200_OK, _review_response(session, existing, brief)

    now = _now_iso()
    review = BriefReview(
        id=str(uuid4()),
        brief_id=brief.id,
        analysis_id=analysis.id,
        status="in_review",
        revision=1,
        created_at=now,
        updated_at=now,
    )
    session.add(review)
    for position, proposed in enumerate(result.proposed_tasks):
        session.add(
            ReviewTask(
                id=str(uuid4()),
                review_id=review.id,
                position=position,
                task_type=proposed.type,
                title=proposed.title,
                description=proposed.description,
                requirement_ids_json=_json_list(proposed.requirement_ids),
                acceptance_criteria_json=_json_list(proposed.acceptance_criteria),
                original_blocked_by_clarification=proposed.blocked_by_clarification,
                decision="pending",
                clarification_resolved=False,
                revision=1,
                created_at=now,
                updated_at=now,
            )
        )
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.scalar(
            select(BriefReview).where(BriefReview.brief_id == str(brief_id))
        )
        if existing is None:
            raise
        return status.HTTP_200_OK, _review_response(session, existing, brief)

    return status.HTTP_201_CREATED, _review_response(session, review, brief)


def get_review(session: Session, brief_id: UUID) -> ReviewResponse:
    brief = _get_brief(session, brief_id)
    return _review_response(session, _get_review(session, brief_id), brief)


def update_review_task(
    session: Session,
    brief_id: UUID,
    task_id: UUID,
    payload: ReviewTaskPatch,
) -> ReviewResponse:
    _get_brief(session, brief_id)
    review = _get_review(session, brief_id)
    if review.status == "finalized":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Finalized reviews are immutable",
        )
    task = session.scalar(
        select(ReviewTask).where(
            ReviewTask.id == str(task_id), ReviewTask.review_id == review.id
        )
    )
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Review task not found"
        )
    if review.revision != payload.review_revision:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Review revision is stale",
        )
    if task.revision != payload.task_revision:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Task revision is stale",
        )

    fields = payload.model_fields_set
    title = payload.title if "title" in fields else task.title
    description = (
        payload.description if "description" in fields else task.description
    )
    requirement_ids = (
        payload.requirement_ids
        if "requirement_ids" in fields
        else json.loads(task.requirement_ids_json)
    )
    acceptance_criteria = (
        payload.acceptance_criteria
        if "acceptance_criteria" in fields
        else json.loads(task.acceptance_criteria_json)
    )
    decision = payload.decision if "decision" in fields else task.decision
    review_note = payload.review_note if "review_note" in fields else task.review_note
    clarification_resolved = (
        payload.clarification_resolved
        if "clarification_resolved" in fields
        else task.clarification_resolved
    )
    clarification_resolution = (
        payload.clarification_resolution
        if "clarification_resolution" in fields
        else task.clarification_resolution
    )

    analysis = session.get(BriefAnalysis, review.analysis_id)
    if analysis is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Review analysis is missing",
        )
    valid_requirement_ids = {req.id for req in _analysis_result(analysis).requirements}
    invalid_ids = set(requirement_ids) - valid_requirement_ids
    if invalid_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown requirement IDs: {', '.join(sorted(invalid_ids))}",
        )

    if clarification_resolved and not clarification_resolution:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Clarification resolution requires a nonempty explanation",
        )
    if (
        decision == "approved"
        and task.original_blocked_by_clarification
        and (not clarification_resolved or not clarification_resolution)
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Blocked tasks require explicit clarification resolution before approval",
        )

    now = _now_iso()
    review_update = session.execute(
        update(BriefReview)
        .where(
            BriefReview.id == review.id,
            BriefReview.status == "in_review",
            BriefReview.revision == payload.review_revision,
        )
        .values(
            revision=BriefReview.revision + 1,
            updated_at=now,
        )
    )
    if review_update.rowcount == 0:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Review revision is stale",
        )

    task_update = session.execute(
        update(ReviewTask)
        .where(
            ReviewTask.id == task.id,
            ReviewTask.review_id == review.id,
            ReviewTask.revision == payload.task_revision,
        )
        .values(
            title=title,
            description=description,
            requirement_ids_json=_json_list(requirement_ids),
            acceptance_criteria_json=_json_list(acceptance_criteria),
            decision=decision,
            review_note=review_note,
            clarification_resolved=clarification_resolved,
            clarification_resolution=clarification_resolution,
            revision=ReviewTask.revision + 1,
            updated_at=now,
        )
    )
    if task_update.rowcount == 0:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Task revision is stale",
        )
    session.commit()
    updated_review = session.get(BriefReview, review.id)
    return _review_response(session, updated_review)  # type: ignore[arg-type]


def finalize_review(
    session: Session, brief_id: UUID, payload: ReviewFinalizeRequest
) -> ReviewResponse:
    _get_brief(session, brief_id)
    review = _get_review(session, brief_id)
    if review.status == "finalized":
        return _review_response(session, review)
    if review.revision != payload.review_revision:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Review revision is stale",
        )
    pending = session.scalar(
        select(ReviewTask.id)
        .where(
            ReviewTask.review_id == review.id,
            ReviewTask.decision == "pending",
        )
        .limit(1)
    )
    if pending is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Every review task must be approved or rejected before finalization",
        )

    now = _now_iso()
    result = session.execute(
        update(BriefReview)
        .where(
            BriefReview.id == review.id,
            BriefReview.status == "in_review",
            BriefReview.revision == payload.review_revision,
        )
        .values(
            status="finalized",
            revision=BriefReview.revision + 1,
            updated_at=now,
            finalized_at=now,
        )
    )
    if result.rowcount == 0:
        session.rollback()
        current = session.get(BriefReview, review.id)
        if current is not None and current.status == "finalized":
            return _review_response(session, current)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Review revision is stale",
        )
    session.commit()
    finalized = session.get(BriefReview, review.id)
    return _review_response(session, finalized)  # type: ignore[arg-type]


def get_review_queue(session: Session) -> ReviewQueueResponse:
    analyses = session.scalars(
        select(BriefAnalysis)
        .where(BriefAnalysis.status == "draft")
        .order_by(BriefAnalysis.completed_at, BriefAnalysis.id)
    ).all()
    items: list[ReviewQueueItem] = []
    for analysis in analyses:
        brief = session.get(Brief, analysis.brief_id)
        if brief is None or not analysis.completed_at:
            continue
        try:
            analysis_result = _analysis_result(analysis)
        except HTTPException:
            continue
        review = session.scalar(
            select(BriefReview).where(BriefReview.analysis_id == analysis.id)
        )
        if review is not None and review.status == "finalized":
            continue
        items.append(
            ReviewQueueItem(
                brief_id=UUID(brief.id),
                client_id=brief.client_id,
                project_name=brief.project_name,
                received_at=datetime.fromisoformat(brief.received_at),
                analysis_id=UUID(analysis.id),
                analysis_completed_at=datetime.fromisoformat(analysis.completed_at),
                proposed_task_count=len(analysis_result.proposed_tasks),
                review_id=UUID(review.id) if review else None,
                review_status="in_review" if review else "ready",
                review_revision=review.revision if review else None,
            )
        )
    return ReviewQueueResponse(items=items)
