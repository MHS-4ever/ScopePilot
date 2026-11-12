from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from scopepilot.analysis_schemas import Requirement, ScopeAssessment, TaskType

ReviewStatus = Literal["in_review", "finalized"]
TaskDecision = Literal["pending", "approved", "rejected"]
PublicationState = Literal[
    "publishing", "published", "failed", "requires_reconciliation"
]

Title = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=150)
]
Description = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)
]
Criterion = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)
]
RequirementId = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=2, max_length=10)
]
ReviewNote = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)
]


class ReviewBriefContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    client_id: str
    project_name: str
    message: str
    original_scope: str | None = None
    received_at: datetime


class ReviewAnalysisContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    model_id: str
    prompt_version: str
    schema_version: str
    completed_at: datetime
    requirements: list[Requirement]
    clarification_questions: list[str]
    scope_assessment: ScopeAssessment
    deadline_quote: str | None = None


class TaskPublicationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_repository: str
    status: PublicationState
    github_issue_number: int | None = None
    github_issue_url: str | None = None
    updated_at: datetime


class ReviewTaskResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    position: int
    type: TaskType
    title: str
    description: str
    requirement_ids: list[str]
    acceptance_criteria: list[str]
    original_blocked_by_clarification: bool
    decision: TaskDecision
    review_note: str | None = None
    clarification_resolved: bool
    clarification_resolution: str | None = None
    revision: int
    updated_at: datetime
    publications: list[TaskPublicationResponse] = Field(default_factory=list)


class ReviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    status: ReviewStatus
    revision: int
    created_at: datetime
    updated_at: datetime
    finalized_at: datetime | None = None
    brief: ReviewBriefContext
    analysis: ReviewAnalysisContext
    tasks: list[ReviewTaskResponse]


class ReviewTaskPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_revision: int = Field(ge=1)
    task_revision: int = Field(ge=1)
    title: Title | None = None
    description: Description | None = None
    requirement_ids: list[RequirementId] | None = Field(
        default=None, min_length=1, max_length=20
    )
    acceptance_criteria: list[Criterion] | None = Field(
        default=None, min_length=1, max_length=6
    )
    decision: TaskDecision | None = None
    review_note: ReviewNote | None = None
    clarification_resolved: bool | None = None
    clarification_resolution: ReviewNote | None = None

    @model_validator(mode="after")
    def validate_patch(self) -> "ReviewTaskPatch":
        mutable_fields = self.model_fields_set - {"review_revision", "task_revision"}
        if not mutable_fields:
            raise ValueError("At least one task field must be supplied")
        non_nullable_fields = {
            "title",
            "description",
            "requirement_ids",
            "acceptance_criteria",
            "decision",
            "clarification_resolved",
        }
        for field_name in self.model_fields_set & non_nullable_fields:
            if getattr(self, field_name) is None:
                raise ValueError(f"{field_name} cannot be null")
        if self.requirement_ids is not None and len(self.requirement_ids) != len(
            set(self.requirement_ids)
        ):
            raise ValueError("Duplicate requirement IDs are not allowed")
        if self.acceptance_criteria is not None and len(
            self.acceptance_criteria
        ) != len(set(self.acceptance_criteria)):
            raise ValueError("Duplicate acceptance criteria are not allowed")
        return self


class ReviewFinalizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_revision: int = Field(ge=1)


class ReviewQueueItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    brief_id: UUID
    client_id: str
    project_name: str
    received_at: datetime
    analysis_id: UUID
    analysis_completed_at: datetime
    proposed_task_count: int
    review_id: UUID | None = None
    review_status: Literal["ready", "in_review"]
    review_revision: int | None = None


class ReviewQueueResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ReviewQueueItem]
