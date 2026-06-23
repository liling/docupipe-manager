"""Add pipeline_runs.command_text for run console command display.

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-23
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE docupipe_manager.pipeline_runs "
        "ADD COLUMN IF NOT EXISTS command_text VARCHAR(1024)"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE docupipe_manager.pipeline_runs "
        "DROP COLUMN IF EXISTS command_text"
    )
