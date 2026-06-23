import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from docupipe_manager.models.base import Base

_SCHEMA = "docupipe_manager"


class RunTriggerType(str, enum.Enum):
    manual = "manual"
    scheduled = "scheduled"


class RunStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey(f"{_SCHEMA}.tasks.id", ondelete="CASCADE"), nullable=False
    )
    trigger_type: Mapped[RunTriggerType] = mapped_column(
        Enum(RunTriggerType, name="run_trigger_type", schema=_SCHEMA, create_constraint=True),
        nullable=False,
    )
    triggered_by: Mapped[uuid.UUID | None] = mapped_column(UUID, nullable=True)
    pipeline_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus, name="run_status", schema=_SCHEMA, create_constraint=True),
        default=RunStatus.pending,
        nullable=False,
    )
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    log_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
