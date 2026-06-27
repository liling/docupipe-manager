import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from docupipe_manager.models.base import Base

_SCHEMA = "docupipe_manager"


class JobKind(str, enum.Enum):
    docupipe_run = "docupipe_run"
    credential_keepalive = "credential_keepalive"


class JobStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class JobTriggerType(str, enum.Enum):
    manual = "manual"
    scheduled = "scheduled"


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    kind: Mapped[JobKind] = mapped_column(
        Enum(JobKind, name="job_kind", schema=_SCHEMA, create_constraint=True),
        nullable=False,
    )
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status", schema=_SCHEMA, create_constraint=True),
        default=JobStatus.pending, nullable=False,
    )
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    log_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    command_text: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    trigger_type: Mapped[JobTriggerType] = mapped_column(
        Enum(JobTriggerType, name="job_trigger_type", schema=_SCHEMA, create_constraint=True),
        nullable=False,
    )
    triggered_by: Mapped[uuid.UUID | None] = mapped_column(UUID, nullable=True)
    credential_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID, ForeignKey(f"{_SCHEMA}.dws_credentials.id", ondelete="SET NULL"), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
