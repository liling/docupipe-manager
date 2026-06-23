import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from docupipe_manager.models.base import Base

_SCHEMA = "docupipe_manager"


class TaskStatus(str, enum.Enum):
    active = "active"
    paused = "paused"
    archived = "archived"


class CredentialType(str, enum.Enum):
    dws = "dws"


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey(f"{_SCHEMA}.projects.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    config_yaml: Mapped[str] = mapped_column(Text, nullable=False)
    credential_id: Mapped[uuid.UUID | None] = mapped_column(UUID, nullable=True)
    credential_type: Mapped[CredentialType | None] = mapped_column(
        Enum(CredentialType, name="credential_type", schema=_SCHEMA, create_constraint=True),
        nullable=True,
    )
    schedule_cron: Mapped[str | None] = mapped_column(String(64), nullable=True)
    schedule_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    schedule_pipeline: Mapped[str | None] = mapped_column(String(255), nullable=True)
    schedule_mode: Mapped[str] = mapped_column(String(16), default="incremental", nullable=False)
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus, name="task_status", schema=_SCHEMA, create_constraint=True),
        default=TaskStatus.active,
        nullable=False,
    )
    created_by: Mapped[uuid.UUID] = mapped_column(UUID, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
