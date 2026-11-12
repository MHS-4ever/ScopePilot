import hashlib
import hmac
import json
from collections.abc import Generator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, HTTPException, Response, Security, status
from fastapi.responses import FileResponse
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from scopepilot.analysis_schemas import (
    AnalysisClaimRequest,
    AnalysisClaimResponse,
    AnalysisCompleteRequest,
    AnalysisFailRequest,
    AnalysisFailResponse,
    AnalysisReadResponse,
)
from scopepilot.analysis_service import (
    claim_analysis,
    complete_analysis,
    fail_analysis,
    get_analysis,
)
from scopepilot.config import Settings, get_settings
from scopepilot.database import (
    Base,
    create_database_engine,
    create_session_factory,
    session_dependency,
)
from scopepilot.models import Brief
from scopepilot.github_schemas import ReviewPublishResponse
from scopepilot.github_service import publish_review
from scopepilot.schemas import BriefCreate, BriefResponse, HealthResponse
from scopepilot.review_schemas import (
    ReviewFinalizeRequest,
    ReviewQueueResponse,
    ReviewResponse,
    ReviewTaskPatch,
)
from scopepilot.review_service import (
    finalize_review,
    get_review,
    get_review_queue,
    initialize_review,
    update_review_task,
)

api_key_header = APIKeyHeader(
    name="X-API-Key", scheme_name="ScopePilotAPIKey", auto_error=False
)
review_key_header = APIKeyHeader(
    name="X-Review-Key", scheme_name="ScopePilotReviewKey", auto_error=False
)


