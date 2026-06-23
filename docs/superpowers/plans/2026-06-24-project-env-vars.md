# 项目环境变量功能 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为项目增加一组环境变量，在任务执行前合并进子进程 env，使任务配置文件可通过 `${VAR}` 引用。

**Architecture:** 新增项目级 `project_env_vars` 表（per-variable 可选 SM4 加密）；`RunnerService._do_execute` 加载并解密项目环境变量后合并进子进程 env（`{**os.environ, **project_env, "HOME": home_dir}`）；新增逐条 CRUD API 与项目详情「环境变量」Tab。

**Tech Stack:** FastAPI + SQLAlchemy 2.0（async）+ Alembic（raw SQL 迁移）+ Pydantic + Jinja2 + 原生 JS fetch。测试：pytest + pytest-asyncio + httpx ASGITransport。

**Spec:** `docs/superpowers/specs/2026-06-24-project-env-vars-design.md`

## Global Constraints

- Python ≥ 3.12；包管理用 `uv`，测试命令统一 `uv run pytest <path> -v`。
- 所有表位于 `docupipe_manager` schema；时间戳 `TIMESTAMPTZ`；主键 `UUID DEFAULT gen_random_uuid()`。
- ORM 用 SQLAlchemy 2.0 `Mapped`/`mapped_column` 风格（参考 `models/project.py`）。
- 迁移用**手写 raw SQL + `IF NOT EXISTS`** 幂等风格（参考 `migrations/versions/0001_initial_schema.py`），不用 autogenerate。
- API 权限统一用 `_require_access_async`（从 `docupipe_manager.api.projects` import），即 admin/Owner/Member 均可管理。
- 加密复用 `docupipe_manager.crypto` 的 `encrypt_sm4` / `decrypt_sm4`，key 来自 `Settings().encryption_key`（32 hex 字符）。
- `key` 命名正则 `^[A-Za-z_][A-Za-z0-9_]*$`；项目内 `UNIQUE(project_id, key)`。
- 提交信息用中文 + conventional 前缀（`feat:` / `test:` / `chore:`），参考 `git log --oneline`。
- 不加任何注释（除非用户要求）；遵循现有代码风格。

---

## File Structure

| 文件 | 职责 | 动作 |
|---|---|---|
| `docupipe_manager/models/project_env_var.py` | `ProjectEnvVar` ORM 模型 | 新建 |
| `docupipe_manager/models/__init__.py` | 注册模型导出 | 修改 |
| `docupipe_manager/migrations/versions/0003_add_project_env_vars.py` | 建表迁移 | 新建 |
| `docupipe_manager/api/env_vars.py` | 环境变量 CRUD 路由（含加密/脱敏/校验） | 新建 |
| `docupipe_manager/main.py` | 注册 `env_vars_router` | 修改 |
| `docupipe_manager/services/runner_service.py` | `_do_execute` 加载并注入 project env | 修改 |
| `docupipe_manager/templates/docupipe/project_detail.html` | 新增「环境变量」Tab 按钮 + panel | 修改 |
| `docupipe_manager/static/js/project_detail.js` | `loadEnvVars()` + 行内编辑器 | 修改 |
| `tests/unit/test_models.py` | `ProjectEnvVar` 映射断言 | 修改（追加） |
| `tests/api/test_env_vars.py` | API CRUD + 脱敏 + 校验测试 | 新建 |
| `tests/services/test_runner_service.py` | 注入 env 测试 + 适配现有测试 | 修改（追加+改） |

---

## Task 1: 数据模型 + 迁移

**Files:**
- Create: `docupipe_manager/models/project_env_var.py`
- Create: `docupipe_manager/migrations/versions/0003_add_project_env_vars.py`
- Modify: `docupipe_manager/models/__init__.py`
- Test: `tests/unit/test_models.py`

**Interfaces:**
- Consumes: `docupipe_manager.models.base.Base`
- Produces: `ProjectEnvVar`（`docupipe_manager.models.project_env_var.ProjectEnvVar`），表 `project_env_vars`；列：`id:UUID PK`, `project_id:UUID`, `key:str`, `value:str`, `is_secret:bool`, `description:str|None`, `created_by:UUID`, `created_at:datetime`, `updated_at:datetime`。后续任务均通过 `from docupipe_manager.models.project_env_var import ProjectEnvVar` 引用。

- [ ] **Step 1: 写失败测试（追加到 `tests/unit/test_models.py` 末尾）**

在文件顶部 import 区追加 `ProjectEnvVar`，并在文件末尾追加断言：

