"""Replace unique constraint with partial index allowing same name after revoke.

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-24
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE docupipe_manager.dws_credentials "
        "DROP CONSTRAINT uq_dws_credentials_project_name"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_dws_credentials_project_active_name "
        "ON docupipe_manager.dws_credentials (project_id, name) "
        "WHERE status != 'revoked'"
    )


def downgrade() -> None:
    op.execute(
        "DROP INDEX IF EXISTS uq_dws_credentials_project_active_name"
    )
    op.execute(
        "ALTER TABLE docupipe_manager.dws_credentials "
        "ADD CONSTRAINT uq_dws_credentials_project_name "
        "UNIQUE (project_id, name)"
    )
