from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationInfo,
    field_validator,
    model_serializer,
    model_validator,
)

NonEmptyStr500 = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)
]
NonEmptyStr300 = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)
]
NonEmptyStr150 = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=150)
]
NonEmptyStr1000 = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)
]

RequirementType = Literal["feature", "bug", "improvement", "other"]
TaskType = Literal["feature", "bug", "improvement", "other"]
ScopeStatus = Literal["within_scope", "potential_change", "unknown"]
ErrorCode = Literal[
    "model_failed",
    "invalid_model_output",
    "transport_failure",
    "internal_error",
]


class Requirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=10)]
    type: RequirementType
    description: NonEmptyStr500
    source_quote: NonEmptyStr500
    needs_clarification: bool


class ProposedTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: NonEmptyStr150
    description: NonEmptyStr1000
    type: TaskType
    requirement_ids: list[
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=10)]
    ] = Field(min_length=1)
    acceptance_criteria: list[NonEmptyStr300] = Field(min_length=1, max_length=6)
    blocked_by_clarification: bool


class ScopeAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ScopeStatus
    reason: NonEmptyStr500
    related_requirement_ids: list[
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=10)]
    ] = Field(default_factory=list)


class AnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirements: list[Requirement] = Field(default_factory=list, max_length=20)
    clarification_questions: list[NonEmptyStr300] = Field(
        default_factory=list, max_length=20
    )
    proposed_tasks: list[ProposedTask] = Field(default_factory=list, max_length=20)
    scope_assessment: ScopeAssessment
    deadline_quote: NonEmptyStr500 | None = None

    @field_validator("clarification_questions")
    @classmethod
    def validate_unique_clarification_questions(
        cls, questions: list[str]
    ) -> list[str]:
        if len(questions) != len(set(questions)):
            raise ValueError("Duplicate clarification questions are not allowed")
        return questions

    @model_validator(mode="after")
    def validate_cross_field_rules(self, info: ValidationInfo) -> "AnalysisResult":
        # Check requirement sequential IDs (R1, R2, ...)
        expected_ids = [f"R{i + 1}" for i in range(len(self.requirements))]
        actual_ids = [r.id for r in self.requirements]
        if actual_ids != expected_ids:
            raise ValueError(
                f"Requirement IDs must be sequential starting at R1. Expected {expected_ids}, got {actual_ids}"
            )

        valid_req_ids = set(actual_ids)

        # Empty requirements cannot produce proposed tasks
        if len(self.requirements) == 0 and len(self.proposed_tasks) > 0:
            raise ValueError("Proposed tasks cannot be created when requirements is empty")

        # Validate task references
        for idx, task in enumerate(self.proposed_tasks):
            for req_id in task.requirement_ids:
                if req_id not in valid_req_ids:
                    raise ValueError(
                        f"Task {idx} references non-existent requirement ID '{req_id}'"
                    )

        # Validate scope assessment related requirement IDs
        for req_id in self.scope_assessment.related_requirement_ids:
            if req_id not in valid_req_ids:
                raise ValueError(
                    f"Scope assessment references non-existent requirement ID '{req_id}'"
                )

        # If context is available, validate against message and original_scope
        if info.context is not None:
            message = info.context.get("message")
            original_scope = info.context.get("original_scope")

            if message is not None:
                for req in self.requirements:
                    if req.source_quote not in message:
                        raise ValueError(
                            f"Requirement {req.id} source quote is not an exact substring of the client message"
                        )
                if self.deadline_quote is not None:
                    if self.deadline_quote not in message:
                        raise ValueError(
                            "deadline_quote is not an exact substring of the client message"
                        )

            # Scope assessment check
            is_scope_empty = original_scope is None or not original_scope.strip()
            if is_scope_empty and self.scope_assessment.status != "unknown":
                raise ValueError(
                    "Scope assessment status must be 'unknown' when original_scope is not provided"
                )

            # Quality safeguard for suspiciously empty extraction
            if message is not None and len(self.requirements) == 0:
                cleaned_msg = message.strip()
                reason_lower = (self.scope_assessment.reason or "").lower()
                questions_lower = " ".join(self.clarification_questions).lower()
                claims_missing = any(
                    phrase in reason_lower or phrase in questions_lower
                    for phrase in MISSING_BRIEF_PHRASES
                )
                if claims_missing and cleaned_msg:
                    raise ValueError(
                        "Suspiciously empty analysis: Model claimed no brief was supplied, but client message is non-empty."
                    )

                words = {w.strip(".,!?:;\"'()[]{}").lower() for w in cleaned_msg.split()}
                matched_indicators = words.intersection(ACTIONABLE_REQUEST_INDICATORS)
                if matched_indicators and len(cleaned_msg) >= 15:
                    raise ValueError(
                        f"Suspiciously empty analysis: Client message contains actionable request indicators "
                        f"({', '.join(sorted(matched_indicators))}) but requirements list is empty."
                    )

        return self