```python
# 顶部 import 追加（在现有 from ... import 行之后）：
from docupipe_manager.models.project_env_var import ProjectEnvVar


# 文件末尾追加：
def test_project_env_var_mapping():
    assert ProjectEnvVar.__tablename__ == "project_env_vars"
    cols = ProjectEnvVar.__table__.columns
    assert "id" in cols
    assert "project_id" in cols
    assert "key" in cols
    assert "value" in cols
    assert "is_secret" in cols
    assert "description" in cols
    assert "created_by" in cols
    assert "created_at" in cols
    assert "updated_at" in cols
    assert cols["is_secret"].default is not None  # 有默认值 false
```

- [ ] **Step 2: 运行测试验证失败**

Run: `uv run pytest tests/unit/test_models.py::test_project_env_var_mapping -v`
Expected: FAIL / ImportError —— `docupipe_manager.models.project_env_var` 不存在。

- [ ] **Step 3: 创建 ORM 模型**

Create `docupipe_manager/models/project_env_var.py`（完全对齐 `models/dws_credential.py` 风格）：

```python
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from docupipe_manager.models.base import Base

_SCHEMA = "docupipe_manager"


class ProjectEnvVar(Base):
    __tablename__ = "project_env_vars"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey(f"{_SCHEMA}.projects.id", ondelete="CASCADE"), nullable=False
    )
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```

- [ ] **Step 4: 注册到 `models/__init__.py`**

在 import 区末尾加一行，并在 `__all__` 列表加入 `"ProjectEnvVar"`：

```python
from docupipe_manager.models.project_env_var import ProjectEnvVar
```

`__all__` 追加 `"ProjectEnvVar"`（保持字母序，置于 `"ProjectMember"` 之后）。

- [ ] **Step 5: 运行测试验证通过**

Run: `uv run pytest tests/unit/test_models.py -v`
Expected: PASS（含新增 `test_project_env_var_mapping` 与原有全部用例）。

- [ ] **Step 6: 创建迁移文件**

Create `docupipe_manager/migrations/versions/0003_add_project_env_vars.py`（对齐 `0001` 的 raw SQL + `IF NOT EXISTS` 幂等风格）：

```python
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
```

- [ ] **Step 7: 提交**

```bash
git add docupipe_manager/models/project_env_var.py docupipe_manager/models/__init__.py docupipe_manager/migrations/versions/0003_add_project_env_vars.py tests/unit/test_models.py
git commit -m "feat: 新增 project_env_vars 模型与迁移"
```

---

## Task 2: API 层 CRUD（env_vars.py + 注册）

**Files:**
- Create: `docupipe_manager/api/env_vars.py`
- Modify: `docupipe_manager/main.py`
- Test: `tests/api/test_env_vars.py`

**Interfaces:**
- Consumes: `ProjectEnvVar`（Task 1）；`_require_access_async`、`_get_engine`（`docupipe_manager.api.projects`）；`encrypt_sm4`（`docupipe_manager.crypto`）；`Settings`（`docupipe_manager.config`）。
- Produces: `router`（`docupipe_manager.api.env_vars.router`），前缀 `/api/projects/{project_id}/env-vars`；端点：`GET ""`、`POST ""`、`PUT "/{var_id}"`、`DELETE "/{var_id}"`。响应字段：`id`/`key`/`value`/`is_secret`/`description`/`created_at`（列表对 secret 返回 `value=null`）。

- [ ] **Step 1: 写失败测试（新建 `tests/api/test_env_vars.py`）**

