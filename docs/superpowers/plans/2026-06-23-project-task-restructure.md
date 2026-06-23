# 项目与任务重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把扁平的 `DocupipeProject`（既是容器又是执行单元）重构为"项目（容器）→ 任务（执行单元）"两层结构，引入项目级 Owner/Member 权限，凭证改为项目级私有池。

**Architecture:** 数据模型分两层（`projects` 纯容器 / `tasks` 承接执行字段），凭证按类型分表（保留 `dws_credentials` 加 `project_id`，未来按需加表），任务多态引用凭证（`credential_id` + `credential_type`）。权限三层：`require_admin`（系统级）/ `require_project_access`（admin 或 Owner 或 Member）/ `require_project_owner`（admin 或 Owner）。API 拆分为项目、成员、凭证、任务、运行五组路由。UI 改为项目列表 + 项目详情（任务/凭证/成员/运行 Tab）。

**Tech Stack:** FastAPI + SQLAlchemy[asyncio] + Alembic(raw SQL 迁移) + APScheduler + Jinja2 模板 + pytest(pytest-asyncio, mock 模式)

## Global Constraints

- Python ≥ 3.12，依赖见 `pyproject.toml`，禁止新增依赖。
- 所有表位于 schema `docupipe_manager`，时间戳带时区（`TIMESTAMPTZ`）。
- 迁移文件用 raw SQL（沿用 `0001` 的 `_create_type_if_not_exists` 模式），不引入 autogenerate。
- 测试用现有 mock 模式：`mock_session` / `mock_platform_client` / `override_get_current_user` / `async_client`（见 `tests/conftest.py`）。`pytest -m 'not integration'` 默认跳过真实 docupipe 运行。
- 命名：项目/任务 slug 校验 `^[a-z0-9-]+$`；调度键格式 `task-{task_id}`。
- 凭证 `auth_blob` 用 SM4 加密（复用 `docupipe_manager.crypto.encrypt_sm4/decrypt_sm4`），`encryption_key` 32 hex chars。
- 本次只交付 DWS 凭证，`credential_type` 枚举与多态引用字段建好但仅 `dws` 取值。
- 所有"删除"为软删除（`status=archived/revoked`），成员移除为物理删除。

## File Structure

**模型层**（`docupipe_manager/models/`）
- Create: `project.py` — `Project`, `ProjectStatus`
- Create: `project_member.py` — `ProjectMember`
- Create: `task.py` — `Task`, `TaskStatus`, `CredentialType`
- Modify: `dws_credential.py` — 加 `project_id`，`name` 改 `UNIQUE(project_id, name)`
- Modify: `pipeline_run.py` — `project_id` → `task_id`
- Delete: `docupipe_project.py`

**迁移**（`docupipe_manager/migrations/versions/`）
- Modify: `0001_initial_schema.py` — 重写（全新开始，drop 旧表，建 5 张新表 + 枚举）

**权限层**（`docupipe_manager/auth/`）
- Create: `project_access.py` — `require_project_access`, `require_project_owner`

**服务层**（`docupipe_manager/services/`）
- Modify: `runner_service.py` — `start_run(task_id=...)`，按 `credential_type` 分派
- Modify: `scheduler_service.py` — `schedule_task`/`unschedule_task`，键 `task-{id}`
- Modify: `credential_service.py` — 所有方法加 `project_id`

**API 层**（`docupipe_manager/api/`）
- Rewrite: `projects.py` — admin 创建 + 项目 CRUD（access/owner 守卫）
- Create: `members.py` — 成员增删查（owner 守卫）
- Rewrite: `credentials.py` — 项目级 device flow（access 守卫）
- Create: `tasks.py` — 任务 CRUD + 触发（access 守卫）
- Modify: `runs.py` — `task_id` + 按可见项目过滤
- Modify: `pages.py` — UI 路由按身份过滤
- Modify: `stats.py` — 适配新模型

**UI 层**（`docupipe_manager/templates/docupipe/`）
- Rewrite: `projects.html` — 列表按身份过滤 + admin 创建按钮
- Create: `project_detail.html` — Tab 式详情（任务/凭证/成员/运行）
- Create: `task_form.html` — 任务表单（yaml/凭证/cron）
- Rewrite: `credentials.html` — 项目内凭证 device flow
- Modify: `runs.html` — 按可见项目过滤
- Delete: `project_form.html`

**装配**（`docupipe_manager/`）
- Modify: `main.py` — 导航菜单（普通用户可见项目/运行）+ 路由注册

**测试**（`tests/`）
- Rewrite: `tests/services/test_credential_service.py`, `test_runner_service.py`, `test_scheduler_service.py`
- Create: `tests/unit/test_project_access.py`
- Create: `tests/api/test_projects.py`, `test_members.py`, `test_tasks.py`, `test_credentials.py`, `test_runs.py`

---

## Task 1: 数据模型与迁移

**Files:**
- Create: `docupipe_manager/models/project.py`
- Create: `docupipe_manager/models/project_member.py`
- Create: `docupipe_manager/models/task.py`
- Modify: `docupipe_manager/models/dws_credential.py`
- Modify: `docupipe_manager/models/pipeline_run.py`
- Delete: `docupipe_manager/models/docupipe_project.py`
- Modify: `docupipe_manager/migrations/versions/0001_initial_schema.py`
- Test: `tests/unit/test_models.py`

**Interfaces:**
- Produces: `Project`, `ProjectStatus`, `ProjectMember`, `Task`, `TaskStatus`, `CredentialType` 模型类；`DwsCredential.project_id` 列；`PipelineRun.task_id` 列。后续所有任务的 ORM 查询依赖这些类与列名。

- [ ] **Step 1: 写 `project.py`**

```python
# docupipe_manager/models/project.py
import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from docupipe_manager.models.base import Base

_SCHEMA = "docupipe_manager"


class ProjectStatus(str, enum.Enum):
    active = "active"
    paused = "paused"
    archived = "archived"


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID, nullable=False)
    status: Mapped[ProjectStatus] = mapped_column(
        Enum(ProjectStatus, name="project_status", schema=_SCHEMA, create_constraint=True),
        default=ProjectStatus.active,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```

- [ ] **Step 2: 写 `project_member.py`**

```python
# docupipe_manager/models/project_member.py
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from docupipe_manager.models.base import Base

_SCHEMA = "docupipe_manager"


class ProjectMember(Base):
    __tablename__ = "project_members"
    __table_args__ = (
        UniqueConstraint("project_id", "user_id", name="uq_project_members_project_user"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey(f"{_SCHEMA}.projects.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID, nullable=False)
    added_by: Mapped[uuid.UUID] = mapped_column(UUID, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

- [ ] **Step 3: 写 `task.py`**

```python
# docupipe_manager/models/task.py
import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from docupipe_manager.models.base import Base

_SCHEMA = "docupipe_manager"


class TaskStatus(str, enum.Enum):
    active = "active"
    paused = "paused"
    archived = "archived"


