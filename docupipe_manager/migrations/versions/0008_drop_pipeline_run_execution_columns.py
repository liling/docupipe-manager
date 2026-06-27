"""Drop execution columns moved from pipeline_runs to jobs.

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-27
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_DROPPED = ["status", "pid", "exit_code", "started_at", "completed_at",
            "log_path", "command_text", "error_message", "trigger_type",
            "triggered_by", "created_at"]


def upgrade() -> None:
    for col in _DROPPED:
        op.execute(f"ALTER TABLE docupipe_manager.pipeline_runs DROP COLUMN IF EXISTS {col}")
    op.execute("DROP TYPE IF EXISTS docupipe_manager.run_trigger_type")
    op.execute("DROP TYPE IF EXISTS docupipe_manager.run_status")


def downgrade() -> None:
    op.execute("DO $$ BEGIN CREATE TYPE docupipe_manager.run_status AS ENUM ('pending','running','succeeded','failed','cancelled'); EXCEPTION WHEN duplicate_object THEN NULL; END $$")
    op.execute("DO $$ BEGIN CREATE TYPE docupipe_manager.run_trigger_type AS ENUM ('manual','scheduled'); EXCEPTION WHEN duplicate_object THEN NULL; END $$")
    op.execute("ALTER TABLE docupipe_manager.pipeline_runs ADD COLUMN IF NOT EXISTS status docupipe_manager.run_status NOT NULL DEFAULT 'pending'")
    op.execute("ALTER TABLE docupipe_manager.pipeline_runs ADD COLUMN IF NOT EXISTS pid INTEGER")
    op.execute("ALTER TABLE docupipe_manager.pipeline_runs ADD COLUMN IF NOT EXISTS exit_code INTEGER")
    op.execute("ALTER TABLE docupipe_manager.pipeline_runs ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ")
    op.execute("ALTER TABLE docupipe_manager.pipeline_runs ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ")
    op.execute("ALTER TABLE docupipe_manager.pipeline_runs ADD COLUMN IF NOT EXISTS log_path VARCHAR(512)")
    op.execute("ALTER TABLE docupipe_manager.pipeline_runs ADD COLUMN IF NOT EXISTS command_text VARCHAR(1024)")
    op.execute("ALTER TABLE docupipe_manager.pipeline_runs ADD COLUMN IF NOT EXISTS error_message TEXT")
    op.execute("ALTER TABLE docupipe_manager.pipeline_runs ADD COLUMN IF NOT EXISTS trigger_type docupipe_manager.run_trigger_type NOT NULL DEFAULT 'manual'")
    op.execute("ALTER TABLE docupipe_manager.pipeline_runs ADD COLUMN IF NOT EXISTS triggered_by UUID")
    op.execute("ALTER TABLE docupipe_manager.pipeline_runs ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now()")