ACTIONABLE_REQUEST_INDICATORS = frozenset({
    "add",
    "fix",
    "create",
    "implement",
    "update",
    "build",
    "rebuild",
    "remove",
    "change",
    "modify",
    "bug",
    "broken",
    "issue",
    "error",
    "preserve",
    "repair",
    "replace",
    "support",
    "enable",
})

MISSING_BRIEF_PHRASES = (
    "no client brief",
    "no brief supplied",
    "no brief was supplied",
    "no brief provided",
    "no message provided",
    "no text provided",
    "empty brief",
)
def validate_analysis_against_brief(
    result: AnalysisResult, message: str, original_scope: str | None
) -> None:
    """Explicit semantic validator against source brief content."""
    for req in result.requirements:
        if req.source_quote not in message:
            raise ValueError(
                f"Requirement {req.id} source quote '{req.source_quote}' is not an exact substring of the client message"
            )

    if result.deadline_quote is not None:
        if result.deadline_quote not in message:
            raise ValueError(
                f"deadline_quote '{result.deadline_quote}' is not an exact substring of the client message"
            )

    is_scope_empty = original_scope is None or not str(original_scope).strip()
    if is_scope_empty and result.scope_assessment.status != "unknown":
        raise ValueError(
            "Scope assessment status must be 'unknown' when original_scope is not provided"
        )

    # Lightweight Quality Safeguard for Suspiciously Empty Extractions
    if len(result.requirements) == 0:
        cleaned_msg = message.strip()

        # 1. Check if model falsely claimed no input/brief was supplied
        reason_lower = (result.scope_assessment.reason or "").lower()
        questions_lower = " ".join(result.clarification_questions).lower()
        claims_missing = any(
            phrase in reason_lower or phrase in questions_lower
            for phrase in MISSING_BRIEF_PHRASES
        )
        if claims_missing and cleaned_msg:
            raise ValueError(
                "Suspiciously empty analysis: Model claimed no brief was supplied, but client message is non-empty."
            )

        # 2. Check if client message contains unmistakable actionable indicators
        words = {w.strip(".,!?:;\"'()[]{}").lower() for w in cleaned_msg.split()}
        matched_indicators = words.intersection(ACTIONABLE_REQUEST_INDICATORS)
        if matched_indicators and len(cleaned_msg) >= 15:
            raise ValueError(
                f"Suspiciously empty analysis: Client message contains actionable request indicators "
                f"({', '.join(sorted(matched_indicators))}) but requirements list is empty."
            )


# API Schemas


class BriefSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    project_name: str
    message: str
    original_scope: str | None = None


class AnalysisClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AnalysisClaimResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["claimed", "already_completed"]
    analysis_id: UUID
    claim_token: UUID | None = None
    brief: BriefSummary
    prompt_version: str
    schema_version: str
    result: AnalysisResult | None = None

    @model_serializer(mode="wrap")
    def serialize(self, handler: Any) -> dict[str, Any]:
        data = handler(self)
        if self.outcome == "claimed":
            data.pop("result", None)
        elif self.outcome == "already_completed":
            data.pop("claim_token", None)
        return data


class AnalysisCompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_token: UUID
    model_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    prompt_version: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
    schema_version: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20)]
    result: AnalysisResult


class AnalysisReadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_id: UUID
    brief_id: UUID
    status: Literal["processing", "draft", "failed"]
    attempt_count: int
    model_id: str | None = None
    prompt_version: str | None = None
    schema_version: str | None = None
    completed_at: datetime | None = None
    result: AnalysisResult | None = None
    last_error_code: str | None = None


class AnalysisFailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_token: UUID
    error_code: ErrorCode


class AnalysisFailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_id: UUID
    brief_id: UUID
    status: Literal["failed"]
    last_error_code: str