```python
"""Tests for project env var API endpoints."""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import override_get_current_user, clear_overrides


def _row(**kw):
    r = MagicMock()
    r.id = kw.get("id", uuid.uuid4())
    r.key = kw.get("key", "FOO")
    r.value = kw.get("value", "plain")
    r.is_secret = kw.get("is_secret", False)
    r.description = kw.get("description", None)
    r.created_at = kw.get("created_at", "2026-01-01T00:00:00+00:00")
    return r


def _mock_engine(rows=None, fetchone_row=None):
    mock_conn = AsyncMock()
    if rows is not None:
        mock_conn.execute.return_value = MagicMock(fetchall=MagicMock(return_value=rows))
    elif fetchone_row is not None:
        mock_conn.execute.return_value = MagicMock(
            fetchall=MagicMock(return_value=[]),
            fetchone=MagicMock(return_value=fetchone_row),
        )
    else:
        mock_conn.execute.return_value = MagicMock(
            fetchall=MagicMock(return_value=[]), fetchone=MagicMock(return_value=None)
        )
    mock_engine = MagicMock()
    mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
    return mock_engine


@pytest.mark.asyncio
async def test_list_masks_secret_value(async_client):
    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    pid = uuid.uuid4()
    rows = [_row(key="FOO", value="plain", is_secret=False),
            _row(key="BAR", value="enc", is_secret=True)]
    with patch("docupipe_manager.api.env_vars._require_access_async", new=AsyncMock(return_value={"role": "admin"})):
        with patch("docupipe_manager.api.env_vars._get_engine", return_value=_mock_engine(rows=rows)):
            r = await async_client.get(f"/api/projects/{pid}/env-vars")
            assert r.status_code == 200
            data = r.json()
            assert data[0]["value"] == "plain"
            assert data[1]["value"] is None  # secret 脱敏
            assert data[1]["is_secret"] is True
    clear_overrides()


@pytest.mark.asyncio
async def test_create_plain_var(async_client):
    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    pid = uuid.uuid4()
    with patch("docupipe_manager.api.env_vars._require_access_async", new=AsyncMock(return_value={"role": "admin"})):
        with patch("docupipe_manager.api.env_vars._get_engine", return_value=_mock_engine(fetchone_row=None)):
            r = await async_client.post(f"/api/projects/{pid}/env-vars",
                                        json={"key": "FOO", "value": "bar"})
            assert r.status_code == 200
            assert "id" in r.json()
    clear_overrides()


@pytest.mark.asyncio
async def test_create_invalid_key(async_client):
    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    pid = uuid.uuid4()
    with patch("docupipe_manager.api.env_vars._require_access_async", new=AsyncMock(return_value={"role": "admin"})):
        r = await async_client.post(f"/api/projects/{pid}/env-vars",
                                    json={"key": "1-bad", "value": "x"})
        assert r.status_code == 422
    clear_overrides()


@pytest.mark.asyncio
async def test_create_duplicate_key_conflict(async_client):
    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    pid = uuid.uuid4()
    existing = _row(key="FOO")
    with patch("docupipe_manager.api.env_vars._require_access_async", new=AsyncMock(return_value={"role": "admin"})):
        with patch("docupipe_manager.api.env_vars._get_engine", return_value=_mock_engine(fetchone_row=existing)):
            r = await async_client.post(f"/api/projects/{pid}/env-vars",
                                        json={"key": "FOO", "value": "x"})
            assert r.status_code == 409
    clear_overrides()


@pytest.mark.asyncio
async def test_update_secret_empty_value_keeps_original(async_client):
    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    pid = uuid.uuid4()
    var_id = uuid.uuid4()
    current = _row(id=var_id, key="BAR", value="ciphertext", is_secret=True)
    with patch("docupipe_manager.api.env_vars._require_access_async", new=AsyncMock(return_value={"role": "admin"})):
        with patch("docupipe_manager.api.env_vars._get_engine", return_value=_mock_engine(fetchone_row=current)):
            r = await async_client.put(f"/api/projects/{pid}/env-vars/{var_id}",
                                       json={"description": "new desc"})
            assert r.status_code == 200
    clear_overrides()


@pytest.mark.asyncio
async def test_delete_var(async_client):
    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    pid = uuid.uuid4()
    var_id = uuid.uuid4()
    existing = _row(id=var_id)
    with patch("docupipe_manager.api.env_vars._require_access_async", new=AsyncMock(return_value={"role": "admin"})):
        with patch("docupipe_manager.api.env_vars._get_engine", return_value=_mock_engine(fetchone_row=existing)):
            r = await async_client.delete(f"/api/projects/{pid}/env-vars/{var_id}")
            assert r.status_code == 200
    clear_overrides()
```

- [ ] **Step 2: 运行测试验证失败**

Run: `uv run pytest tests/api/test_env_vars.py -v`
Expected: FAIL —— `docupipe_manager.api.env_vars` 不存在（ImportError）。

- [ ] **Step 3: 实现 `api/env_vars.py`**

Create `docupipe_manager/api/env_vars.py`：

