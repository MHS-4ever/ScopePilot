from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


PublicationResultStatus = Literal[
    "published", "already_published", "failed", "requires_reconciliation"
]


class PublicationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: UUID
    task_title: str
    status: PublicationResultStatus
    issue_number: int | None = None
    issue_url: str | None = None
    detail: str | None = None


class ReviewPublishResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_id: UUID
    target_repository: str
    results: list[PublicationResult]
