"""Create initial schema: ENUMs + 5 tables (raw SQL for idempotency).

Revision ID: 0001
Revises:
Create Date: 2026-06-23
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _create_type_if_not_exists(name: str, values: list[str]) -> None:
    vals = ", ".join(f"'{v}'" for v in values)
    op.execute(f"""
        DO $$ BEGIN
            CREATE TYPE docupipe_manager.{name} AS ENUM ({vals});
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS docupipe_manager")

    _create_type_if_not_exists("credential_status", ["active", "expired", "revoked"])
    _create_type_if_not_exists("project_status", ["active", "paused", "archived"])
    _create_type_if_not_exists("task_status", ["active", "paused", "archived"])
    _create_type_if_not_exists("credential_type", ["dws"])
    _create_type_if_not_exists("run_trigger_type", ["manual", "scheduled"])
    _create_type_if_not_exists("run_status", ["pending", "running", "succeeded", "failed", "cancelled"])

    op.execute("""
        CREATE TABLE IF NOT EXISTS docupipe_manager.projects (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name VARCHAR(255) UNIQUE NOT NULL,
            slug VARCHAR(64) UNIQUE NOT NULL,
            description TEXT,
            owner_id UUID NOT NULL,
            status docupipe_manager.project_status NOT NULL DEFAULT 'active',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS docupipe_manager.project_members (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id UUID NOT NULL REFERENCES docupipe_manager.projects(id) ON DELETE CASCADE,
            user_id UUID NOT NULL,
            added_by UUID NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_project_members_project_user UNIQUE (project_id, user_id)
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS docupipe_manager.dws_credentials (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id UUID NOT NULL REFERENCES docupipe_manager.projects(id) ON DELETE CASCADE,
            name VARCHAR(255) NOT NULL,
            corp_id VARCHAR(64) NOT NULL,
            auth_blob BYTEA NOT NULL,
            token_expires_at TIMESTAMPTZ,
            refresh_token_expires_at TIMESTAMPTZ,
            last_refreshed_at TIMESTAMPTZ,
            status docupipe_manager.credential_status NOT NULL DEFAULT 'active',
            created_by UUID NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_dws_credentials_project_name UNIQUE (project_id, name)
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS docupipe_manager.tasks (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id UUID NOT NULL REFERENCES docupipe_manager.projects(id) ON DELETE CASCADE,
            name VARCHAR(255) NOT NULL,
            slug VARCHAR(64) NOT NULL,
            description TEXT,
            config_yaml TEXT NOT NULL,
            credential_id UUID,
            credential_type docupipe_manager.credential_type,
            schedule_cron VARCHAR(64),
            schedule_enabled BOOLEAN NOT NULL DEFAULT true,
            schedule_pipeline VARCHAR(255),
            schedule_mode VARCHAR(16) NOT NULL DEFAULT 'incremental',
            status docupipe_manager.task_status NOT NULL DEFAULT 'active',
            created_by UUID NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS docupipe_manager.pipeline_runs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            task_id UUID NOT NULL REFERENCES docupipe_manager.tasks(id) ON DELETE CASCADE,
            trigger_type docupipe_manager.run_trigger_type NOT NULL,
            triggered_by UUID,
            pipeline_name VARCHAR(255),
            mode VARCHAR(16) NOT NULL,
            status docupipe_manager.run_status NOT NULL DEFAULT 'pending',
            pid INTEGER,
            exit_code INTEGER,
            started_at TIMESTAMPTZ,
            completed_at TIMESTAMPTZ,
            log_path VARCHAR(512),
            error_message TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("CREATE INDEX IF NOT EXISTS ix_project_members_user ON docupipe_manager.project_members (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_tasks_project_status ON docupipe_manager.tasks (project_id, status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_dws_credentials_project_status ON docupipe_manager.dws_credentials (project_id, status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_pipeline_runs_task_created ON docupipe_manager.pipeline_runs (task_id, created_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_pipeline_runs_status ON docupipe_manager.pipeline_runs (status)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS docupipe_manager.pipeline_runs CASCADE")
    op.execute("DROP TABLE IF EXISTS docupipe_manager.tasks CASCADE")
    op.execute("DROP TABLE IF EXISTS docupipe_manager.dws_credentials CASCADE")
    op.execute("DROP TABLE IF EXISTS docupipe_manager.project_members CASCADE")
    op.execute("DROP TABLE IF EXISTS docupipe_manager.projects CASCADE")
    for t in ["run_status", "run_trigger_type", "credential_type", "task_status", "project_status", "credential_status"]:
        op.execute(f"DROP TYPE IF EXISTS docupipe_manager.{t}")
    op.execute("DROP SCHEMA IF EXISTS docupipe_manager CASCADE")