```python
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, insert, select, update

from docupipe_manager.api.projects import _get_engine, _require_access_async
from docupipe_manager.config import Settings
from docupipe_manager.crypto import encrypt_sm4
from docupipe_manager.models.project_env_var import ProjectEnvVar

router = APIRouter(prefix="/api/projects/{project_id}/env-vars", tags=["env-vars"])

_settings = Settings()

_KEY_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*$"


class CreateEnvVarRequest(BaseModel):
    key: str = Field(..., min_length=1, max_length=255, pattern=_KEY_PATTERN)
    value: str = Field(..., min_length=0)
    is_secret: bool = False
    description: Optional[str] = Field(None, max_length=255)


class UpdateEnvVarRequest(BaseModel):
    key: Optional[str] = Field(None, min_length=1, max_length=255, pattern=_KEY_PATTERN)
    value: Optional[str] = None
    description: Optional[str] = Field(None, max_length=255)


def _serialize(row, mask_secret: bool) -> dict:
    return {
        "id": str(row.id),
        "key": row.key,
        "value": None if (row.is_secret and mask_secret) else row.value,
        "is_secret": row.is_secret,
        "description": row.description,
        "created_at": str(row.created_at),
    }


@router.get("")
async def list_env_vars(project_id: uuid.UUID, user: dict = Depends(_require_access_async)):
    engine = _get_engine()
    async with engine.begin() as conn:
        rows = (await conn.execute(
            select(ProjectEnvVar).where(ProjectEnvVar.project_id == project_id)
            .order_by(ProjectEnvVar.key)
        )).fetchall()
    return [_serialize(r, mask_secret=True) for r in rows]


@router.post("")
async def create_env_var(project_id: uuid.UUID, body: CreateEnvVarRequest,
                         user: dict = Depends(_require_access_async)):
    engine = _get_engine()
    async with engine.begin() as conn:
        existing = (await conn.execute(
            select(ProjectEnvVar).where(
                ProjectEnvVar.project_id == project_id, ProjectEnvVar.key == body.key
            )
        )).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="变量名已存在")
        value = encrypt_sm4(body.value, _settings.encryption_key) if body.is_secret else body.value
        var_id = uuid.uuid4()
        await conn.execute(
            insert(ProjectEnvVar).values(
                id=var_id, project_id=project_id, key=body.key, value=value,
                is_secret=body.is_secret, description=body.description,
                created_by=uuid.UUID(user["id"]),
            )
        )
    return {"id": str(var_id)}


@router.put("/{var_id}")
async def update_env_var(project_id: uuid.UUID, var_id: uuid.UUID, body: UpdateEnvVarRequest,
                         user: dict = Depends(_require_access_async)):
    engine = _get_engine()
    async with engine.begin() as conn:
        current = (await conn.execute(
            select(ProjectEnvVar).where(
                ProjectEnvVar.id == var_id, ProjectEnvVar.project_id == project_id
            )
        )).fetchone()
        if current is None:
            raise HTTPException(status_code=404, detail="变量不存在")

        data = body.model_dump(exclude_unset=True)

        if "key" in data and data["key"] is not None and data["key"] != current.key:
            dup = (await conn.execute(
                select(ProjectEnvVar).where(
                    ProjectEnvVar.project_id == project_id,
                    ProjectEnvVar.key == data["key"],
                    ProjectEnvVar.id != var_id,
                )
            )).fetchone()
            if dup:
                raise HTTPException(status_code=409, detail="变量名已存在")

        if "value" in data:
            if current.is_secret:
                if data["value"]:
                    data["value"] = encrypt_sm4(data["value"], _settings.encryption_key)
                else:
                    data.pop("value")  # secret + 空 = 保持原值
            # 非 secret：data["value"] 为明文（含空串），保留

        if data:
            await conn.execute(
                update(ProjectEnvVar).where(ProjectEnvVar.id == var_id).values(**data)
            )
    return {"status": "updated"}


@router.delete("/{var_id}")
async def delete_env_var(project_id: uuid.UUID, var_id: uuid.UUID,
                         user: dict = Depends(_require_access_async)):
    engine = _get_engine()
    async with engine.begin() as conn:
        existing = (await conn.execute(
            select(ProjectEnvVar).where(
                ProjectEnvVar.id == var_id, ProjectEnvVar.project_id == project_id
            )
        )).fetchone()
        if existing is None:
            raise HTTPException(status_code=404, detail="变量不存在")
        await conn.execute(
            delete(ProjectEnvVar).where(ProjectEnvVar.id == var_id)
        )
    return {"status": "deleted"}
```

- [ ] **Step 4: 注册 router 到 `main.py`**

在 `main.py` 现有 router import 区（约 136-143 行）追加：

```python
from docupipe_manager.api.env_vars import router as env_vars_router
```

在 `app.include_router(...)` 区（约 145-153 行）追加：

```python
app.include_router(env_vars_router)
```

- [ ] **Step 5: 运行测试验证通过**

Run: `uv run pytest tests/api/test_env_vars.py -v`
Expected: 全部 PASS。

- [ ] **Step 6: 提交**

```bash
git add docupipe_manager/api/env_vars.py docupipe_manager/main.py tests/api/test_env_vars.py
git commit -m "feat: 新增项目环境变量 CRUD API"
```

---

## Task 3: Runner 注入项目环境变量

**Files:**
- Modify: `docupipe_manager/services/runner_service.py`（`_do_execute` 方法）
- Test: `tests/services/test_runner_service.py`（追加新测试 + 适配 3 个现有 `_do_execute` 测试的 session 0 mock）

