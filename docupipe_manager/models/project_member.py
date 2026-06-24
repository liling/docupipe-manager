import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, PrimaryKeyConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from docupipe_manager.models.base import Base

_SCHEMA = "docupipe_manager"


class MemberRole(str, enum.Enum):
    OWNER = "owner"
    MEMBER = "member"


class ProjectMember(Base):
    __tablename__ = "project_members"
    __table_args__ = (
        PrimaryKeyConstraint("user_id", "project_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(UUID, nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey(f"{_SCHEMA}.projects.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[MemberRole] = mapped_column(
        Enum(MemberRole, name="member_role", schema=_SCHEMA,
             values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
        default=MemberRole.MEMBER,
        server_default="member",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
