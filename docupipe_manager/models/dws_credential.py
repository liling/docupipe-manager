import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import BYTEA, UUID
from sqlalchemy.orm import Mapped, mapped_column

from docupipe_manager.models.base import Base
from docupipe_manager.models.task import CredentialType

_SCHEMA = "docupipe_manager"


class CredentialStatus(str, enum.Enum):
    active = "active"
    expired = "expired"
    revoked = "revoked"


class DwsCredential(Base):
    __tablename__ = "dws_credentials"
    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_dws_credentials_project_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey(f"{_SCHEMA}.projects.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    corp_id: Mapped[str] = mapped_column(String(64), nullable=False)
    auth_blob: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    refresh_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[CredentialStatus] = mapped_column(
        Enum(CredentialStatus, name="credential_status", schema=_SCHEMA, create_constraint=True),
        default=CredentialStatus.active,
        nullable=False,
    )
    credential_type: Mapped[CredentialType] = mapped_column(
        Enum(CredentialType, name="credential_type", schema=_SCHEMA, create_constraint=True),
        default=CredentialType.dws,
        nullable=False,
    )
    created_by: Mapped[uuid.UUID] = mapped_column(UUID, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