**Interfaces:**
- Consumes: `ProjectEnvVar`（Task 1）；`decrypt_sm4`（已在 runner 顶部 import）；`self._settings.encryption_key`。
- Produces: `_do_execute` 在两处子进程的 env 增加 `**project_env`，顺序为 `{**os.environ, **project_env, "HOME": home_dir}`。解密失败 → 调 `_mark_run_failed` 并 return。

**关键改动点说明：** 现有 `_do_execute` 的第一个 session（session 0）当前只调用 `session.get(...)`。本任务在同一个 session 内**追加一次** `session.execute(select(ProjectEnvVar)...)` 查询项目环境变量。这会影响现有 3 个 `_do_execute` 端到端测试（它们 mock 的 `sessions[0]` 只有 `.get`，需补 `.execute` 返回空 env vars）。

- [ ] **Step 1: 适配现有测试 —— 给 session 0 补 execute mock（返回空 env vars）**

需修改 `tests/services/test_runner_service.py` 中 3 个端到端测试：
`test_do_execute_flushes_and_broadcasts_each_line`、`test_do_execute_truncates_log_file_at_max_bytes`、`test_do_execute_runs_without_credential`。

在每个测试里，设置 `sessions[0]` 的 `.get` 之后，**追加** `sessions[0].execute` 的 mock。先定义空结果 helper（放在文件顶部 import 之后）：

```python
def _empty_env_result():
    """session.execute(select(ProjectEnvVar)...) 的空结果 mock。"""
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    return result
```

然后在 3 个测试中，找到给 `sessions[0]` 设置 `.get` 的那行，**紧跟其后**追加：

```python
    sessions[0].execute = AsyncMock(return_value=_empty_env_result())
```

例如 `test_do_execute_flushes_and_broadcasts_each_line` 中：

```python
    sessions[0].get = AsyncMock(side_effect=[run_mock, task_mock, cred_mock])
    sessions[0].execute = AsyncMock(return_value=_empty_env_result())  # 新增
```

`test_do_execute_runs_without_credential` 中：

```python
    sessions[0].get = AsyncMock(side_effect=[run_mock, task_mock])
    sessions[0].execute = AsyncMock(return_value=_empty_env_result())  # 新增
```

- [ ] **Step 2: 先不改 runner，运行现有测试确认它们仍然通过（验证 mock 适配正确）**

Run: `uv run pytest tests/services/test_runner_service.py -v`
Expected: 现有全部 PASS（runner 还没加查询，mock 的 execute 不会被调用，但 mock 已就位不影响）。

> 说明：此步是「安全网」——确认 mock 适配本身不破坏现有行为，再进入实现。

- [ ] **Step 3: 写失败测试（追加到 `tests/services/test_runner_service.py` 末尾）**

```python
@pytest.mark.asyncio
async def test_do_execute_injects_project_env_into_subprocess(runner_service, tmp_path):
    """项目环境变量被合并进子进程 env；项目变量覆盖 os.environ；HOME 保持 home_dir。"""
    rid = uuid.uuid4()
    runner_service._active_runs.add(rid)
    task_id = uuid.uuid4()
    project_id = uuid.uuid4()

    run_mock = MagicMock()
    run_mock.id = rid
    run_mock.task_id = task_id
    run_mock.mode = "incremental"
    run_mock.pipeline_name = None
    task_mock = MagicMock()
    task_mock.id = task_id
    task_mock.project_id = project_id
    task_mock.credential_id = None
    task_mock.credential_type = None
    task_mock.config_yaml = "k: v"
    task_mock.slug = "demo"

    # 两个 env var：非 secret + secret（secret 值用 fixture 同款 key 加密）
    from docupipe_manager.crypto import encrypt_sm4
    plain = MagicMock()
    plain.is_secret = False
    plain.key = "MY_PLAIN"
    plain.value = "hello"
    secret = MagicMock()
    secret.is_secret = True
    secret.key = "MY_SECRET"
    secret.value = encrypt_sm4("topsecret", runner_service._settings.encryption_key)

    env_result = MagicMock()
    env_result.scalars.return_value.all.return_value = [plain, secret]

    sessions = [MagicMock(), MagicMock(), MagicMock(), MagicMock()]
    idx = {"i": 0}

    def fake_factory():
        s = sessions[idx["i"]]
        idx["i"] += 1
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=s)
        cm.__aexit__ = AsyncMock(return_value=None)
        return cm

    sessions[0].get = AsyncMock(side_effect=[run_mock, task_mock])
    sessions[0].execute = AsyncMock(return_value=env_result)
    sessions[1].execute = AsyncMock()
    sessions[1].commit = AsyncMock()
    sessions[2].execute = AsyncMock()
    sessions[2].commit = AsyncMock()
    sessions[3].execute = AsyncMock()
    sessions[3].commit = AsyncMock()
    runner_service._session_factory = fake_factory
    runner_service._settings.data_dir = str(tmp_path)

    home_dir = tmp_path / "home"

    with patch("docupipe_manager.services.runner_service.mkdtemp", return_value=str(home_dir)), \
         patch("asyncio.create_subprocess_exec", new=AsyncMock()) as mock_sub:
        proc = MagicMock()
        proc.stdout.readline = AsyncMock(side_effect=[b"ok\n", b""])
        proc.wait = AsyncMock(return_value=0)
        proc.pid = 999
        mock_sub.side_effect = [proc]

        await runner_service._do_execute(rid)

    assert mock_sub.call_count == 1
    env_passed = mock_sub.call_args.kwargs["env"]
    assert env_passed["MY_PLAIN"] == "hello"
    assert env_passed["MY_SECRET"] == "topsecret"  # secret 已解密
    assert env_passed["HOME"] == str(home_dir)     # HOME 保持 home_dir
```

