import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from docupipe_manager.models.base import Base

_SCHEMA = "docupipe_manager"


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey(f"{_SCHEMA}.jobs.id", ondelete="CASCADE"), nullable=False, unique=True,
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey(f"{_SCHEMA}.tasks.id", ondelete="CASCADE"), nullable=False
    )
    pipeline_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