def payload_fingerprint(payload: BriefCreate) -> str:
    canonical = json.dumps(
        payload.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def brief_response(brief: Brief) -> BriefResponse:
    return BriefResponse(
        id=UUID(brief.id),
        source_id=brief.source_id,
        client_id=brief.client_id,
        project_name=brief.project_name,
        message=brief.message,
        original_scope=brief.original_scope,
        received_at=datetime.fromisoformat(brief.received_at),
        status=brief.status,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    engine = create_database_engine(settings.database_url)
    session_factory = create_session_factory(engine)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):  # type: ignore[no-untyped-def]
        Base.metadata.create_all(engine)
        yield
        engine.dispose()

    app = FastAPI(title="ScopePilot API", version="0.1.0", lifespan=lifespan)
    review_ui_dir = Path(__file__).parent / "static" / "review"
    app.mount(
        "/review/assets",
        StaticFiles(directory=review_ui_dir),
        name="review-assets",
    )

    def require_api_key(provided_key: str | None = Security(api_key_header)) -> None:
        if provided_key is None or not hmac.compare_digest(provided_key, settings.api_key):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing API key",
            )

    def require_review_key(
        provided_key: str | None = Security(review_key_header),
    ) -> None:
        if (
            not settings.review_key
            or provided_key is None
            or not hmac.compare_digest(provided_key, settings.review_key)
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing review key",
            )

    def get_session() -> Generator[Session, None, None]:
        yield from session_dependency(session_factory)

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok")

    @app.get("/review", include_in_schema=False)
    def review_ui() -> FileResponse:
        return FileResponse(review_ui_dir / "index.html")

    @app.post(
        "/api/v1/briefs",
        response_model=BriefResponse,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(require_api_key)],
    )
    def ingest_brief(
        payload: BriefCreate,
        session: Session = Depends(get_session),
    ) -> BriefResponse:
        fingerprint = payload_fingerprint(payload)
        identifier = (Brief.client_id == payload.client_id) & (
            Brief.source_id == payload.source_id
        )
        brief = Brief(
            id=str(uuid4()),
            **payload.model_dump(),
            received_at=datetime.now(timezone.utc).isoformat(),
            status="received",
            payload_fingerprint=fingerprint,
        )
        session.add(brief)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            existing = session.scalar(select(Brief).where(identifier))
            if existing is None:
                raise
            if existing.payload_fingerprint != fingerprint:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="A different brief already exists for this client_id and source_id",
                )
            return brief_response(existing)

        return brief_response(brief)

    @app.get(
        "/api/v1/briefs/{brief_id}",
        response_model=BriefResponse,
        dependencies=[Depends(require_api_key)],
    )
    def get_brief(
        brief_id: UUID,
        session: Session = Depends(get_session),
    ) -> BriefResponse:
        brief = session.get(Brief, str(brief_id))
        if brief is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Brief not found")
        return brief_response(brief)

    @app.post(
        "/api/v1/briefs/{brief_id}/analysis/claim",
        response_model=AnalysisClaimResponse,
        dependencies=[Depends(require_api_key)],
    )
    def claim_analysis_endpoint(
        brief_id: UUID,
        response: Response,
        _payload: AnalysisClaimRequest | None = None,
        session: Session = Depends(get_session),
    ) -> AnalysisClaimResponse:
        status_code, claim_resp = claim_analysis(session, brief_id)
        response.status_code = status_code
        return claim_resp

    @app.post(
        "/api/v1/briefs/{brief_id}/analysis/complete",
        response_model=AnalysisReadResponse,
        status_code=status.HTTP_200_OK,
        dependencies=[Depends(require_api_key)],
    )
    def complete_analysis_endpoint(
        brief_id: UUID,
        payload: AnalysisCompleteRequest,
        session: Session = Depends(get_session),
    ) -> AnalysisReadResponse:
        return complete_analysis(session, brief_id, payload)

    @app.get(
        "/api/v1/briefs/{brief_id}/analysis",
        response_model=AnalysisReadResponse,
        dependencies=[Depends(require_api_key)],
    )
    def get_analysis_endpoint(
        brief_id: UUID,
        session: Session = Depends(get_session),
    ) -> AnalysisReadResponse:
        return get_analysis(session, brief_id)

    @app.post(
        "/api/v1/briefs/{brief_id}/analysis/fail",
        response_model=AnalysisFailResponse,
        status_code=status.HTTP_200_OK,
        dependencies=[Depends(require_api_key)],
    )
    def fail_analysis_endpoint(
        brief_id: UUID,
        payload: AnalysisFailRequest,
        session: Session = Depends(get_session),
    ) -> AnalysisFailResponse:
        return fail_analysis(session, brief_id, payload)

    @app.get(
        "/api/v1/review-queue",
        response_model=ReviewQueueResponse,
        dependencies=[Depends(require_review_key)],
    )
    def get_review_queue_endpoint(
        session: Session = Depends(get_session),
    ) -> ReviewQueueResponse:
        return get_review_queue(session)

    @app.post(
        "/api/v1/briefs/{brief_id}/review",
        response_model=ReviewResponse,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(require_review_key)],
    )
    def initialize_review_endpoint(
        brief_id: UUID,
        response: Response,
        session: Session = Depends(get_session),
    ) -> ReviewResponse:
        status_code, review = initialize_review(session, brief_id)
        response.status_code = status_code
        return review

    @app.get(
        "/api/v1/briefs/{brief_id}/review",
        response_model=ReviewResponse,
        dependencies=[Depends(require_review_key)],
    )
    def get_review_endpoint(
        brief_id: UUID,
        session: Session = Depends(get_session),
    ) -> ReviewResponse:
        return get_review(session, brief_id)

    @app.patch(
        "/api/v1/briefs/{brief_id}/review/tasks/{task_id}",
        response_model=ReviewResponse,
        dependencies=[Depends(require_review_key)],
    )
    def update_review_task_endpoint(
        brief_id: UUID,
        task_id: UUID,
        payload: ReviewTaskPatch,
        session: Session = Depends(get_session),
    ) -> ReviewResponse:
        return update_review_task(session, brief_id, task_id, payload)

    @app.post(
        "/api/v1/briefs/{brief_id}/review/finalize",
        response_model=ReviewResponse,
        dependencies=[Depends(require_review_key)],
    )
    def finalize_review_endpoint(
        brief_id: UUID,
        payload: ReviewFinalizeRequest,
        session: Session = Depends(get_session),
    ) -> ReviewResponse:
        return finalize_review(session, brief_id, payload)

    @app.post(
        "/api/v1/briefs/{brief_id}/review/publish",
        response_model=ReviewPublishResponse,
        dependencies=[Depends(require_review_key)],
    )
    def publish_review_endpoint(
        brief_id: UUID,
        session: Session = Depends(get_session),
    ) -> ReviewPublishResponse:
        return publish_review(session, brief_id, settings)

    app.state.engine = engine
    app.state.session_factory = session_factory
    return app


app = create_app()