> fixture 里 `settings.encryption_key = "0123456789abcdef0123456789abcdef"`，测试用同一 key 加密造数据，runner 解密能成功。

- [ ] **Step 4: 运行新测试验证失败**

Run: `uv run pytest tests/services/test_runner_service.py::test_do_execute_injects_project_env_into_subprocess -v`
Expected: FAIL —— runner 尚未注入 env，`env_passed["MY_PLAIN"]` KeyError 或断言失败。

- [ ] **Step 5: 实现 runner 注入**

修改 `docupipe_manager/services/runner_service.py`：

5a. 顶部 import 追加 `ProjectEnvVar` 与 `select`：

```python
from sqlalchemy import select, update
from docupipe_manager.models.project_env_var import ProjectEnvVar
```

（`update` 与 `select` 已在现有 import；只需确认 `select` 在，并把 `ProjectEnvVar` 加到现有 model import 区。当前文件已 import `select`（见第 12 行），故只需加 `ProjectEnvVar`。）

5b. 在 `_do_execute` 的第一个 `async with self._session_factory() as session:` 块内（当前读取 run/task/credential 之后、`config_yaml = task.config_yaml` 等局部变量提取处），**追加**查询项目环境变量，并在提取局部变量时一并带出 `env_var_rows`：

定位到现有这段（约 141-156 行）：

```python
            credential = None
            if task.credential_id is not None and task.credential_type is not None:
                ...
                if credential is None:
                    await self._mark_run_failed(run_id, "Credential not found")
                    return

            config_yaml = task.config_yaml
            slug = task.slug
            mode = run.mode
            pipeline_name = run.pipeline_name
            cred_type = task.credential_type
```

改为（在 `config_yaml = ...` 之前插入 env vars 查询，并把 `env_var_rows` 加入带出 session 的局部变量）：

```python
            credential = None
            if task.credential_id is not None and task.credential_type is not None:
                ...
                if credential is None:
                    await self._mark_run_failed(run_id, "Credential not found")
                    return

            env_var_rows = (await session.execute(
                select(ProjectEnvVar).where(ProjectEnvVar.project_id == task.project_id)
            )).scalars().all()

            config_yaml = task.config_yaml
            slug = task.slug
            mode = run.mode
            pipeline_name = run.pipeline_name
            cred_type = task.credential_type
```

5c. 在 session 块结束之后、`settings = self._settings` 之后，构建 `project_env` 字典（含解密与失败处理）。定位到现有 `settings = self._settings` 行（约 158 行），在其后插入：

```python
        settings = self._settings

        project_env: dict[str, str] = {}
        for ev in env_var_rows:
            if ev.is_secret:
                try:
                    ev_value = decrypt_sm4(ev.value, settings.encryption_key)
                except Exception:
                    await self._mark_run_failed(run_id, f"环境变量 {ev.key} 解密失败")
                    return
            else:
                ev_value = ev.value
            project_env[ev.key] = ev_value
```

5d. 修改两处子进程的 env（当前 `env={**os.environ, "HOME": home_dir}`）为：

```python
                env={**os.environ, **project_env, "HOME": home_dir},
```

两处都要改：
- dws `auth import` 子进程（约 184 行）
- docupipe `run` 子进程（约 215 行）

- [ ] **Step 6: 运行全部 runner 测试验证通过**

Run: `uv run pytest tests/services/test_runner_service.py -v`
Expected: 全部 PASS（含新增注入测试 + 3 个已适配的端到端测试）。

- [ ] **Step 7: 提交**

