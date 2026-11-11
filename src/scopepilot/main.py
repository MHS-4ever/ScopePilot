import hashlib
import hmac
import json
from collections.abc import Generator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, HTTPException, Security, status
from fastapi.security import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from scopepilot.config import Settings, get_settings
from scopepilot.database import (
    Base,
    create_database_engine,
    create_session_factory,
    session_dependency,
)
from scopepilot.models import Brief
from scopepilot.schemas import BriefCreate, BriefResponse, HealthResponse

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


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

    def require_api_key(provided_key: str | None = Security(api_key_header)) -> None:
        if provided_key is None or not hmac.compare_digest(provided_key, settings.api_key):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing API key",
            )

    def get_session() -> Generator[Session, None, None]:
        yield from session_dependency(session_factory)

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok")

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

    app.state.engine = engine
    app.state.session_factory = session_factory
    return app


app = create_app()

