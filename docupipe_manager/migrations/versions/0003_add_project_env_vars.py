"""Add project_env_vars table for project-level environment variables.

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-24
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS docupipe_manager.project_env_vars (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id UUID NOT NULL REFERENCES docupipe_manager.projects(id) ON DELETE CASCADE,
            key VARCHAR(255) NOT NULL,
            value TEXT NOT NULL,
            is_secret BOOLEAN NOT NULL DEFAULT false,
            description VARCHAR(255),
            created_by UUID NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_project_env_vars_project_key UNIQUE (project_id, key)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_project_env_vars_project "
        "ON docupipe_manager.project_env_vars (project_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS docupipe_manager.project_env_vars CASCADE")
