from datetime import datetime, timedelta, timezone
import json
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from scopepilot.analysis_schemas import (
    AnalysisClaimResponse,
    AnalysisCompleteRequest,
    AnalysisFailRequest,
    AnalysisFailResponse,
    AnalysisReadResponse,
    AnalysisResult,
    BriefSummary,
    validate_analysis_against_brief,
)
from scopepilot.models import Brief, BriefAnalysis

LEASE_DURATION = timedelta(minutes=10)
PROMPT_VERSION = "brief-extraction-v1"
SCHEMA_VERSION = "1"


def claim_analysis(
    session: Session, brief_id: UUID
) -> tuple[int, AnalysisClaimResponse]:
    brief = session.get(Brief, str(brief_id))
    if brief is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Brief not found"
        )

    brief_summary = BriefSummary(
        id=UUID(brief.id),
        project_name=brief.project_name,
        message=brief.message,
        original_scope=brief.original_scope,
    )

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    lease_expires_iso = (now + LEASE_DURATION).isoformat()

    analysis = session.scalar(
        select(BriefAnalysis).where(BriefAnalysis.brief_id == str(brief_id))
    )

    if analysis is None:
        # First claim attempt
        claim_token = uuid4()
        new_analysis = BriefAnalysis(
            id=str(uuid4()),
            brief_id=str(brief_id),
            status="processing",
            claim_token=str(claim_token),
            attempt_count=1,
            claimed_at=now_iso,
            lease_expires_at=lease_expires_iso,
            created_at=now_iso,
            updated_at=now_iso,
            last_error_code=None,
        )
        session.add(new_analysis)
        try:
            session.commit()
            return status.HTTP_201_CREATED, AnalysisClaimResponse(
                outcome="claimed",
                analysis_id=UUID(new_analysis.id),
                claim_token=claim_token,
                brief=brief_summary,
                prompt_version=PROMPT_VERSION,
                schema_version=SCHEMA_VERSION,
            )
        except IntegrityError:
            session.rollback()
            analysis = session.scalar(
                select(BriefAnalysis).where(BriefAnalysis.brief_id == str(brief_id))
            )
            if analysis is None:
                raise

    # Handle existing analysis record
    if analysis.status == "draft":
        saved_result = None
        if analysis.result_json:
            saved_result = AnalysisResult.model_validate_json(analysis.result_json)
        return status.HTTP_200_OK, AnalysisClaimResponse(
            outcome="already_completed",
            analysis_id=UUID(analysis.id),
            claim_token=None,
            brief=brief_summary,
            prompt_version=analysis.prompt_version or PROMPT_VERSION,
            schema_version=analysis.schema_version or SCHEMA_VERSION,
            result=saved_result,
        )

    if analysis.status == "failed":
        new_token = uuid4()
        stmt = (
            update(BriefAnalysis)
            .where(
                BriefAnalysis.id == analysis.id,
                BriefAnalysis.status == "failed",
            )
            .values(
                status="processing",
                claim_token=str(new_token),
                attempt_count=BriefAnalysis.attempt_count + 1,
                claimed_at=now_iso,
                lease_expires_at=lease_expires_iso,
                updated_at=now_iso,
                last_error_code=None,
            )
        )
        res = session.execute(stmt)
        session.commit()
        if res.rowcount == 0:
            session.rollback()
            # Concurrently modified, reload and check
            analysis = session.get(BriefAnalysis, analysis.id)
            if analysis and analysis.status == "draft":
                saved_result = (
                    AnalysisResult.model_validate_json(analysis.result_json)
                    if analysis.result_json
                    else None
                )
                return status.HTTP_200_OK, AnalysisClaimResponse(
                    outcome="already_completed",
                    analysis_id=UUID(analysis.id),
                    claim_token=None,
                    brief=brief_summary,
                    prompt_version=analysis.prompt_version or PROMPT_VERSION,
                    schema_version=analysis.schema_version or SCHEMA_VERSION,
                    result=saved_result,
                )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="analysis_in_progress",
            )

        return status.HTTP_201_CREATED, AnalysisClaimResponse(
            outcome="claimed",
            analysis_id=UUID(analysis.id),
            claim_token=new_token,
            brief=brief_summary,
            prompt_version=PROMPT_VERSION,
            schema_version=SCHEMA_VERSION,
        )

    # Status is processing
    lease_expires = datetime.fromisoformat(analysis.lease_expires_at)
    if now < lease_expires:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="analysis_in_progress",
        )

    # Expired lease, reclaim
    new_token = uuid4()
    stmt = (
        update(BriefAnalysis)
        .where(
            BriefAnalysis.id == analysis.id,
            BriefAnalysis.status == "processing",
            BriefAnalysis.claim_token == analysis.claim_token,
        )
        .values(
            status="processing",
            claim_token=str(new_token),
            attempt_count=BriefAnalysis.attempt_count + 1,
            claimed_at=now_iso,
            lease_expires_at=lease_expires_iso,
            updated_at=now_iso,
        )
    )
    res = session.execute(stmt)
    session.commit()
    if res.rowcount == 0:
        session.rollback()
        analysis = session.get(BriefAnalysis, analysis.id)
        if analysis and analysis.status == "draft":
            saved_result = (
                AnalysisResult.model_validate_json(analysis.result_json)
                if analysis.result_json
                else None
            )
            return status.HTTP_200_OK, AnalysisClaimResponse(
                outcome="already_completed",
                analysis_id=UUID(analysis.id),
                claim_token=None,
                brief=brief_summary,
                prompt_version=analysis.prompt_version or PROMPT_VERSION,
                schema_version=analysis.schema_version or SCHEMA_VERSION,
                result=saved_result,
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="analysis_in_progress",
        )

    return status.HTTP_201_CREATED, AnalysisClaimResponse(
        outcome="claimed",
        analysis_id=UUID(analysis.id),
        claim_token=new_token,
        brief=brief_summary,
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
    )


