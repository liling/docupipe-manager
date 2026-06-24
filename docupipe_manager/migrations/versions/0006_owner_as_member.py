"""Migrate owner_id into project_members as role='owner'.

- Create member_role enum
- Add role column to project_members
- Insert current owners as members with role='owner'
- Drop projects.owner_id
- Rebuild project_members PK: (user_id, project_id), drop id/added_by

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-24
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create enum
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE docupipe_manager.member_role AS ENUM ('owner', 'member');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """)

    # Add role column with default 'member'
    op.execute("""
        ALTER TABLE docupipe_manager.project_members
        ADD COLUMN role docupipe_manager.member_role NOT NULL DEFAULT 'member'
    """)

    # Relax added_by constraint so owner rows (no added_by) can be inserted
    op.execute("""
        ALTER TABLE docupipe_manager.project_members ALTER COLUMN added_by DROP NOT NULL
    """)

    # Upgrade existing member rows (if the owner was already a member) to role='owner',
    # then insert remaining owners as new member rows.
    op.execute("""
        UPDATE docupipe_manager.project_members pm SET role = 'owner'
        FROM docupipe_manager.projects p
        WHERE p.id = pm.project_id AND p.owner_id = pm.user_id
    """)
    op.execute("""
        INSERT INTO docupipe_manager.project_members (user_id, project_id, role, created_at)
        SELECT p.owner_id, p.id, 'owner', p.created_at
        FROM docupipe_manager.projects p
        WHERE p.owner_id IS NOT NULL
        AND NOT EXISTS (
            SELECT 1 FROM docupipe_manager.project_members pm
            WHERE pm.project_id = p.id AND pm.user_id = p.owner_id
        )
    """)

    # Drop owner_id from projects
    op.execute("ALTER TABLE docupipe_manager.projects DROP COLUMN owner_id")

    # Rebuild project_members PK — drop id surrogate, drop added_by, use (user_id, project_id)
    op.execute("""
        ALTER TABLE docupipe_manager.project_members DROP CONSTRAINT IF EXISTS uq_project_members_project_user
    """)
    op.execute("""
        ALTER TABLE docupipe_manager.project_members DROP CONSTRAINT IF EXISTS project_members_pkey
    """)
    op.execute("ALTER TABLE docupipe_manager.project_members DROP COLUMN id")
    op.execute("ALTER TABLE docupipe_manager.project_members DROP COLUMN added_by")
    op.execute("""
        ALTER TABLE docupipe_manager.project_members ADD PRIMARY KEY (user_id, project_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_project_members_project
        ON docupipe_manager.project_members (project_id)
    """)


def downgrade() -> None:
    # Restore id column and old PK
    op.execute("DROP INDEX IF EXISTS ix_project_members_project")
    op.execute("ALTER TABLE docupipe_manager.project_members DROP CONSTRAINT project_members_pkey")
    op.execute("""
        ALTER TABLE docupipe_manager.project_members
        ADD COLUMN id UUID DEFAULT gen_random_uuid() NOT NULL
    """)
    op.execute("ALTER TABLE docupipe_manager.project_members ADD PRIMARY KEY (id)")
    op.execute("""
        ALTER TABLE docupipe_manager.project_members
        ADD COLUMN added_by UUID NOT NULL DEFAULT '00000000-0000-0000-0000-000000000000'
    """)
    op.execute("""
        ALTER TABLE docupipe_manager.project_members
        ADD CONSTRAINT uq_project_members_project_user UNIQUE (project_id, user_id)
    """)

    # Remove owner members
    op.execute("""
        DELETE FROM docupipe_manager.project_members WHERE role = 'owner'
    """)

    # Restore owner_id on projects
    op.execute("""
        ALTER TABLE docupipe_manager.projects ADD COLUMN owner_id UUID
    """)
    op.execute("""
        UPDATE docupipe_manager.projects p SET owner_id = (
            SELECT pm.user_id FROM docupipe_manager.project_members pm
            WHERE pm.project_id = p.id AND pm.role = 'owner' LIMIT 1
        )
    """)
    op.execute("""
        ALTER TABLE docupipe_manager.projects ALTER COLUMN owner_id SET NOT NULL
    """)

    # Drop role column
    op.execute("ALTER TABLE docupipe_manager.project_members DROP COLUMN role")
    op.execute("DROP TYPE IF EXISTS docupipe_manager.member_role")
