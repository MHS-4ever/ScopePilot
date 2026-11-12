from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from scopepilot.database import Base


class Brief(Base):
    __tablename__ = "briefs"
    __table_args__ = (
        UniqueConstraint("client_id", "source_id", name="uq_briefs_client_source"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(200), nullable=False)
    client_id: Mapped[str] = mapped_column(String(200), nullable=False)
    project_name: Mapped[str] = mapped_column(String(300), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    original_scope: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="received")
    payload_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)


class BriefAnalysis(Base):
    __tablename__ = "brief_analyses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    brief_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("briefs.id"), nullable=False, unique=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="processing")
    claim_token: Mapped[str] = mapped_column(String(36), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    claimed_at: Mapped[str] = mapped_column(String(32), nullable=False)
    lease_expires_at: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False)
    completed_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    schema_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)


class BriefReview(Base):
    __tablename__ = "brief_reviews"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    brief_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("briefs.id"), nullable=False, unique=True
    )
    analysis_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("brief_analyses.id"), nullable=False, unique=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="in_review")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False)
    finalized_at: Mapped[str | None] = mapped_column(String(32), nullable=True)


class ReviewTask(Base):
    __tablename__ = "review_tasks"
    __table_args__ = (
        UniqueConstraint("review_id", "position", name="uq_review_tasks_position"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    review_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("brief_reviews.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    task_type: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    requirement_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    acceptance_criteria_json: Mapped[str] = mapped_column(Text, nullable=False)
    original_blocked_by_clarification: Mapped[bool] = mapped_column(
        Boolean, nullable=False
    )
    decision: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    clarification_resolved: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    clarification_resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False)


class TaskPublication(Base):
    __tablename__ = "task_publications"
    __table_args__ = (
        UniqueConstraint(
            "review_task_id",
            "target_repository",
            name="uq_task_publications_task_repository",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    review_task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("review_tasks.id", ondelete="CASCADE"), nullable=False
    )
    target_repository: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    github_issue_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    github_issue_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False)