def complete_analysis(
    session: Session, brief_id: UUID, payload: AnalysisCompleteRequest
) -> AnalysisReadResponse:
    brief = session.get(Brief, str(brief_id))
    if brief is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Brief not found"
        )

    analysis = session.scalar(
        select(BriefAnalysis).where(BriefAnalysis.brief_id == str(brief_id))
    )
    if analysis is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Analysis not found"
        )

    if analysis.status == "draft":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Analysis has already been completed as a draft",
        )

    if analysis.status != "processing":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Analysis is not in processing state (status: {analysis.status})",
        )

    if analysis.claim_token != str(payload.claim_token):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Invalid or stale claim token",
        )

    now = datetime.now(timezone.utc)
    lease_expires = datetime.fromisoformat(analysis.lease_expires_at)
    if now > lease_expires:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Processing lease has expired",
        )

    # Validate against source brief
    try:
        validate_analysis_against_brief(
            payload.result,
            message=brief.message,
            original_scope=brief.original_scope,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(e),
        )

    now_iso = now.isoformat()
    canonical_result_json = json.dumps(
        payload.result.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
    )

    stmt = (
        update(BriefAnalysis)
        .where(
            BriefAnalysis.id == analysis.id,
            BriefAnalysis.status == "processing",
            BriefAnalysis.claim_token == str(payload.claim_token),
        )
        .values(
            status="draft",
            completed_at=now_iso,
            model_id=payload.model_id,
            prompt_version=payload.prompt_version,
            schema_version=payload.schema_version,
            result_json=canonical_result_json,
            updated_at=now_iso,
            last_error_code=None,
        )
    )
    res = session.execute(stmt)
    session.commit()

    if res.rowcount == 0:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Claim token is stale or analysis state was concurrently changed",
        )

    return AnalysisReadResponse(
        analysis_id=UUID(analysis.id),
        brief_id=UUID(brief.id),
        status="draft",
        attempt_count=analysis.attempt_count,
        model_id=payload.model_id,
        prompt_version=payload.prompt_version,
        schema_version=payload.schema_version,
        completed_at=now,
        result=payload.result,
        last_error_code=None,
    )


def get_analysis(session: Session, brief_id: UUID) -> AnalysisReadResponse:
    brief = session.get(Brief, str(brief_id))
    if brief is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Brief not found"
        )

    analysis = session.scalar(
        select(BriefAnalysis).where(BriefAnalysis.brief_id == str(brief_id))
    )
    if analysis is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Analysis not found"
        )

    result_data = None
    if analysis.status == "draft" and analysis.result_json:
        result_data = AnalysisResult.model_validate_json(analysis.result_json)

    completed_at_dt = None
    if analysis.completed_at:
        completed_at_dt = datetime.fromisoformat(analysis.completed_at)

    return AnalysisReadResponse(
        analysis_id=UUID(analysis.id),
        brief_id=UUID(brief.id),
        status=analysis.status,  # type: ignore[arg-type]
        attempt_count=analysis.attempt_count,
        model_id=analysis.model_id,
        prompt_version=analysis.prompt_version,
        schema_version=analysis.schema_version,
        completed_at=completed_at_dt,
        result=result_data,
        last_error_code=analysis.last_error_code if analysis.status == "failed" else None,
    )


def fail_analysis(
    session: Session, brief_id: UUID, payload: AnalysisFailRequest
) -> AnalysisFailResponse:
    brief = session.get(Brief, str(brief_id))
    if brief is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Brief not found"
        )

    analysis = session.scalar(
        select(BriefAnalysis).where(BriefAnalysis.brief_id == str(brief_id))
    )
    if analysis is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Analysis not found"
        )

    if analysis.status == "draft":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot mark a completed draft as failed",
        )

    if analysis.claim_token != str(payload.claim_token):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Invalid or stale claim token",
        )

    now_iso = datetime.now(timezone.utc).isoformat()
    stmt = (
        update(BriefAnalysis)
        .where(
            BriefAnalysis.id == analysis.id,
            BriefAnalysis.status == "processing",
            BriefAnalysis.claim_token == str(payload.claim_token),
        )
        .values(
            status="failed",
            last_error_code=payload.error_code,
            updated_at=now_iso,
        )
    )
    res = session.execute(stmt)
    session.commit()

    if res.rowcount == 0:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Analysis state was concurrently modified or claim is no longer active",
        )

    return AnalysisFailResponse(
        analysis_id=UUID(analysis.id),
        brief_id=UUID(brief.id),
        status="failed",
        last_error_code=payload.error_code,
    )
