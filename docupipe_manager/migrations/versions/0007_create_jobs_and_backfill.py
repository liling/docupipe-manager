"""Create jobs table, backfill from pipeline_runs, add pipeline_runs.job_id.

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-27
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. 枚举类型
    op.execute("DO $$ BEGIN CREATE TYPE docupipe_manager.job_kind AS ENUM ('docupipe_run', 'credential_keepalive'); EXCEPTION WHEN duplicate_object THEN NULL; END $$")
    op.execute("DO $$ BEGIN CREATE TYPE docupipe_manager.job_status AS ENUM ('pending', 'running', 'succeeded', 'failed', 'cancelled'); EXCEPTION WHEN duplicate_object THEN NULL; END $$")
    op.execute("DO $$ BEGIN CREATE TYPE docupipe_manager.job_trigger_type AS ENUM ('manual', 'scheduled'); EXCEPTION WHEN duplicate_object THEN NULL; END $$")

    # 2. jobs 表
    op.execute(
        "CREATE TABLE IF NOT EXISTS docupipe_manager.jobs ("
        "id UUID PRIMARY KEY, "
        "kind docupipe_manager.job_kind NOT NULL, "
        "status docupipe_manager.job_status NOT NULL DEFAULT 'pending', "
        "pid INTEGER, "
        "exit_code INTEGER, "
        "started_at TIMESTAMPTZ, "
        "completed_at TIMESTAMPTZ, "
        "log_path VARCHAR(512), "
        "command_text VARCHAR(1024), "
        "error_message TEXT, "
        "trigger_type docupipe_manager.job_trigger_type NOT NULL, "
        "triggered_by UUID, "
        "credential_id UUID REFERENCES docupipe_manager.dws_credentials(id) ON DELETE SET NULL, "
        "created_at TIMESTAMPTZ NOT NULL DEFAULT now()"
        ")"
    )

    # 3. 回填：每个 pipeline_runs → 一行 jobs（共享 id）
    op.execute(
        "INSERT INTO docupipe_manager.jobs "
        "(id, kind, status, pid, exit_code, started_at, completed_at, log_path, "
        " command_text, error_message, trigger_type, triggered_by, created_at) "
         "SELECT pr.id, 'docupipe_run', pr.status::text::docupipe_manager.job_status, pr.pid, pr.exit_code, pr.started_at, pr.completed_at, "
         "       pr.log_path, pr.command_text, pr.error_message, pr.trigger_type::text::docupipe_manager.job_trigger_type, pr.triggered_by, pr.created_at "
         "FROM docupipe_manager.pipeline_runs pr"
    )

    # 4. pipeline_runs.job_id（共享 id）+ FK + 唯一
    op.execute("ALTER TABLE docupipe_manager.pipeline_runs ADD COLUMN IF NOT EXISTS job_id UUID")
    op.execute("UPDATE docupipe_manager.pipeline_runs SET job_id = id")
    op.execute(
        "ALTER TABLE docupipe_manager.pipeline_runs "
        "DROP CONSTRAINT IF EXISTS fk_pipeline_runs_job_id"
    )
    op.execute(
        "ALTER TABLE docupipe_manager.pipeline_runs "
        "ADD CONSTRAINT fk_pipeline_runs_job_id "
        "FOREIGN KEY (job_id) REFERENCES docupipe_manager.jobs(id) ON DELETE CASCADE"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_pipeline_runs_job_id "
        "ON docupipe_manager.pipeline_runs (job_id)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE docupipe_manager.pipeline_runs DROP CONSTRAINT IF EXISTS fk_pipeline_runs_job_id")
    op.execute("DROP INDEX IF EXISTS docupipe_manager.uq_pipeline_runs_job_id")
    op.execute("ALTER TABLE docupipe_manager.pipeline_runs DROP COLUMN IF EXISTS job_id")
    op.execute("DROP TABLE IF EXISTS docupipe_manager.jobs")
    op.execute("DROP TYPE IF EXISTS docupipe_manager.job_trigger_type")
    op.execute("DROP TYPE IF EXISTS docupipe_manager.job_status")
    op.execute("DROP TYPE IF EXISTS docupipe_manager.job_kind")