```bash
git add docupipe_manager/services/runner_service.py tests/services/test_runner_service.py
git commit -m "feat: runner 注入项目环境变量到子进程 env"
```

---

## Task 4: UI —— 项目详情「环境变量」Tab

**Files:**
- Modify: `docupipe_manager/templates/docupipe/project_detail.html`
- Modify: `docupipe_manager/static/js/project_detail.js`

**Interfaces:**
- Consumes: Task 2 的 API（`GET/POST/PUT/DELETE /api/projects/{pid}/env-vars`）；现有 `activateTab` / `statusTagClass` / Tab 结构。
- Produces: 项目详情页新增「环境变量」Tab，支持列表/新增/编辑/删除，secret 列表显示 `••••••`、编辑留空表示不改。

> 项目无前端自动化测试，本任务通过手动验证。

- [ ] **Step 1: 修改模板 `project_detail.html` —— 新增 Tab 按钮与 panel**

定位到 Tab 按钮区（约 44-49 行）：

```html
  <div class="tabs">
    <button class="tab tab-active" data-tab="tasks">任务</button>
    <button class="tab" data-tab="credentials">凭证</button>
    <button class="tab" data-tab="members">成员</button>
    <button class="tab" data-tab="runs">运行历史</button>
  </div>
  <div id="tab-tasks" class="tab-panel"></div>
  <div id="tab-credentials" class="tab-panel hidden"></div>
  <div id="tab-members" class="tab-panel hidden"></div>
  <div id="tab-runs" class="tab-panel hidden"></div>
```

在「运行历史」按钮后追加「环境变量」按钮，并在 panel 区追加对应 div：

```html
  <div class="tabs">
    <button class="tab tab-active" data-tab="tasks">任务</button>
    <button class="tab" data-tab="credentials">凭证</button>
    <button class="tab" data-tab="members">成员</button>
    <button class="tab" data-tab="runs">运行历史</button>
    <button class="tab" data-tab="env-vars">环境变量</button>
  </div>
  <div id="tab-tasks" class="tab-panel"></div>
  <div id="tab-credentials" class="tab-panel hidden"></div>
  <div id="tab-members" class="tab-panel hidden"></div>
  <div id="tab-runs" class="tab-panel hidden"></div>
  <div id="tab-env-vars" class="tab-panel hidden"></div>
```

- [ ] **Step 2: 修改 `project_detail.js` —— 新增 `loadEnvVars()` 并加入调用列表**

2a. 在文件末尾的调用行：

```javascript
loadProject(); loadTasks(); loadCredentials(); loadMembers(); loadRuns();
```

追加 `loadEnvVars()`：

```javascript
loadProject(); loadTasks(); loadCredentials(); loadMembers(); loadRuns(); loadEnvVars();
```

2b. 在 `loadRuns()` 函数定义之后、上述调用行之前，新增 `loadEnvVars()` 函数：