class CredentialType(str, enum.Enum):
    dws = "dws"


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey(f"{_SCHEMA}.projects.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    config_yaml: Mapped[str] = mapped_column(Text, nullable=False)
    credential_id: Mapped[uuid.UUID | None] = mapped_column(UUID, nullable=True)
    credential_type: Mapped[CredentialType | None] = mapped_column(
        Enum(CredentialType, name="credential_type", schema=_SCHEMA, create_constraint=True),
        nullable=True,
    )
    schedule_cron: Mapped[str | None] = mapped_column(String(64), nullable=True)
    schedule_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    schedule_pipeline: Mapped[str | None] = mapped_column(String(255), nullable=True)
    schedule_mode: Mapped[str] = mapped_column(String(16), default="incremental", nullable=False)
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus, name="task_status", schema=_SCHEMA, create_constraint=True),
        default=TaskStatus.active,
        nullable=False,
    )
    created_by: Mapped[uuid.UUID] = mapped_column(UUID, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```

- [ ] **Step 4: 改 `dws_credential.py`（加 `project_id`，`name` 改联合唯一）**

替换整个文件：

```python
# docupipe_manager/models/dws_credential.py
import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import BYTEA, UUID
from sqlalchemy.orm import Mapped, mapped_column

from docupipe_manager.models.base import Base

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
    created_by: Mapped[uuid.UUID] = mapped_column(UUID, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```

- [ ] **Step 5: 改 `pipeline_run.py`（`project_id` → `task_id`）**

把 `project_id` 那一行改为：

```python
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey(f"{_SCHEMA}.tasks.id", ondelete="CASCADE"), nullable=False
    )
```

（其余字段不变。`import` 里加 `ForeignKey`。）

- [ ] **Step 6: 删除 `docupipe_project.py`**

```bash
rm docupipe_manager/models/docupipe_project.py
```

- [ ] **Step 7: 重写迁移 `0001_initial_schema.py`**

```python
# docupipe_manager/migrations/versions/0001_initial_schema.py
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
```

- [ ] **Step 8: 写模型 smoke 测试**

```python
# tests/unit/test_models.py
"""模型类可 import 且列映射正确（不连数据库）。"""
from docupipe_manager.models.project import Project, ProjectStatus
from docupipe_manager.models.project_member import ProjectMember
from docupipe_manager.models.task import Task, TaskStatus, CredentialType
from docupipe_manager.models.dws_credential import DwsCredential, CredentialStatus
from docupipe_manager.models.pipeline_run import PipelineRun


def test_models_importable():
    assert Project.__tablename__ == "projects"
    assert ProjectMember.__tablename__ == "project_members"
    assert Task.__tablename__ == "tasks"
    assert DwsCredential.__tablename__ == "dws_credentials"
    assert PipelineRun.__tablename__ == "pipeline_runs"


def test_enums():
    assert ProjectStatus.active.value == "active"
    assert TaskStatus.active.value == "active"
    assert CredentialType.dws.value == "dws"
    assert CredentialStatus.active.value == "active"


def test_pipeline_run_has_task_id():
    assert "task_id" in PipelineRun.__table__.columns
    assert "project_id" not in PipelineRun.__table__.columns


def test_dws_credential_has_project_id():
    assert "project_id" in DwsCredential.__table__.columns


def test_task_has_credential_polymorphic_fields():
    cols = Task.__table__.columns
    assert "credential_id" in cols
    assert "credential_type" in cols
```

- [ ] **Step 9: 运行测试**

Run: `pytest tests/unit/test_models.py -v`
Expected: 5 passed

- [ ] **Step 10: 提交**

```bash
git add docupipe_manager/models/ docupipe_manager/migrations/versions/0001_initial_schema.py tests/unit/test_models.py
git commit -m "refactor(models): 项目-任务两层模型 + 项目级凭证 + 全新迁移

新增 Project/ProjectMember/Task 模型，dws_credentials 加 project_id，
pipeline_runs 关联 task_id；重写 0001 迁移为 5 张表。"
```

---

## Task 2: 项目级权限依赖

**Files:**
- Create: `docupipe_manager/auth/project_access.py`
- Test: `tests/unit/test_project_access.py`

**Interfaces:**
- Consumes: `get_current_user`（返回 `{"id", "username", "role"}`），`app.state.engine`
- Produces: `require_project_access(project_id)`, `require_project_owner(project_id)` — 都是 FastAPI 依赖工厂（接收 path param，返回 dependency 函数），返回 `dict` user。判定规则：admin 永远通过；Owner = `projects.owner_id == user.id`；Member = `project_members` 存在记录。

- [ ] **Step 1: 写失败测试**

```python
# tests/unit/test_project_access.py
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from docupipe_manager.auth.project_access import is_project_owner, is_project_member


@pytest.mark.asyncio
async def test_admin_is_always_owner():
    user = {"id": str(uuid.uuid4()), "role": "admin"}
    assert await is_project_owner(uuid.uuid4(), user) is True


@pytest.mark.asyncio
async def test_admin_is_always_member():
    user = {"id": str(uuid.uuid4()), "role": "admin"}
    assert await is_project_member(uuid.uuid4(), user) is True


@pytest.mark.asyncio
async def test_owner_match():
    owner_id = uuid.uuid4()
    user = {"id": str(owner_id), "role": "user"}
    with patch("docupipe_manager.auth.project_access.get_engine") as mock_ge:
        mock_conn = AsyncMock()
        mock_row = MagicMock()
        mock_row.owner_id = owner_id
        mock_conn.execute = AsyncMock(return_value=MagicMock(fetchone=MagicMock(return_value=mock_row)))
        mock_engine = MagicMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_ge.return_value = mock_engine
        assert await is_project_owner(uuid.uuid4(), user) is True


@pytest.mark.asyncio
async def test_not_owner():
    user = {"id": str(uuid.uuid4()), "role": "user"}
    with patch("docupipe_manager.auth.project_access.get_engine") as mock_ge:
        mock_conn = AsyncMock()
        mock_row = MagicMock()
        mock_row.owner_id = uuid.uuid4()
        mock_conn.execute = AsyncMock(return_value=MagicMock(fetchone=MagicMock(return_value=mock_row)))
        mock_engine = MagicMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_ge.return_value = mock_engine
        assert await is_project_owner(uuid.uuid4(), user) is False
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/unit/test_project_access.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 写 `project_access.py`**

```python
# docupipe_manager/auth/project_access.py
"""项目级权限依赖：access(Owner/Member/admin) 与 owner(admin/Owner)。"""
import uuid

from fastapi import Depends, HTTPException, status

from docupipe_manager.auth.dependencies import get_current_user


def get_engine():
    from docupipe_manager.main import app
    return app.state.engine


async def is_project_owner(project_id: uuid.UUID, user: dict) -> bool:
    if user.get("role") == "admin":
        return True
    from sqlalchemy import select, text
    engine = get_engine()
    async with engine.begin() as conn:
        row = (await conn.execute(
            text("SELECT owner_id FROM docupipe_manager.projects WHERE id = :pid AND status != 'archived'"),
            {"pid": str(project_id)},
        )).fetchone()
    if row is None:
        return False
    return str(row.owner_id) == str(user["id"])


async def is_project_member(project_id: uuid.UUID, user: dict) -> bool:
    if user.get("role") == "admin":
        return True
    if await is_project_owner(project_id, user):
        return True
    from sqlalchemy import text
    engine = get_engine()
    async with engine.begin() as conn:
        row = (await conn.execute(
            text("SELECT 1 FROM docupipe_manager.project_members WHERE project_id = :pid AND user_id = :uid"),
            {"pid": str(project_id), "uid": str(user["id"])},
        )).fetchone()
    return row is not None


def require_project_access(project_id: uuid.UUID):
    """依赖工厂：admin 或 Owner 或 Member 通过，否则 403；项目不存在或归档返回 404。"""
    async def _dep(user: dict = Depends(get_current_user)) -> dict:
        if user.get("role") == "admin":
            return user
        if await is_project_owner(project_id, user):
            return user
        if await is_project_member(project_id, user):
            return user
        from sqlalchemy import text
        engine = get_engine()
        async with engine.begin() as conn:
            exists = (await conn.execute(
                text("SELECT 1 FROM docupipe_manager.projects WHERE id = :pid AND status != 'archived'"),
                {"pid": str(project_id)},
            )).fetchone()
        if exists is None:
            raise HTTPException(status_code=404, detail="Project not found")
        raise HTTPException(status_code=403, detail="Not a project member")
    return _dep


def require_project_owner(project_id: uuid.UUID):
    """依赖工厂：admin 或 Owner 通过，否则 403/404。"""
    async def _dep(user: dict = Depends(get_current_user)) -> dict:
        if await is_project_owner(project_id, user):
            return user
        from sqlalchemy import text
        engine = get_engine()
        async with engine.begin() as conn:
            exists = (await conn.execute(
                text("SELECT 1 FROM docupipe_manager.projects WHERE id = :pid AND status != 'archived'"),
                {"pid": str(project_id)},
            )).fetchone()
        if exists is None:
            raise HTTPException(status_code=404, detail="Project not found")
        raise HTTPException(status_code=403, detail="Project owner required")
    return _dep
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/unit/test_project_access.py -v`
Expected: 4 passed

- [ ] **Step 5: 提交**

```bash
git add docupipe_manager/auth/project_access.py tests/unit/test_project_access.py
git commit -m "feat(auth): 项目级权限依赖 require_project_access/owner"
```

---

## Task 3: CredentialService 项目隔离

**Files:**
- Modify: `docupipe_manager/services/credential_service.py`
- Rewrite: `tests/services/test_credential_service.py`

**Interfaces:**
- Consumes: `DwsCredential.project_id`（Task 1）
- Produces: `CredentialService` 方法签名带 `project_id`：`start_device_login(project_id, name)`, `finalize_login(session_key, name, user_id, project_id)`, `check_status(credential_id, project_id)`, `revoke(credential_id, user_id, project_id)`, `list_credentials(project_id)`。`DwsCredential` 存储时带 `project_id`。

- [ ] **Step 1: 改 `start_device_login` 签名（仅加 project_id 入参，不存 DB）**

在 `credential_service.py` 把 `async def start_device_login(self, name: str) -> dict:` 改为 `async def start_device_login(self, project_id: uuid.UUID, name: str) -> dict:`。session 字典加 `"project_id": project_id`。

- [ ] **Step 2: 改 `finalize_login` 存 project_id**

签名改为 `async def finalize_login(self, session_key, name, user_id, project_id)`。构造 `DwsCredential(...)` 时加 `project_id=project_id`。

- [ ] **Step 3: 改 `check_status` / `revoke` / `list_credentials` 带 project_id 过滤**

```python
    async def check_status(self, credential_id: uuid.UUID, project_id: uuid.UUID) -> dict:
        async with self._session_factory() as db_session:
            credential = await db_session.get(DwsCredential, credential_id)
            if credential is None or credential.project_id != project_id:
                raise ValueError("Credential not found")
        # ... 原有解密 + subprocess 逻辑不变 ...

    async def revoke(self, credential_id: uuid.UUID, user_id: uuid.UUID, project_id: uuid.UUID) -> None:
        async with self._session_factory() as db_session:
            credential = await db_session.get(DwsCredential, credential_id)
            if credential is None or credential.project_id != project_id:
                raise ValueError("Credential not found")
            credential.status = CredentialStatus.revoked
            await db_session.commit()
        # ... push_audit 不变 ...

    async def list_credentials(self, project_id: uuid.UUID) -> list[DwsCredential]:
        async with self._session_factory() as db_session:
            result = await db_session.execute(
                select(DwsCredential)
                .where(DwsCredential.project_id == project_id)
                .order_by(DwsCredential.created_at.desc())
            )
            return list(result.scalars().all())
```

- [ ] **Step 4: 重写测试**

```python
# tests/services/test_credential_service.py
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from docupipe_manager.services.credential_service import CredentialService


@pytest.fixture
def credential_service():
    engine = MagicMock()
    settings = MagicMock()
    settings.dws_cli_path = "dws"
    settings.encryption_key = "0123456789abcdef0123456789abcdef"
    platform_client = MagicMock()
    platform_client.push_audit = AsyncMock()
    return CredentialService(engine, settings, platform_client)


@pytest.mark.asyncio
async def test_revoke_filters_by_project(credential_service):
    pid = uuid.uuid4()
    other_pid = uuid.uuid4()
    cred = MagicMock()
    cred.project_id = other_pid
    with patch.object(credential_service, "_session_factory") as mock_sf:
        mock_session = AsyncMock()
        mock_sf.return_value.__aenter__.return_value = mock_session
        mock_session.get = AsyncMock(return_value=cred)
        with pytest.raises(ValueError):
            await credential_service.revoke(uuid.uuid4(), uuid.uuid4(), pid)


@pytest.mark.asyncio
async def test_list_credentials_filters_by_project(credential_service):
    pid = uuid.uuid4()
    with patch.object(credential_service, "_session_factory") as mock_sf:
        mock_session = AsyncMock()
        mock_sf.return_value.__aenter__.return_value = mock_session
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)
        result = await credential_service.list_credentials(pid)
        assert result == []
```

- [ ] **Step 5: 运行测试**

Run: `pytest tests/services/test_credential_service.py -v`
Expected: passed

- [ ] **Step 6: 提交**

```bash
git add docupipe_manager/services/credential_service.py tests/services/test_credential_service.py
git commit -m "refactor(services): CredentialService 凭证按 project_id 隔离"
```

---

## Task 4: RunnerService 改 task 驱动

**Files:**
- Modify: `docupipe_manager/services/runner_service.py`
- Rewrite: `tests/services/test_runner_service.py`

**Interfaces:**
- Consumes: `Task`, `PipelineRun.task_id`（Task 1）
- Produces: `RunnerService.start_run(task_id, trigger_type, triggered_by, pipeline_name=None, mode="incremental") -> PipelineRun`。`_do_execute` 通过 task 取 `config_yaml`/`credential_id`/`credential_type`，按 `credential_type` 分派解密（本次仅 dws 分支，复用现有 SM4 解密）；`PipelineRun` 写 `task_id`。

- [ ] **Step 1: 改 `start_run` 签名与字段**

把 `start_run` 的 `project_id: uuid.UUID` 参数改为 `task_id: uuid.UUID`，构造 `PipelineRun(task_id=task_id, ...)`。

- [ ] **Step 2: 改 `_do_execute`：task 驱动 + 凭证分派**

替换 `_do_execute` 开头的查询逻辑：

```python
    async def _do_execute(self, run_id: uuid.UUID) -> None:
        async with self._session_factory() as session:
            run = await session.get(PipelineRun, run_id)
            if run is None:
                return
            task = await session.get(Task, run.task_id)
            if task is None:
                await self._mark_run_failed(run_id, "Task not found")
                return
            if task.credential_id is None or task.credential_type is None:
                await self._mark_run_failed(run_id, "Task has no credential bound")
                return
            if task.credential_type == CredentialType.dws:
                credential = await session.get(DwsCredential, task.credential_id)
            else:
                await self._mark_run_failed(run_id, f"Unsupported credential type: {task.credential_type}")
                return
            if credential is None:
                await self._mark_run_failed(run_id, "Credential not found")
                return

            config_yaml = task.config_yaml
            slug = task.slug
            mode = run.mode
            pipeline_name = run.pipeline_name
            cred_type = task.credential_type
```

`project_dir` 改用 task slug（避免不同项目同名 task 冲突，加 project 维度在 Task 8 由路由层注入；此处保持 slug）：

```python
        # 目录按 task 维度（runner 只负责执行）
        project_dir = os.path.join(settings.data_dir, "tasks", str(task.id))
```

其余 subprocess 逻辑不变（凭证解密仍走 `decrypt_sm4`，本次只有 dws）。

- [ ] **Step 3: 更新 import**

`runner_service.py` 顶部 import 改为：

```python
from docupipe_manager.models.task import Task, CredentialType
from docupipe_manager.models.dws_credential import DwsCredential
from docupipe_manager.models.pipeline_run import PipelineRun, RunStatus
```

（删除 `DocupipeProject` import）

- [ ] **Step 4: 重写测试**

```python
# tests/services/test_runner_service.py
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from docupipe_manager.models.task import CredentialType
from docupipe_manager.models.pipeline_run import RunStatus
from docupipe_manager.services.runner_service import RunnerService


@pytest.fixture
def runner_service():
    engine = MagicMock()
    settings = MagicMock()
    settings.max_concurrent_runs = 3
    settings.data_dir = "/tmp/docupipe-test"
    settings.dws_cli_path = "dws"
    settings.docupipe_python = "python"
    settings.run_log_max_bytes = 10 * 1024 * 1024
    settings.encryption_key = "0123456789abcdef0123456789abcdef"
    platform_client = MagicMock()
    platform_client.push_audit = AsyncMock()
    return RunnerService(engine, settings, platform_client)


@pytest.mark.asyncio
async def test_start_run_creates_run_with_task_id(runner_service):
    task_id = uuid.uuid4()
    with patch.object(runner_service, "_session_factory") as mock_sf:
        mock_session = AsyncMock()
        mock_sf.return_value.__aenter__.return_value = mock_session
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()
        with patch.object(runner_service, "_execute_run", new=AsyncMock()):
            run = await runner_service.start_run(
                task_id=task_id, trigger_type="manual", triggered_by=uuid.uuid4(),
            )
            assert run.status == RunStatus.pending
            assert run.task_id == task_id


@pytest.mark.asyncio
async def test_cancel_pending_run(runner_service):
    run_mock = MagicMock()
    run_mock.status = RunStatus.pending
    with patch.object(runner_service, "_session_factory") as mock_sf:
        mock_session = AsyncMock()
        mock_sf.return_value.__aenter__.return_value = mock_session
        mock_session.get = AsyncMock(return_value=run_mock)
        await runner_service.cancel_run(uuid.uuid4())
        assert run_mock.status == RunStatus.cancelled


@pytest.mark.asyncio
async def test_mark_run_failed(runner_service):
    with patch.object(runner_service, "_session_factory") as mock_sf:
        mock_session = AsyncMock()
        mock_sf.return_value.__aenter__.return_value = mock_session
        mock_session.execute = AsyncMock()
        mock_session.commit = AsyncMock()
        await runner_service._mark_run_failed(uuid.uuid4(), "err")
        mock_session.execute.assert_awaited_once()
```

- [ ] **Step 5: 运行测试**

Run: `pytest tests/services/test_runner_service.py -v`
Expected: passed

- [ ] **Step 6: 提交**

```bash
git add docupipe_manager/services/runner_service.py tests/services/test_runner_service.py
git commit -m "refactor(services): RunnerService 改为 task 驱动 + 凭证类型分派"
```

---

## Task 5: SchedulerService 改 task 驱动

**Files:**
- Modify: `docupipe_manager/services/scheduler_service.py`
- Rewrite: `tests/services/test_scheduler_service.py`

**Interfaces:**
- Consumes: `Task`, `TaskStatus`（Task 1）
- Produces: `schedule_task(task_id)`, `unschedule_task(task_id)`；调度键 `task-{task_id}`；`_reload_all` 扫描 `tasks` 表（active + schedule_enabled + schedule_cron not null）；`_scheduled_run(task_id)` 调 `runner.start_run(task_id=..., trigger_type="scheduled", ...)`，从 task 读 schedule_pipeline/schedule_mode。

- [ ] **Step 1: 改 SchedulerService 方法与查询**

```python
# docupipe_manager/services/scheduler_service.py（关键改动）
from docupipe_manager.models.task import Task, TaskStatus
from docupipe_manager.models.pipeline_run import PipelineRun, RunStatus  # RunStatus 未直接用，可删

# schedule_project → schedule_task
async def schedule_task(self, task_id: uuid.UUID) -> None:
    job_id = f"task-{task_id}"
    try:
        self._scheduler.remove_job(job_id)
    except Exception:
        pass
    async with self._session_factory() as session:
        task = await session.get(Task, task_id)
        if task is None:
            return
        if task.status != TaskStatus.active or not task.schedule_enabled or not task.schedule_cron:
            return
        cron = task.schedule_cron
        name = f"task-{task.slug}"
    if not croniter.is_valid(cron):
        logger.warning("Invalid cron for task %s: %s", task_id, cron)
        return
    trigger = CronTrigger.from_crontab(cron)
    self._scheduler.add_job(self._scheduled_run, trigger, args=[task_id], id=job_id,
                            replace_existing=True, name=name)

async def unschedule_task(self, task_id: uuid.UUID) -> None:
    try:
        self._scheduler.remove_job(f"task-{task_id}")
    except Exception:
        pass

async def _reload_all(self) -> None:
    async with self._session_factory() as session:
        result = await session.execute(
            select(Task).where(
                Task.status == TaskStatus.active,
                Task.schedule_enabled.is_(True),
                Task.schedule_cron.isnot(None),
            )
        )
        tasks = list(result.scalars().all())
    for t in tasks:
        await self.schedule_task(t.id)

async def _scheduled_run(self, task_id: uuid.UUID) -> None:
    async with self._session_factory() as session:
        task = await session.get(Task, task_id)
        if task is None or task.status != TaskStatus.active or not task.schedule_enabled:
            return
        pipeline_name = task.schedule_pipeline
        mode = task.schedule_mode
    await self._runner.start_run(
        task_id=task_id, trigger_type="scheduled", triggered_by=None,
        pipeline_name=pipeline_name, mode=mode,
    )
```

- [ ] **Step 2: 重写测试（task 驱动）**

```python
# tests/services/test_scheduler_service.py
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from docupipe_manager.models.task import Task, TaskStatus
from docupipe_manager.services.scheduler_service import SchedulerService


@pytest.fixture
def scheduler_service():
    runner = MagicMock()
    runner.start_run = AsyncMock()
    engine = MagicMock()
    settings = MagicMock()
    return SchedulerService(runner, engine, settings)


def _make_task(status=TaskStatus.active, enabled=True, cron="0 3 * * *", slug="t1"):
    t = MagicMock(spec=Task)
    t.status = status
    t.schedule_enabled = enabled
    t.schedule_cron = cron
    t.slug = slug
    t.schedule_pipeline = None
    t.schedule_mode = "incremental"
    return t


@pytest.mark.asyncio
async def test_schedule_task(scheduler_service):
    with patch.object(scheduler_service, "_session_factory") as mock_sf:
        mock_session = AsyncMock()
        mock_sf.return_value.__aenter__.return_value = mock_session
        mock_session.get = AsyncMock(return_value=_make_task())
        await scheduler_service.schedule_task(uuid.uuid4())
        assert len(scheduler_service._scheduler.get_jobs()) > 0


@pytest.mark.asyncio
async def test_unschedule_task(scheduler_service):
    tid = uuid.uuid4()
    scheduler_service._scheduler.add_job(lambda: None, "interval", seconds=60, id=f"task-{tid}")
    await scheduler_service.unschedule_task(tid)
    assert scheduler_service._scheduler.get_job(f"task-{tid}") is None


@pytest.mark.asyncio
async def test_schedule_task_paused(scheduler_service):
    with patch.object(scheduler_service, "_session_factory") as mock_sf:
        mock_session = AsyncMock()
        mock_sf.return_value.__aenter__.return_value = mock_session
        mock_session.get = AsyncMock(return_value=_make_task(status=TaskStatus.paused))
        await scheduler_service.schedule_task(uuid.uuid4())
        assert len(scheduler_service._scheduler.get_jobs()) == 0


@pytest.mark.asyncio
async def test_scheduled_run_calls_runner(scheduler_service):
    tid = uuid.uuid4()
    with patch.object(scheduler_service, "_session_factory") as mock_sf:
        mock_session = AsyncMock()
        mock_sf.return_value.__aenter__.return_value = mock_session
        mock_session.get = AsyncMock(return_value=_make_task())
        await scheduler_service._scheduled_run(tid)
        scheduler_service._runner.start_run.assert_awaited_once()
        kwargs = scheduler_service._runner.start_run.call_args.kwargs
        assert kwargs["task_id"] == tid
        assert kwargs["trigger_type"] == "scheduled"
```

- [ ] **Step 3: 运行测试**

Run: `pytest tests/services/test_scheduler_service.py -v`
Expected: passed

- [ ] **Step 4: 提交**

```bash
git add docupipe_manager/services/scheduler_service.py tests/services/test_scheduler_service.py
git commit -m "refactor(services): SchedulerService 改为 task 驱动调度"
```

---

## Task 6: 项目 API（admin 创建 + CRUD）

**Files:**
- Rewrite: `docupipe_manager/api/projects.py`
- Create: `tests/api/test_projects.py`

**Interfaces:**
- Consumes: `Project` 模型（Task 1）；`require_admin`、`require_project_access`、`require_project_owner`（Task 2）；`app.state.scheduler`（`schedule_task`/`unschedule_task`）；`app.state.engine`。
- Produces: 两个 router。`admin_router`（prefix `/admin/api/projects`，POST 创建，require_admin）；`router`（prefix `/api/projects`，GET 列表/详情、PUT 编辑、DELETE 归档）。返回 JSON shape 供 UI 与前端调用。

- [ ] **Step 1: 重写 `api/projects.py`**

```python
# docupipe_manager/api/projects.py
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from docupipe_manager.auth.dependencies import require_admin
from docupipe_manager.auth.project_access import require_project_access, require_project_owner
from docupipe_manager.models.project import Project, ProjectStatus

admin_router = APIRouter(prefix="/admin/api/projects", tags=["projects"])
router = APIRouter(prefix="/api/projects", tags=["projects"])


class CreateProjectRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    slug: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-z0-9-]+$")
    description: Optional[str] = None


class UpdateProjectRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    status: Optional[str] = None


def _get_engine():
    from docupipe_manager.main import app
    return app.state.engine


@admin_router.post("")
async def create_project(body: CreateProjectRequest, user: dict = Depends(require_admin)):
    from sqlalchemy import select
    engine = _get_engine()
    async with engine.begin() as conn:
        existing = (await conn.execute(select(Project).where(
            (Project.slug == body.slug) | (Project.name == body.name)
        ))).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="Project name or slug already exists")
        project = Project(
            name=body.name, slug=body.slug, description=body.description,
            owner_id=uuid.UUID(user["id"]),
        )
        conn.add(project)
        await conn.flush()
        pid = project.id
    return {"id": str(pid)}


@router.get("")
async def list_projects(user: dict = Depends(_get_current_user_dep)):
    """admin 看全部；普通用户看自己 Member 的项目（未归档）。"""
    from sqlalchemy import select, text
    engine = _get_engine()
    async with engine.begin() as conn:
        if user.get("role") == "admin":
            rows = (await conn.execute(
                select(Project).where(Project.status != ProjectStatus.archived)
                .order_by(Project.created_at.desc())
            )).fetchall()
        else:
            rows = (await conn.execute(text("""
                SELECT p.* FROM docupipe_manager.projects p
                JOIN docupipe_manager.project_members m ON m.project_id = p.id
                WHERE m.user_id = :uid AND p.status != 'archived'
                ORDER BY p.created_at DESC
            """), {"uid": user["id"]})).fetchall()
    return [_project_dict(r) for r in rows]


@router.get("/{project_id}")
async def get_project(project_id: uuid.UUID, user: dict = Depends(require_project_access_via_path)):
    from sqlalchemy import select
    engine = _get_engine()
    async with engine.begin() as conn:
        row = (await conn.execute(select(Project).where(Project.id == project_id))).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return _project_dict(row, include_owner=True, current_user=user)


@router.put("/{project_id}")
async def update_project(project_id: uuid.UUID, body: UpdateProjectRequest,
                         user: dict = Depends(require_project_access_via_path)):
    from sqlalchemy import select
    engine = _get_engine()
    async with engine.begin() as conn:
        row = (await conn.execute(select(Project).where(Project.id == project_id))).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Project not found")
        data = body.model_dump(exclude_unset=True)
        if "status" in data and data["status"] not in ("active", "paused"):
            raise HTTPException(status_code=400, detail="status must be active or paused")
        await conn.execute(
            Project.__table__.update().where(Project.id == project_id).values(**data)
        )
    return {"status": "updated"}


@admin_router.delete("/{project_id}")
async def archive_project(project_id: uuid.UUID, user: dict = Depends(require_project_owner)):
    """归档项目（owner/admin）+ 取消所有任务调度。"""
    from sqlalchemy import select, update, text
    engine = _get_engine()
    async with engine.begin() as conn:
        row = (await conn.execute(select(Project).where(Project.id == project_id))).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Project not found")
        await conn.execute(update(Project).where(Project.id == project_id).values(status=ProjectStatus.archived))
        task_ids = [r.id for r in (await conn.execute(
            text("SELECT id FROM docupipe_manager.tasks WHERE project_id = :pid"), {"pid": str(project_id)}
        )).fetchall()]
    # 取消所有任务调度
    from docupipe_manager.main import app
    for tid in task_ids:
        await app.state.scheduler.unschedule_task(tid)
    return {"status": "archived"}
```

> 注：上面用了 `require_project_access_via_path` / `_get_current_user_dep` 两个包装。因为 FastAPI 的 path param `project_id` 需要先被路由解析再传给依赖工厂。定义见 Step 2。

- [ ] **Step 2: 补依赖包装与序列化辅助**

在同一文件追加：

```python
from docupipe_manager.auth.dependencies import get_current_user


def require_project_access_via_path(project_id: uuid.UUID, user: dict = Depends(get_current_user)) -> dict:
    """path param 版本：先 require_project_access 判定，再返回 user。"""
    from docupipe_manager.auth.project_access import is_project_member
    import asyncio
    ok = asyncio.get_event_loop().run_until_completion  # 占位，实际见下
    # 注：FastAPI 依赖支持 async，直接用 async 包装更清晰
    raise NotImplementedError  # 见下方正式实现


async def _require_access_async(project_id: uuid.UUID, user: dict) -> dict:
    from docupipe_manager.auth.project_access import is_project_member, is_project_owner
    if user.get("role") == "admin":
        return user
    if await is_project_owner(project_id, user) or await is_project_member(project_id, user):
        return user
    from sqlalchemy import text
    async with _get_engine().begin() as conn:
        exists = (await conn.execute(
            text("SELECT 1 FROM docupipe_manager.projects WHERE id = :pid AND status != 'archived'"),
            {"pid": str(project_id)},
        )).fetchone()
    raise HTTPException(status_code=404 if exists is None else 403,
                        detail="Project not found" if exists is None else "Not a project member")
```

> ⚠️ 清理：上面 Step 1/2 里的占位 `require_project_access_via_path` / `_get_current_user_dep` 是为说明 path param 流程。**正式实现**用统一的 async 依赖（FastAPI 支持 async 依赖直接 await 子协程）。最终文件里：
> - `get_project`/`update_project` 用 `Depends(_require_access_async)`
> - `create_project` 用 `Depends(require_admin)`
> - `archive_project` 用 `Depends(_require_owner_async)`（同结构，调 `is_project_owner`）
> - `list_projects` 用 `Depends(get_current_user)`
> 
> 实施时删除占位的 `require_project_access_via_path` 和 `NotImplementedError`，改用上述 async 函数。

补充 `_require_owner_async`：

```python
async def _require_owner_async(project_id: uuid.UUID, user: dict = Depends(get_current_user)) -> dict:
    from docupipe_manager.auth.project_access import is_project_owner
    if await is_project_owner(project_id, user):
        return user
    from sqlalchemy import text
    async with _get_engine().begin() as conn:
        exists = (await conn.execute(
            text("SELECT 1 FROM docupipe_manager.projects WHERE id = :pid AND status != 'archived'"),
            {"pid": str(project_id)},
        )).fetchone()
    raise HTTPException(status_code=404 if exists is None else 403,
                        detail="Project not found" if exists is None else "Project owner required")
```

`_project_dict`：

```python
def _project_dict(row, include_owner=False, current_user=None) -> dict:
    d = {
        "id": str(row.id), "name": row.name, "slug": row.slug,
        "description": row.description, "status": row.status.value if hasattr(row.status, "value") else row.status,
        "created_at": str(row.created_at),
    }
    if include_owner and current_user is not None:
        d["is_owner"] = (str(row.owner_id) == current_user["id"]) or current_user.get("role") == "admin"
        d["can_manage_members"] = bool(d["is_owner"])
    return d
```

- [ ] **Step 3: 写 API 测试（代表性：创建、列表按角色过滤、权限 403）**

```python
# tests/api/test_projects.py
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import override_get_current_user, clear_overrides


@pytest.mark.asyncio
async def test_create_project_requires_admin(async_client):
    override_get_current_user({"id": str(uuid.uuid4()), "username": "u", "role": "user"})
    r = await async_client.post("/admin/api/projects", json={"name": "p", "slug": "p"})
    assert r.status_code == 403
    clear_overrides()


@pytest.mark.asyncio
async def test_create_project_admin_ok(async_client):
    uid = str(uuid.uuid4())
    override_get_current_user({"id": uid, "username": "a", "role": "admin"})
    fake_project = MagicMock()
    fake_project.id = uuid.uuid4()
    with patch("docupipe_manager.api.projects._get_engine") as mock_ge:
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value=MagicMock(fetchone=MagicMock(return_value=None)))
        mock_conn.add = MagicMock()
        mock_conn.flush = AsyncMock()
        # 模拟 conn.add 后 project.id 可用
        def _add(p):
            p.id = fake_project.id
        mock_conn.add.side_effect = _add
        mock_engine = MagicMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_ge.return_value = mock_engine
        r = await async_client.post("/admin/api/projects", json={"name": "p", "slug": "p"})
        assert r.status_code == 200
        assert "id" in r.json()
    clear_overrides()
```

- [ ] **Step 4: 运行测试**

Run: `pytest tests/api/test_projects.py -v`
Expected: passed

- [ ] **Step 5: 提交**

```bash
git add docupipe_manager/api/projects.py tests/api/test_projects.py
git commit -m "feat(api): 项目 admin 创建 + CRUD（按角色守卫）"
```

---

## Task 7: 成员 API

**Files:**
- Create: `docupipe_manager/api/members.py`
- Create: `tests/api/test_members.py`
- Modify: `docupipe_manager/main.py`（路由注册留到 Task 13 统一处理）

**Interfaces:**
- Consumes: `ProjectMember`（Task 1）；`_require_owner_async` / `_require_access_async`（Task 6 的 `projects.py`，导出复用）；平台用户搜索（`app.state.platform_client`，调用 `batch_get_users` 或新增 `search_users`）。
- Produces: router（prefix `/api/projects/{project_id}/members`）：GET 列表（access）、POST 添加（owner）、DELETE 移除（owner）。

- [ ] **Step 1: 写 `api/members.py`**

```python
# docupipe_manager/api/members.py
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from docupipe_manager.api.projects import _require_access_async, _require_owner_async, _get_engine
from docupipe_manager.models.project_member import ProjectMember

router = APIRouter(prefix="/api/projects/{project_id}/members", tags=["members"])


class AddMemberRequest(BaseModel):
    user_id: str
    username: str | None = None  # 仅用于展示，不存


@router.get("")
async def list_members(project_id: uuid.UUID, user: dict = Depends(_require_access_async)):
    from sqlalchemy import text
    engine = _get_engine()
    async with engine.begin() as conn:
        owner = (await conn.execute(
            text("SELECT owner_id FROM docupipe_manager.projects WHERE id = :pid"),
            {"pid": str(project_id)},
        )).fetchone()
        members = (await conn.execute(text("""
            SELECT user_id, added_by, created_at FROM docupipe_manager.project_members
            WHERE project_id = :pid ORDER BY created_at
        """), {"pid": str(project_id)})).fetchall()
    all_ids = {str(owner.owner_id)} | {str(m.user_id) for m in members}
    # 批量取用户名（platform client）
    from docupipe_manager.main import app
    names = {}
    try:
        names = await app.state.platform_client.batch_get_users(list(all_ids))
    except Exception:
        pass
    return {
        "owner": {"user_id": str(owner.owner_id), "username": names.get(str(owner.owner_id), ""), "is_owner": True},
        "members": [
            {"user_id": str(m.user_id), "username": names.get(str(m.user_id), ""),
             "added_by": str(m.added_by), "created_at": str(m.created_at)}
            for m in members
        ],
    }


@router.post("")
async def add_member(project_id: uuid.UUID, body: AddMemberRequest,
                     user: dict = Depends(_require_owner_async)):
    from sqlalchemy import select, text
    engine = _get_engine()
    async with engine.begin() as conn:
        owner = (await conn.execute(
            text("SELECT owner_id FROM docupipe_manager.projects WHERE id = :pid"),
            {"pid": str(project_id)},
        )).fetchone()
        if str(owner.owner_id) == body.user_id:
            raise HTTPException(status_code=400, detail="Owner is already in project")
        existing = (await conn.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project_id,
                ProjectMember.user_id == uuid.UUID(body.user_id),
            )
        )).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="User is already a member")
        m = ProjectMember(
            project_id=project_id, user_id=uuid.UUID(body.user_id),
            added_by=uuid.UUID(user["id"]),
        )
        conn.add(m)
        await conn.flush()
    return {"status": "added", "user_id": body.user_id}


@router.delete("/{user_id}")
async def remove_member(project_id: uuid.UUID, user_id: uuid.UUID,
                        user: dict = Depends(_require_owner_async)):
    from sqlalchemy import delete, text
    engine = _get_engine()
    async with engine.begin() as conn:
        owner = (await conn.execute(
            text("SELECT owner_id FROM docupipe_manager.projects WHERE id = :pid"),
            {"pid": str(project_id)},
        )).fetchone()
        if str(owner.owner_id) == str(user_id):
            raise HTTPException(status_code=400, detail="Cannot remove owner")
        await conn.execute(delete(ProjectMember).where(
            ProjectMember.project_id == project_id, ProjectMember.user_id == user_id
        ))
    return {"status": "removed"}
```

- [ ] **Step 2: 平台用户搜索辅助（`platform/client.py` 加 `search_users`）**

若 `XinyiPlatformClient` 无 `search_users`，加一个透传方法（POST `/api/users/search` 或复用平台现有接口）。如平台无此接口，UI 端用 `batch_get_users` + 前缀匹配兜底。本步在 Task 12 UI 联调时确认；此处 API 层先约定 `GET /api/projects/{project_id}/members/search?q=` 返回平台用户建议列表（透传 `platform_client.search_users(q)`）。

- [ ] **Step 3: 写测试（添加/移除/重复/移除 owner 报错）**

```python
# tests/api/test_members.py
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import override_get_current_user, clear_overrides


@pytest.mark.asyncio
async def test_add_member_owner_ok(async_client):
    owner_id = uuid.uuid4()
    override_get_current_user({"id": str(owner_id), "username": "o", "role": "user"})
    pid = uuid.uuid4()
    with patch("docupipe_manager.api.members._require_owner_async", new=AsyncMock(return_value={"id": str(owner_id), "role": "user"})), \
         patch("docupipe_manager.api.members._get_engine") as mock_ge:
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(side_effect=[
            MagicMock(fetchone=MagicMock(return_value=MagicMock(owner_id=owner_id))),  # owner check
            MagicMock(fetchone=MagicMock(return_value=None)),  # existing member
        ])
        mock_conn.add = MagicMock()
        mock_conn.flush = AsyncMock()
        mock_engine = MagicMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_ge.return_value = mock_engine
        r = await async_client.post(f"/api/projects/{pid}/members", json={"user_id": str(uuid.uuid4())})
        assert r.status_code == 200
    clear_overrides()
```

- [ ] **Step 4: 运行测试**

Run: `pytest tests/api/test_members.py -v`
Expected: passed

- [ ] **Step 5: 提交**

```bash
git add docupipe_manager/api/members.py tests/api/test_members.py
git commit -m "feat(api): 项目成员增删查（owner 守卫）"
```

---

## Task 8: 凭证 API（项目级 device flow）

**Files:**
- Rewrite: `docupipe_manager/api/credentials.py`
- Create: `tests/api/test_credentials.py`

**Interfaces:**
- Consumes: `CredentialService`（Task 3，方法带 `project_id`）；`_require_access_async`（Task 6）。
- Produces: router（prefix `/api/projects/{project_id}/credentials`）：GET 列表、POST `/device-login/start`、GET `/device-login/poll`、POST `/device-login/finalize`、GET `/{cid}/status`、DELETE `/{cid}`。

- [ ] **Step 1: 重写 `api/credentials.py`**

```python
# docupipe_manager/api/credentials.py
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from docupipe_manager.api.projects import _require_access_async

router = APIRouter(prefix="/api/projects/{project_id}/credentials", tags=["credentials"])


class FinalizeRequest(BaseModel):
    session_key: str
    name: str = Field(..., min_length=1, max_length=255)


@router.get("")
async def list_credentials(project_id: uuid.UUID, user: dict = Depends(_require_access_async)):
    from docupipe_manager.main import app
    creds = await app.state.credential.list_credentials(project_id)
    return [
        {"id": str(c.id), "name": c.name, "corp_id": c.corp_id, "status": c.status.value,
         "token_expires_at": str(c.token_expires_at) if c.token_expires_at else None,
         "created_at": str(c.created_at)}
        for c in creds if c.status != "revoked"
    ]


@router.post("/device-login/start")
async def start_device_login(project_id: uuid.UUID, name: str,
                             user: dict = Depends(_require_access_async)):
    from docupipe_manager.main import app
    return await app.state.credential.start_device_login(project_id, name)


@router.get("/device-login/poll")
async def poll_device_login(project_id: uuid.UUID, session_key: str,
                            user: dict = Depends(_require_access_async)):
    from docupipe_manager.main import app
    return await app.state.credential.poll_device_login(session_key)


@router.post("/device-login/finalize")
async def finalize_device_login(project_id: uuid.UUID, body: FinalizeRequest,
                                user: dict = Depends(_require_access_async)):
    from docupipe_manager.main import app
    cred = await app.state.credential.finalize_login(
        body.session_key, body.name, uuid.UUID(user["id"]), project_id
    )
    return {"id": str(cred.id), "status": "active"}


@router.get("/{credential_id}/status")
async def check_status(project_id: uuid.UUID, credential_id: uuid.UUID,
                       user: dict = Depends(_require_access_async)):
    from docupipe_manager.main import app
    try:
        return await app.state.credential.check_status(credential_id, project_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/{credential_id}")
async def revoke_credential(project_id: uuid.UUID, credential_id: uuid.UUID,
                            user: dict = Depends(_require_access_async)):
    from docupipe_manager.main import app
    try:
        await app.state.credential.revoke(credential_id, uuid.UUID(user["id"]), project_id)
        return {"status": "revoked"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
```

- [ ] **Step 2: 写测试（mock CredentialService）**

```python
# tests/api/test_credentials.py
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import override_get_current_user, clear_overrides


@pytest.mark.asyncio
async def test_list_credentials(async_client):
    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    pid = uuid.uuid4()
    cred = MagicMock()
    cred.id = uuid.uuid4(); cred.name = "c1"; cred.corp_id = "x"; cred.status = MagicMock(value="active")
    cred.token_expires_at = None; cred.created_at = "2026-01-01"
    with patch("docupipe_manager.api.credentials._require_access_async", new=AsyncMock(return_value={"role": "admin"})):
        with patch("docupipe_manager.main.app") as mock_app:
            mock_app.state.credential.list_credentials = AsyncMock(return_value=[cred])
            r = await async_client.get(f"/api/projects/{pid}/credentials")
            assert r.status_code == 200
            assert len(r.json()) == 1
    clear_overrides()
```

- [ ] **Step 3: 运行测试**

Run: `pytest tests/api/test_credentials.py -v`
Expected: passed

- [ ] **Step 4: 提交**

```bash
git add docupipe_manager/api/credentials.py tests/api/test_credentials.py
git commit -m "feat(api): 项目级 DWS 凭证 device flow（access 守卫）"
```

---

## Task 9: 任务 API（CRUD + 触发）

**Files:**
- Create: `docupipe_manager/api/tasks.py`
- Create: `tests/api/test_tasks.py`

**Interfaces:**
- Consumes: `Task` 模型（Task 1）；`_require_access_async`（Task 6）；`app.state.scheduler`（`schedule_task`/`unschedule_task`）；`app.state.runner`（`start_run`）。
- Produces: router（prefix `/api/projects/{project_id}/tasks`）：GET 列表、POST 创建、GET/PUT/DELETE `/{task_id}`、POST `/{task_id}/trigger`。YAML 与 cron 校验沿用现有 `field_validator`。

- [ ] **Step 1: 写 `api/tasks.py`**

```python
# docupipe_manager/api/tasks.py
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from typing_extensions import Literal

from docupipe_manager.api.projects import _require_access_async, _get_engine
from docupipe_manager.models.task import Task, TaskStatus

router = APIRouter(prefix="/api/projects/{project_id}/tasks", tags=["tasks"])


def _validate_yaml(v: str) -> str:
    import yaml
    if not v.strip():
        raise ValueError("config_yaml must not be empty")
    try:
        parsed = yaml.safe_load(v)
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML: {e}")
    if not isinstance(parsed, dict):
        raise ValueError("YAML must be a mapping")
    if not isinstance(parsed.get("pipelines"), list):
        raise ValueError("YAML must contain a 'pipelines' list")
    return v


def _validate_cron(v: Optional[str]) -> Optional[str]:
    if v:
        from croniter import croniter
        if not croniter.is_valid(v):
            raise ValueError(f"Invalid cron: {v}")
    return v


class CreateTaskRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    slug: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-z0-9-]+$")
    description: Optional[str] = None
    config_yaml: str
    credential_id: Optional[str] = None
    credential_type: Optional[Literal["dws"]] = None
    schedule_cron: Optional[str] = None
    schedule_enabled: bool = True
    schedule_pipeline: Optional[str] = None
    schedule_mode: Literal["full", "incremental", "mirror"] = "incremental"

    @field_validator("config_yaml")
    @classmethod
    def _v_yaml(cls, v): return _validate_yaml(v)

    @field_validator("schedule_cron")
    @classmethod
    def _v_cron(cls, v): return _validate_cron(v)


class UpdateTaskRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    config_yaml: Optional[str] = None
    credential_id: Optional[str] = None
    credential_type: Optional[Literal["dws"]] = None
    schedule_cron: Optional[str] = None
    schedule_enabled: Optional[bool] = None
    schedule_pipeline: Optional[str] = None
    schedule_mode: Optional[Literal["full", "incremental", "mirror"]] = None

    @field_validator("config_yaml")
    @classmethod
    def _v_yaml(cls, v): return _validate_yaml(v) if v else v

    @field_validator("schedule_cron")
    @classmethod
    def _v_cron(cls, v): return _validate_cron(v)


class TriggerRequest(BaseModel):
    pipeline_name: Optional[str] = None
    mode: Optional[Literal["full", "incremental", "mirror"]] = None


@router.get("")
async def list_tasks(project_id: uuid.UUID, user: dict = Depends(_require_access_async)):
    from sqlalchemy import select, text
    engine = _get_engine()
    async with engine.begin() as conn:
        rows = (await conn.execute(text("""
            SELECT t.id, t.name, t.slug, t.schedule_cron, t.schedule_enabled,
                   t.schedule_pipeline, t.schedule_mode, t.status, t.created_at,
                   (SELECT status FROM docupipe_manager.pipeline_runs
                    WHERE task_id = t.id ORDER BY created_at DESC LIMIT 1) as last_run_status
            FROM docupipe_manager.tasks t
            WHERE t.project_id = :pid AND t.status != 'archived'
            ORDER BY t.created_at DESC
        """), {"pid": str(project_id)})).fetchall()
    return [_task_summary(r) for r in rows]


@router.post("")
async def create_task(project_id: uuid.UUID, body: CreateTaskRequest,
                      user: dict = Depends(_require_access_async)):
    engine = _get_engine()
    async with engine.begin() as conn:
        task = Task(
            project_id=project_id, name=body.name, slug=body.slug,
            description=body.description, config_yaml=body.config_yaml,
            credential_id=uuid.UUID(body.credential_id) if body.credential_id else None,
            credential_type=body.credential_type,
            schedule_cron=body.schedule_cron, schedule_enabled=body.schedule_enabled,
            schedule_pipeline=body.schedule_pipeline, schedule_mode=body.schedule_mode,
            created_by=uuid.UUID(user["id"]),
        )
        conn.add(task)
        await conn.flush()
        tid = task.id
    if body.schedule_cron:
        from docupipe_manager.main import app
        await app.state.scheduler.schedule_task(tid)
    return {"id": str(tid)}


@router.get("/{task_id}")
async def get_task(project_id: uuid.UUID, task_id: uuid.UUID,
                   user: dict = Depends(_require_access_async)):
    from sqlalchemy import select
    engine = _get_engine()
    async with engine.begin() as conn:
        t = (await conn.execute(select(Task).where(Task.id == task_id, Task.project_id == project_id))).fetchone()
    if t is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return _task_detail(t)


@router.put("/{task_id}")
async def update_task(project_id: uuid.UUID, task_id: uuid.UUID, body: UpdateTaskRequest,
                      user: dict = Depends(_require_access_async)):
    from sqlalchemy import select, update
    engine = _get_engine()
    async with engine.begin() as conn:
        t = (await conn.execute(select(Task).where(Task.id == task_id, Task.project_id == project_id))).fetchone()
        if t is None:
            raise HTTPException(status_code=404, detail="Task not found")
        data = body.model_dump(exclude_unset=True)
        if data.get("credential_id"):
            data["credential_id"] = uuid.UUID(data["credential_id"])
        await conn.execute(update(Task).where(Task.id == task_id).values(**data))
    from docupipe_manager.main import app
    if data.get("schedule_cron"):
        await app.state.scheduler.schedule_task(task_id)
    elif "schedule_cron" in data and data["schedule_cron"] is None:
        await app.state.scheduler.unschedule_task(task_id)
    return {"status": "updated"}


@router.delete("/{task_id}")
async def archive_task(project_id: uuid.UUID, task_id: uuid.UUID,
                       user: dict = Depends(_require_access_async)):
    from sqlalchemy import select, update
    engine = _get_engine()
    async with engine.begin() as conn:
        t = (await conn.execute(select(Task).where(Task.id == task_id, Task.project_id == project_id))).fetchone()
        if t is None:
            raise HTTPException(status_code=404, detail="Task not found")
        await conn.execute(update(Task).where(Task.id == task_id).values(status=TaskStatus.archived))
    from docupipe_manager.main import app
    await app.state.scheduler.unschedule_task(task_id)
    return {"status": "archived"}


@router.post("/{task_id}/trigger")
async def trigger_task(project_id: uuid.UUID, task_id: uuid.UUID, body: TriggerRequest,
                       user: dict = Depends(_require_access_async)):
    from sqlalchemy import select
    engine = _get_engine()
    async with engine.begin() as conn:
        t = (await conn.execute(select(Task).where(Task.id == task_id, Task.project_id == project_id))).fetchone()
        if t is None:
            raise HTTPException(status_code=404, detail="Task not found")
    from docupipe_manager.main import app
    run = await app.state.runner.start_run(
        task_id=task_id, trigger_type="manual", triggered_by=uuid.UUID(user["id"]),
        pipeline_name=body.pipeline_name or t.schedule_pipeline,
        mode=body.mode or t.schedule_mode,
    )
    return {"run_id": str(run.id), "status": run.status.value}


def _task_summary(r) -> dict:
    return {
        "id": str(r.id), "name": r.name, "slug": r.slug,
        "schedule_cron": r.schedule_cron, "schedule_enabled": r.schedule_enabled,
        "schedule_pipeline": r.schedule_pipeline, "schedule_mode": r.schedule_mode,
        "status": r.status.value if hasattr(r.status, "value") else r.status,
        "last_run_status": r.last_run_status,
        "created_at": str(r.created_at),
    }


def _task_detail(t) -> dict:
    return {
        "id": str(t.id), "name": t.name, "slug": t.slug, "description": t.description,
        "config_yaml": t.config_yaml,
        "credential_id": str(t.credential_id) if t.credential_id else None,
        "credential_type": t.credential_type.value if t.credential_type else None,
        "schedule_cron": t.schedule_cron, "schedule_enabled": t.schedule_enabled,
        "schedule_pipeline": t.schedule_pipeline, "schedule_mode": t.schedule_mode,
        "status": t.status.value if hasattr(t.status, "value") else t.status,
    }
```

- [ ] **Step 2: 写测试（创建 + 触发 + YAML 校验失败）**

```python
# tests/api/test_tasks.py
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import override_get_current_user, clear_overrides

VALID_YAML = "pipelines:\n  - name: p1\n"


@pytest.mark.asyncio
async def test_create_task_invalid_yaml(async_client):
    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    pid = uuid.uuid4()
    r = await async_client.post(f"/api/projects/{pid}/tasks",
                                json={"name": "t", "slug": "t", "config_yaml": "not: a: list"})
    assert r.status_code == 422
    clear_overrides()


@pytest.mark.asyncio
async def test_create_task_ok(async_client):
    uid = str(uuid.uuid4())
    override_get_current_user({"id": uid, "role": "admin"})
    pid = uuid.uuid4()
    fake_task = MagicMock(); fake_task.id = uuid.uuid4()
    with patch("docupipe_manager.api.tasks._require_access_async", new=AsyncMock(return_value={"id": uid, "role": "admin"})), \
         patch("docupipe_manager.api.tasks._get_engine") as mock_ge:
        mock_conn = AsyncMock()
        mock_conn.add = MagicMock()
        mock_conn.flush = AsyncMock()
        def _add(tk): tk.id = fake_task.id
        mock_conn.add.side_effect = _add
        mock_engine = MagicMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_ge.return_value = mock_engine
        with patch("docupipe_manager.main.app") as mock_app:
            mock_app.state.scheduler.schedule_task = AsyncMock()
            r = await async_client.post(f"/api/projects/{pid}/tasks",
                                        json={"name": "t", "slug": "t", "config_yaml": VALID_YAML})
            assert r.status_code == 200
    clear_overrides()
```

- [ ] **Step 3: 运行测试**

Run: `pytest tests/api/test_tasks.py -v`
Expected: passed

- [ ] **Step 4: 提交**

```bash
git add docupipe_manager/api/tasks.py tests/api/test_tasks.py
git commit -m "feat(api): 任务 CRUD + 手动触发（access 守卫）"
```

---

## Task 10: 运行 API（task_id + 权限过滤）

**Files:**
- Modify: `docupipe_manager/api/runs.py`
- Modify: `docupipe_manager/api/stats.py`（适配新模型）
- Create: `tests/api/test_runs.py`

**Interfaces:**
- Consumes: `PipelineRun.task_id`（Task 1）；`get_current_user`；项目可见性判定（admin 全看，否则 join member/owner）。
- Produces: `runs.py` router（prefix `/api/runs`）：GET 列表（`task_id`/`project_id` 可选过滤，按可见项目过滤）、GET `/{id}`、GET `/{id}/log`、GET `/{id}/download-log`、POST `/{id}/cancel`。

- [ ] **Step 1: 改 `runs.py` 前缀与过滤**

把 `prefix="/admin/api/docupipe/runs"` 改为 `prefix="/api/runs"`。`list_runs` 入参 `project_id` → `task_id`（可选）。替换守卫 `require_admin` 为 `get_current_user`，并在查询里加可见项目过滤：

```python
# 列表过滤核心（runs.py 内）
@router.get("")
async def list_runs(task_id: Optional[uuid.UUID] = None, status: Optional[str] = None,
                    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                    user: dict = Depends(get_current_user)):
    from docupipe_manager.main import app
    from sqlalchemy import select, func, text
    from docupipe_manager.models.pipeline_run import PipelineRun

    # 可见 task 集合：admin 不限；否则限定在 member/owner 的项目里的 task
    engine = app.state.engine
    async with engine.begin() as conn:
        if user.get("role") != "admin":
            visible_tasks = [r.id for r in (await conn.execute(text("""
                SELECT t.id FROM docupipe_manager.tasks t
                WHERE t.project_id IN (
                    SELECT id FROM docupipe_manager.projects WHERE owner_id = :uid AND status != 'archived'
                    UNION
                    SELECT pm.project_id FROM docupipe_manager.project_members pm WHERE pm.user_id = :uid
                )
            """), {"uid": user["id"]})).fetchall()]
            if not visible_tasks:
                return {"total": 0, "page": page, "page_size": page_size, "runs": []}
        # 其余 count/list 查询，非 admin 追加 PipelineRun.task_id.in_(visible_tasks) 条件
        ...
```

`get_run` / `get_run_log` / `download_run_log` / `cancel_run`：把 `require_admin` 改为通过 run→task→project 的可见性校验（admin 跳过）。

- [ ] **Step 2: 适配 `stats.py`**

`stats.py` 里若引用 `docupipe_projects` 表，改为 `projects`/`tasks`，计数按可见项目过滤。具体改动按现有 `get_stats` 逻辑替换表名与权限。

- [ ] **Step 3: 写测试（admin 列表、普通用户过滤）**

```python
# tests/api/test_runs.py
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import override_get_current_user, clear_overrides


@pytest.mark.asyncio
async def test_list_runs_admin(async_client):
    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    with patch("docupipe_manager.main.app") as mock_app:
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(side_effect=[
            MagicMock(scalar=MagicMock(return_value=0)),  # count
            MagicMock(fetchall=MagicMock(return_value=[])),  # list
        ])
        mock_engine = MagicMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_app.state.engine = mock_engine
        r = await async_client.get("/api/runs")
        assert r.status_code == 200
        assert r.json()["total"] == 0
    clear_overrides()
```

- [ ] **Step 4: 运行测试**

Run: `pytest tests/api/test_runs.py -v`
Expected: passed

- [ ] **Step 5: 提交**

```bash
git add docupipe_manager/api/runs.py docupipe_manager/api/stats.py tests/api/test_runs.py
git commit -m "refactor(api): runs 改 task_id + 按可见项目过滤"
```

---

## Task 11: UI 页面路由与项目列表/详情

**Files:**
- Modify: `docupipe_manager/api/pages.py`
- Rewrite: `docupipe_manager/templates/docupipe/projects.html`
- Create: `docupipe_manager/templates/docupipe/project_detail.html`
- Delete: `docupipe_manager/templates/docupipe/project_form.html`

**Interfaces:**
- Consumes: 所有 API（Task 6-10）；`get_current_user`。
- Produces: 页面路由 `/docupipe/projects`（列表）、`/docupipe/projects/new`（admin 创建表单）、`/docupipe/projects/{id}`（详情 Tab）、`/docupipe/projects/{id}/tasks/new`、`/docupipe/projects/{id}/tasks/{tid}/edit`。模板内通过 fetch 调 API。

- [ ] **Step 1: 改 `pages.py` 路由**

删除 `projects_new` / `projects_edit` 里对 `docupipe_projects` 的 SQL，改为：列表页直接渲染空壳（数据由前端 fetch `/api/projects`）；新增 `project_detail` 路由只校验 access 后渲染壳；`projects_new` 仅 admin 可访问。

```python
# pages.py 关键改动（保留 _render/_ui_vars 不变）
@router.get("/projects")
async def projects_list(request: Request, user: dict = Depends(get_current_user)):
    return _render(request, "docupipe/projects.html", {"current_user": user})


@router.get("/projects/new")
async def projects_new(request: Request, user: dict = Depends(require_admin)):
    return _render(request, "docupipe/project_detail.html",
                   {"current_user": user, "mode": "new", "project": None})


@router.get("/projects/{project_id}")
async def project_detail(request: Request, project_id: str, user: dict = Depends(get_current_user)):
    # 可见性由 API 校验；这里只渲染壳，前端 fetch /api/projects/{id}
    return _render(request, "docupipe/project_detail.html",
                   {"current_user": user, "mode": "view", "project_id": project_id, "project": None})


@router.get("/projects/{project_id}/tasks/new")
async def task_new(request: Request, project_id: str, user: dict = Depends(get_current_user)):
    return _render(request, "docupipe/task_form.html",
                   {"current_user": user, "project_id": project_id, "task": None})


@router.get("/projects/{project_id}/tasks/{task_id}/edit")
async def task_edit(request: Request, project_id: str, task_id: str, user: dict = Depends(get_current_user)):
    return _render(request, "docupipe/task_form.html",
                   {"current_user": user, "project_id": project_id, "task_id": task_id, "task": None})
```

（import 加 `require_admin`；删除旧的凭证查询逻辑）

- [ ] **Step 2: 重写 `projects.html`**

```html
{% extends "base.html" %}
{% block content %}
<div class="container">
  <div class="flex justify-between items-center mb-4">
    <h1 class="text-2xl font-bold">项目</h1>
    {% if current_user.role == "admin" %}
    <a href="/docupipe/projects/new" class="btn btn-primary">创建项目</a>
    {% endif %}
  </div>
  <div id="project-list" class="space-y-2">
    <p class="text-gray-500">加载中...</p>
  </div>
</div>
<script>
async function loadProjects() {
  const r = await fetch("/api/projects");
  const projects = await r.json();
  const box = document.getElementById("project-list");
  if (!projects.length) {
    box.innerHTML = '<p class="text-gray-500">暂无可见的项目。</p>';
    return;
  }
  box.innerHTML = projects.map(p => `
    <a href="/docupipe/projects/${p.id}" class="block card p-4 hover:shadow">
      <div class="flex justify-between">
        <span class="font-semibold">${p.name}</span>
        <span class="text-sm ${p.status === 'active' ? 'text-green-600' : 'text-gray-400'}">${p.status}</span>
      </div>
      ${p.description ? `<p class="text-sm text-gray-600 mt-1">${p.description}</p>` : ""}
    </a>`).join("");
}
loadProjects();
</script>
{% endblock %}
```

- [ ] **Step 3: 写 `project_detail.html`（Tab 壳 + fetch）**

```html
{% extends "base.html" %}
{% block content %}
<div class="container" data-project-id="{{ project_id or '' }}" data-mode="{{ mode }}">
  {% if mode == "new" %}
  <h1 class="text-2xl font-bold mb-4">创建项目</h1>
  <form id="project-form" class="space-y-3 max-w-lg">
    <input name="name" placeholder="项目名" class="input" required>
    <input name="slug" placeholder="slug (a-z0-9-)" pattern="[a-z0-9-]+" class="input" required>
    <textarea name="description" placeholder="描述（可选）" class="input"></textarea>
    <button class="btn btn-primary">创建</button>
  </form>
  <script>
    document.getElementById("project-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = new FormData(e.target);
      const body = Object.fromEntries(f.entries());
      const r = await fetch("/admin/api/projects", {
        method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body),
      });
      if (r.ok) { const j = await r.json(); location.href = `/docupipe/projects/${j.id}`; }
      else { alert((await r.json()).detail || "创建失败"); }
    });
  </script>
  {% else %}
  <div class="flex items-center gap-3 mb-4">
    <h1 id="proj-name" class="text-2xl font-bold">加载中...</h1>
    <span id="proj-status" class="text-sm text-gray-500"></span>
  </div>
  <div class="tabs mb-4">
    <button class="tab tab-active" data-tab="tasks">任务</button>
    <button class="tab" data-tab="credentials">凭证</button>
    <button class="tab" data-tab="members">成员</button>
    <button class="tab" data-tab="runs">运行历史</button>
  </div>
  <div id="tab-tasks" class="tab-panel"></div>
  <div id="tab-credentials" class="tab-panel hidden"></div>
  <div id="tab-members" class="tab-panel hidden"></div>
  <div id="tab-runs" class="tab-panel hidden"></div>
  <script src="/static/js/project_detail.js"></script>
  {% endif %}
</div>
<script>
// Tab 切换
document.querySelectorAll(".tab").forEach(b => b.addEventListener("click", () => {
  document.querySelectorAll(".tab").forEach(x => x.classList.remove("tab-active"));
  document.querySelectorAll(".tab-panel").forEach(x => x.classList.add("hidden"));
  b.classList.add("tab-active");
  document.getElementById("tab-" + b.dataset.tab).classList.remove("hidden");
}));
</script>
{% endblock %}
```

> `project_detail.js` 加载各 Tab 数据（fetch `/api/projects/{id}/tasks` 等），实现任务列表/触发、凭证 device flow、成员增删、运行历史。脚本放 `docupipe_manager/static/js/project_detail.js`（若无 static 目录，创建并在 `main.py` 挂载 StaticFiles）。

- [ ] **Step 4: 写 `project_detail.js`（任务/凭证/成员/运行 Tab 交互）**

```javascript
// docupipe_manager/static/js/project_detail.js
const pid = document.querySelector("[data-project-id]").dataset.projectId;

async function loadProject() {
  const r = await fetch(`/api/projects/${pid}`);
  if (!r.ok) { location.href = "/docupipe/projects"; return; }
  const p = await r.json();
  document.getElementById("proj-name").textContent = p.name;
  document.getElementById("proj-status").textContent = p.status;
}

async function loadTasks() {
  const r = await fetch(`/api/projects/${pid}/tasks`);
  const tasks = await r.json();
  const box = document.getElementById("tab-tasks");
  if (!tasks.length) { box.innerHTML = '<p class="text-gray-500">无任务。<a class="link" href="/docupipe/projects/'+pid+'/tasks/new">新建任务</a></p>'; return; }
  box.innerHTML = `<div class="mb-2"><a class="btn btn-sm btn-primary" href="/docupipe/projects/${pid}/tasks/new">新建任务</a></div>` +
    tasks.map(t => `
    <div class="card p-3 flex justify-between items-center">
      <div>
        <a class="font-semibold" href="/docupipe/projects/${pid}/tasks/${t.id}/edit">${t.name}</a>
        <span class="text-xs text-gray-500 ml-2">${t.schedule_cron || "手动"} · ${t.schedule_mode}</span>
      </div>
      <div class="flex gap-2 items-center">
        ${t.last_run_status ? `<span class="text-xs">${t.last_run_status}</span>` : ""}
        <button class="btn btn-sm trigger" data-id="${t.id}">触发</button>
      </div>
    </div>`).join("");
  box.querySelectorAll(".trigger").forEach(b => b.addEventListener("click", async () => {
    const r = await fetch(`/api/projects/${pid}/tasks/${b.dataset.id}/trigger`, {method: "POST", headers: {"Content-Type": "application/json"}, body: "{}"});
    alert(r.ok ? "已触发" : "触发失败");
  }));
}

async function loadCredentials() { /* fetch /api/projects/{pid}/credentials，渲染列表 + device flow 三步按钮 */ }
async function loadMembers() { /* fetch members，渲染 owner + 成员列表；is_owner 时显示添加/删除 */ }
async function loadRuns() { /* fetch /api/runs?task_id=... 或按项目聚合，渲染运行历史 */ }

loadProject(); loadTasks(); loadCredentials(); loadMembers(); loadRuns();
```

（`loadCredentials`/`loadMembers`/`loadRuns` 按 device flow 三步与成员增删 API 实现，模板字符串渲染。）

- [ ] **Step 5: 删除旧 `project_form.html`**

```bash
rm docupipe_manager/templates/docupipe/project_form.html
```

- [ ] **Step 6: 验证页面可访问（应用启动后）**

Run: `uv run uvicorn docupipe_manager.main:app`（需 DB）
手动访问 `/docupipe/projects`、`/docupipe/projects/new`、`/docupipe/projects/{id}`，确认渲染无 500。

- [ ] **Step 7: 提交**

```bash
git add docupipe_manager/api/pages.py docupipe_manager/templates/ docupipe_manager/static/
git commit -m "feat(ui): 项目列表 + Tab 式项目详情（任务/凭证/成员/运行）"
```

---

## Task 12: 任务表单与凭证/成员 UI 细化

**Files:**
- Create: `docupipe_manager/templates/docupipe/task_form.html`
- Create: `docupipe_manager/static/js/task_form.js`
- Modify: `project_detail.js`（补全凭证 device flow 三步、成员增删、运行历史）

**Interfaces:**
- Consumes: `/api/projects/{pid}/credentials`（凭证下拉）、`/api/projects/{pid}/tasks`（POST/PUT）。
- Produces: 任务创建/编辑表单（yaml 文本框 + 凭证下拉 + cron + mode + pipeline）。

- [ ] **Step 1: 写 `task_form.html`**

```html
{% extends "base.html" %}
{% block content %}
<div class="container max-w-2xl" data-project-id="{{ project_id }}" data-task-id="{{ task_id or '' }}">
  <h1 class="text-2xl font-bold mb-4">{{ task_id ? '编辑任务' : '新建任务' }}</h1>
  <form id="task-form" class="space-y-3">
    <input name="name" placeholder="任务名" class="input" required>
    <input name="slug" placeholder="slug (a-z0-9-)" pattern="[a-z0-9-]+" class="input" required>
    <textarea name="description" placeholder="描述" class="input"></textarea>
    <label class="block text-sm font-medium">config.yaml</label>
    <textarea name="config_yaml" rows="12" class="input font-mono" required>pipelines:
  - name: default</textarea>
    <div>
      <label class="block text-sm font-medium">凭证</label>
      <select name="credential_id" class="input"><option value="">（无）</option></select>
    </div>
    <div class="grid grid-cols-2 gap-3">
      <input name="schedule_cron" placeholder="cron（如 0 3 * * *）" class="input">
      <select name="schedule_mode" class="input">
        <option value="incremental">incremental</option><option value="full">full</option><option value="mirror">mirror</option>
      </select>
    </div>
    <input name="schedule_pipeline" placeholder="pipeline 名（可选）" class="input">
    <label class="flex items-center gap-2"><input type="checkbox" name="schedule_enabled" checked> 启用调度</label>
    <input type="hidden" name="credential_type" value="dws">
    <div class="flex gap-2">
      <button class="btn btn-primary">保存</button>
      <a class="btn" href="/docupipe/projects/{{ project_id }}">取消</a>
    </div>
  </form>
</div>
<script src="/static/js/task_form.js"></script>
{% endblock %}
```

- [ ] **Step 2: 写 `task_form.js`（加载凭证下拉、编辑回填、提交）**

```javascript
const root = document.querySelector("[data-project-id]");
const pid = root.dataset.projectId;
const tid = root.dataset.taskId;

(async function init() {
  // 凭证下拉
  const cr = await fetch(`/api/projects/${pid}/credentials`);
  const creds = await cr.json();
  const sel = document.querySelector('[name="credential_id"]');
  creds.forEach(c => {
    const o = document.createElement("option");
    o.value = c.id; o.textContent = `${c.name} (${c.corp_id})`;
    sel.appendChild(o);
  });
  // 编辑回填
  if (tid) {
    const r = await fetch(`/api/projects/${pid}/tasks/${tid}`);
    const t = await r.json();
    const f = document.getElementById("task-form");
    Object.entries(t).forEach(([k, v]) => {
      const el = f.elements[k];
      if (el && typeof v !== "object") el.value = v;
    });
    if (t.schedule_enabled === false) f.elements.schedule_enabled.checked = false;
    if (t.credential_id) sel.value = t.credential_id;
    f.elements.slug.readOnly = true; // 编辑时不改 slug
  }
})();

document.getElementById("task-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target;
  const body = Object.fromEntries(new FormData(f).entries());
  body.schedule_enabled = f.elements.schedule_enabled.checked;
  if (!body.credential_id) { delete body.credential_id; delete body.credential_type; }
  const url = tid ? `/api/projects/${pid}/tasks/${tid}` : `/api/projects/${pid}/tasks`;
  const method = tid ? "PUT" : "POST";
  const r = await fetch(url, {method, headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  if (r.ok) location.href = `/docupipe/projects/${pid}`;
  else { const j = await r.json(); alert(typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail)); }
});
```

- [ ] **Step 3: 补全 `project_detail.js` 的凭证/成员/运行 Tab**

凭证 device flow 三步：
```javascript
async function loadCredentials() {
  const r = await fetch(`/api/projects/${pid}/credentials`);
  const creds = await r.json();
  const box = document.getElementById("tab-credentials");
  box.innerHTML = `<button id="add-cred" class="btn btn-sm btn-primary mb-2">添加凭证（DWS device flow）</button>` +
    creds.map(c => `<div class="card p-3 flex justify-between"><span>${c.name} (${c.corp_id})</span><button class="btn btn-sm revoke" data-id="${c.id}">吊销</button></div>`).join("");
  // revoke
  box.querySelectorAll(".revoke").forEach(b => b.addEventListener("click", async () => {
    if (!confirm("吊销该凭证？")) return;
    await fetch(`/api/projects/${pid}/credentials/${b.dataset.id}`, {method: "DELETE"});
    loadCredentials();
  }));
  // add: device flow 三步（start → 弹窗显示 user_code → poll → finalize）
  document.getElementById("add-cred").addEventListener("click", async () => {
    const name = prompt("凭证名称");
    if (!name) return;
    const s = await fetch(`/api/projects/${pid}/credentials/device-login/start?name=${encodeURIComponent(name)}`, {method: "POST"});
    const start = await s.json();
    alert(`请访问：${start.verification_url}\n输入码：${start.user_code}`);
    const poll = async () => {
      const p = await fetch(`/api/projects/${pid}/credentials/device-login/poll?session_key=${start.session_key}`);
      const j = await p.json();
      if (j.status === "success") {
        await fetch(`/api/projects/${pid}/credentials/device-login/finalize`, {
          method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({session_key: start.session_key, name}),
        });
        loadCredentials();
      } else if (j.status === "pending") {
        setTimeout(poll, 3000);
      } else {
        alert("登录失败: " + (j.error || ""));
      }
    };
    setTimeout(poll, 3000);
  });
}
```

成员 Tab：
```javascript
async function loadMembers() {
  const r = await fetch(`/api/projects/${pid}/members`);
  const m = await r.json();
  const box = document.getElementById("tab-members");
  const proj = await (await fetch(`/api/projects/${pid}`)).json();
  const canManage = proj.is_owner;
  box.innerHTML = `
    <div class="card p-3 mb-2"><span class="font-semibold">${m.owner.username || "Owner"}</span> <span class="badge">Owner</span></div>
    ${m.members.map(x => `<div class="card p-3 flex justify-between"><span>${x.username || x.user_id}</span>${canManage ? `<button class="btn btn-sm rm" data-uid="${x.user_id}">移除</button>` : ""}</div>`).join("")}
    ${canManage ? '<div class="mt-2"><input id="m-uid" placeholder="user_id" class="input inline w-48"><button id="add-m" class="btn btn-sm btn-primary ml-2">添加成员</button></div>' : ""}
  `;
  if (canManage) {
    document.getElementById("add-m").addEventListener("click", async () => {
      const uid = document.getElementById("m-uid").value;
      const r = await fetch(`/api/projects/${pid}/members`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({user_id: uid})});
      if (r.ok) loadMembers(); else alert("添加失败");
    });
    box.querySelectorAll(".rm").forEach(b => b.addEventListener("click", async () => {
      await fetch(`/api/projects/${pid}/members/${b.dataset.uid}`, {method: "DELETE"}); loadMembers();
    }));
  }
}
```

运行历史 Tab：
```javascript
async function loadRuns() {
  const r = await fetch(`/api/runs?page_size=20`);
  const j = await r.json();
  const box = document.getElementById("tab-runs");
  box.innerHTML = j.runs.map(rn => `<div class="card p-3 text-sm flex justify-between"><span>${rn.status} · ${rn.trigger_type}</span><span class="text-gray-500">${rn.created_at}</span></div>`).join("") || '<p class="text-gray-500">无运行记录</p>';
}
```

- [ ] **Step 4: 提交**

```bash
git add docupipe_manager/templates/docupipe/task_form.html docupipe_manager/static/
git commit -m "feat(ui): 任务表单 + 凭证 device flow/成员/运行 Tab 交互"
```

---

## Task 13: main.py 装配与导航菜单

**Files:**
- Modify: `docupipe_manager/main.py`

**Interfaces:**
- Consumes: 所有新 router（Task 6-10）；`StaticFiles`（挂载 `/static`）。
- Produces: 应用启动后导航菜单对普通用户可见项目/运行；所有路由注册；static 目录挂载。

- [ ] **Step 1: 调整 `DOCUPIPE_NAV_MENU`**

把"管理"分组的 `require_admin` 去掉，所有登录用户可见：

```python
DOCUPIPE_NAV_MENU = [
    {"label": "账户", "items": [{"id": "account", "label": "我的账户", "href": "/account"}]},
    {"label": "DocuPipe", "items": [
        {"id": "projects", "label": "项目", "href": "/docupipe/projects"},
        {"id": "runs", "label": "运行", "href": "/docupipe/runs"},
    ]},
]
```

- [ ] **Step 2: 注册新路由 + 挂载 static**

```python
# main.py 路由注册段
from docupipe_manager.api.projects import router as projects_router, admin_router as admin_projects_router
from docupipe_manager.api.members import router as members_router
from docupipe_manager.api.credentials import router as credentials_router
from docupipe_manager.api.tasks import router as tasks_router
from docupipe_manager.api.runs import router as runs_router

app.include_router(admin_projects_router)
app.include_router(projects_router)
app.include_router(members_router)
app.include_router(credentials_router)
app.include_router(tasks_router)
app.include_router(runs_router)

# static 挂载（若未挂）
import os
_static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")
```

- [ ] **Step 3: 确认 lifespan 无需改**

`runner`/`scheduler`/`credential` 已在 Task 3-5 改为 task/project 驱动，构造函数签名不变，lifespan 无需改动。

- [ ] **Step 4: 启动应用验证无 import 错误**

Run: `uv run python -c "from docupipe_manager.main import app; print('ok')"`
Expected: 输出 `ok`（无 ImportError）

- [ ] **Step 5: 提交**

```bash
git add docupipe_manager/main.py
git commit -m "feat: 装配新路由 + 导航菜单对普通用户可见 + 挂载 static"
```

---

## Task 14: 全量验证

**Files:** 无新文件

**Interfaces:** N/A

- [ ] **Step 1: 跑全部单元/服务/API 测试**

Run: `pytest -v`
Expected: 全部 passed（`-m 'not integration'` 默认生效）

- [ ] **Step 2: 启动应用 + DB 迁移**

Run: `uv run alembic upgrade head` 然后 `uv run uvicorn docupipe_manager.main:app --reload`
Expected: 迁移成功，应用启动无报错

- [ ] **Step 3: 端到端手动流程**

以 admin 登录后验证：
1. 访问 `/docupipe/projects/new`，创建项目 → 跳转详情页。
2. 凭证 Tab：device flow 添加一张 DWS 凭证（需真实 dws CLI，可标 integration 跳过）。
3. 任务 Tab：新建任务，填 yaml + 选凭证 + cron，保存。
4. 成员 Tab：添加一个普通用户 user_id（从平台获取）。
5. 触发任务，运行历史 Tab 看到运行记录。
6. 归档项目，确认列表不再显示、调度取消。

以被添加的普通用户登录后验证：
1. 项目列表只看到被加入的项目。
2. 能编辑任务、触发运行，但成员 Tab 无"添加/移除"按钮。
3. 不能访问 `/admin/api/projects`（POST 创建）→ 403。

- [ ] **Step 4: 修复发现的问题并提交**

如有问题，回到相关 Task 修复后补提交。全部通过后：

```bash
git log --oneline  # 确认 14 个提交（或合并后）完整
```

---

## Self-Review

**1. Spec 覆盖**：逐条对照 spec：
- 数据模型 5 张表 → Task 1 ✓
- 权限模型（admin/Owner/Member + 3 依赖）→ Task 2 + Task 6 的 `_require_access_async`/`_require_owner_async` ✓
- 项目/成员/凭证/任务/运行 API → Task 6/7/8/9/10 ✓
- 服务层（Runner/Scheduler/Credential）→ Task 3/4/5 ✓
- UI（列表 + Tab 详情 + 任务表单）→ Task 11/12 ✓
- 迁移策略（drop + 重建）→ Task 1 Step 7 ✓
- 删除语义（软删除）→ Task 6 archive / Task 9 archive_task / Task 8 revoke ✓
- 本次仅 DWS → Task 1 枚举仅 dws；Task 4 runner 仅 dws 分支 ✓

**2. 占位符扫描**：Task 6 Step 1/2 有意保留了"占位说明 + 正式实现"对照，已在 Step 2 注释里明确删除占位。其余无 TBD/TODO。`loadCredentials`/`loadMembers`/`loadRuns` 在 Task 12 给了完整代码。

**3. 类型/命名一致性**：
- `Task.task_id` 在 runner/scheduler/runs API 一致 ✓
- `schedule_task`/`unschedule_task` 在 scheduler/tasks API/main 一致 ✓
- `_require_access_async`/`_require_owner_async` 在 members/credentials/tasks API 复用一致 ✓
- `project_id` 作为 path param 在 members/credentials/tasks router 一致 ✓

**已知偏离 spec**：
- spec 写 `POST /admin/api/projects/{id}` 删除路径，实现里 `archive_project` 放在 `admin_router`（`/admin/api/projects/{id}` DELETE）。需确认与 `require_project_owner` 一致——已在 Task 6 Step 1 用 `admin_router.delete` + `require_project_owner`。
- spec 成员 API 守卫：POST/DELETE owner，GET access——实现一致 ✓

无需新增任务。

