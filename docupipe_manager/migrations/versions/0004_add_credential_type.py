"""Add credential_type column to dws_credentials.

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-24
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE docupipe_manager.dws_credentials "
        "ADD COLUMN IF NOT EXISTS credential_type docupipe_manager.credential_type "
        "NOT NULL DEFAULT 'dws'"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE docupipe_manager.dws_credentials "
        "DROP COLUMN IF EXISTS credential_type"
    )