```javascript
async function loadEnvVars() {
  const box = document.getElementById("tab-env-vars");
  let html = '<div style="margin-bottom:10px"><button class="btn btn-sm btn-primary" id="env-add">新增变量</button></div>';
  html += '<div id="env-editor" class="hidden card" style="margin-bottom:10px"></div>';
  html += '<div id="env-list"></div>';
  box.innerHTML = html;
  document.getElementById("env-add").addEventListener("click", () => showEnvEditor(null));
  await refreshEnvList();
}

async function refreshEnvList() {
  const r = await fetch(`/api/projects/${pid}/env-vars`);
  const vars = await r.json();
  const list = document.getElementById("env-list");
  if (!vars.length) {
    list.innerHTML = '<div class="empty-state">暂无环境变量。</div>';
    return;
  }
  let html = '<table class="data-table"><thead><tr><th>变量名</th><th>值</th><th>类型</th><th>说明</th><th>操作</th></tr></thead><tbody>';
  for (const v of vars) {
    const valCell = v.is_secret ? '<span class="card-row-meta-inline">•••••• 🔒</span>' : `<code>${v.value || ""}</code>`;
    const typeTag = v.is_secret ? '<span class="status-tag">密钥</span>' : '<span class="card-row-meta-inline">普通</span>';
    html += `<tr>
      <td><code>${v.key}</code></td>
      <td>${valCell}</td>
      <td>${typeTag}</td>
      <td>${v.description || "—"}</td>
      <td class="action-cell">
        <button class="btn btn-sm btn-secondary env-edit" data-id="${v.id}">编辑</button>
        <button class="btn btn-sm btn-danger env-del" data-id="${v.id}">删除</button>
      </td>
    </tr>`;
  }
  html += '</tbody></table>';
  list.innerHTML = html;
  list.querySelectorAll(".env-edit").forEach(b => b.addEventListener("click", () => showEnvEditor(b.dataset.id)));
  list.querySelectorAll(".env-del").forEach(b => b.addEventListener("click", async () => {
    if (!confirm("确认删除该环境变量？")) return;
    const dr = await fetch(`/api/projects/${pid}/env-vars/${b.dataset.id}`, {method: "DELETE"});
    if (dr.ok) { refreshEnvList(); } else { alert("删除失败"); }
  }));
}

async function showEnvEditor(varId) {
  const editor = document.getElementById("env-editor");
  editor.classList.remove("hidden");
  let v = null;
  if (varId) {
    const r = await fetch(`/api/projects/${pid}/env-vars`);
    const all = await r.json();
    v = all.find(x => x.id === varId);
  }
  const isEdit = !!v;
  const valPlaceholder = (isEdit && v.is_secret) ? 'placeholder="留空表示不修改"' : 'placeholder="值"';
  const secretDisabled = isEdit ? 'disabled' : '';
  const secretChecked = (isEdit && v.is_secret) ? 'checked' : '';
  editor.innerHTML = `
    <h3>${isEdit ? "编辑环境变量" : "新增环境变量"}</h3>
    <div class="form-group"><label>变量名</label>
      <input id="env-key" class="form-control" value="${isEdit ? v.key : ""}" placeholder="如 MY_VAR" pattern="^[A-Za-z_][A-Za-z0-9_]*$"></div>
    <div class="form-group"><label>值</label>
      <input id="env-value" class="form-control" ${valPlaceholder}></div>
    <div class="form-group"><label><input type="checkbox" id="env-secret" ${secretChecked} ${secretDisabled}> 密钥（加密存储）</label></div>
    <div class="form-group"><label>说明（可选）</label>
      <input id="env-desc" class="form-control" value="${isEdit && v.description ? v.description : ""}"></div>
    <div class="form-actions">
      <button class="btn btn-sm btn-primary" id="env-save">保存</button>
      <button class="btn btn-sm btn-secondary" id="env-cancel">取消</button>
    </div>`;
  document.getElementById("env-cancel").addEventListener("click", () => editor.classList.add("hidden"));
  document.getElementById("env-save").addEventListener("click", async () => {
    const body = {
      key: document.getElementById("env-key").value.trim(),
      value: document.getElementById("env-value").value,
      is_secret: document.getElementById("env-secret").checked,
      description: document.getElementById("env-desc").value.trim() || null,
    };
    if (!body.key) { alert("变量名不能为空"); return; }
    if (!isEdit && !body.value && !body.is_secret) { alert("值不能为空"); return; }
    let r;
    if (isEdit) {
      const upd = {description: body.description};
      if (document.getElementById("env-key").value.trim() !== v.key) upd.key = body.key;
      if (document.getElementById("env-value").value) upd.value = body.value;
      r = await fetch(`/api/projects/${pid}/env-vars/${varId}`, {
        method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify(upd),
      });
    } else {
      r = await fetch(`/api/projects/${pid}/env-vars`, {
        method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body),
      });
    }
    if (r.ok) { editor.classList.add("hidden"); refreshEnvList(); }
    else { const j = await r.json(); alert(j.detail || "保存失败"); }
  });
}
```

- [ ] **Step 3: 手动验证**

启动应用（按项目既有方式，例如 `uv run uvicorn docupipe_manager.main:app`，具体以本地 `.env` 配置为准），登录后：

1. 进入任一项目详情页 `/docupipe/projects/{id}`。
2. 点击「环境变量」Tab，看到空态「暂无环境变量」。
3. 点「新增变量」：填 `MY_PLAIN`=`hello`（普通）、`MY_SECRET`=`topsecret`（勾选密钥）、说明可选 → 保存，列表出现两行。
4. 列表中 secret 行的值显示 `•••••• 🔒`。
5. 点「编辑」secret 行：值输入框为空 + placeholder「留空表示不修改」；只改说明、不动值 → 保存成功，值不变。
6. 点「编辑」非 secret 行：值正常回填，可改。
7. 点「删除」某行 → 确认后从列表消失。
8. （可选，验证注入）给项目的某个任务配置 yaml 引用 `${MY_PLAIN}`，触发运行，在运行日志确认变量生效。

- [ ] **Step 4: 提交**

```bash
git add docupipe_manager/templates/docupipe/project_detail.html docupipe_manager/static/js/project_detail.js
git commit -m "feat(ui): 项目详情新增环境变量 Tab"
```

---

## 完成验证

全部任务完成后，运行完整测试套件确认无回归：

```bash
uv run pytest -v
```

Expected: 全部 PASS（除默认跳过的 `integration` 标记测试）。
