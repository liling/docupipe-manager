# 凭证保活与 Job 下沉 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 抽出通用 `Job`（命令执行+输出捕获）实体，让 `PipelineRun` 引用 Job；新增凭证定时保活（作为一种 Job），通过现有 APScheduler 周期触发 `dws wiki space list` 刷新 token 并回写 DB。

**Architecture:** Job 是执行生命周期的唯一真相（status/pid/exit_code/log_path/command_text/trigger 等）。PipelineRun 瘦身为 `job_id` + task 绑定 + pipeline/mode。保活直接建 Job（kind=credential_keepalive + credential_id）。复用现有 `AsyncIOScheduler`，新增 keepalive job 类型。macOS 要求真实 HOME；本次仅复用 `_dws_lock` 做 interim 串行，进程级锁下次再做。

**Tech Stack:** Python 3, FastAPI, SQLAlchemy 2 (async), Alembic（手写幂等 raw SQL），APScheduler AsyncIOScheduler，pytest + pytest-asyncio。

## Global Constraints

- 所有表位于 `docupipe_manager` schema；迁移沿用项目手写幂等 raw SQL 风格（见 `migrations/versions/0005`），不依赖 autogenerate。
- 枚举值与旧 `run_status`/`run_trigger_type` 一致，仅改类名/类型名为 `Job*`。
- 共享 id：`jobs.id = pipeline_runs.id`（同一 UUID），关系 1:1，迁移用此零成本回填。
- dws 子进程一律**真实 HOME**（macOS 钥匙串要求，见 `credential_service.py:203-205`）；不建隔离 HOME。
- 测试沿用 `conftest.py` 的 `async_client`/`override_get_current_user`/service 层 mock `_session_factory` 模式；dws 子进程用 `patch asyncio.create_subprocess_exec`。
- 对外"运行"文案/路径/前端**不改**（`/api/runs`、运行记录页保持）；仅内部模型与表归一。
- 加密：`auth_blob` 存 `bytes.fromhex(encrypt_sm4(b64, key))`；解密 `decrypt_sm4(cred.auth_blob.hex(), key)`。
- **不要**引入进程级 dws 锁（非目标）。保活仅复用现有 `CredentialService._dws_lock`。

---

## File Structure

- **Create** `docupipe_manager/models/job.py` — Job 模型 + JobKind/JobStatus/JobTriggerType 枚举
- **Create** `docupipe_manager/migrations/versions/0006_create_jobs_and_backfill.py` — 建 jobs 表 + 枚举 + 回填 + pipeline_runs.job_id
- **Create** `docupipe_manager/migrations/versions/0007_drop_moved_run_columns.py` — 删除 pipeline_runs 已搬迁列
- **Modify** `docupipe_manager/models/__init__.py` — 导出 Job 系列
- **Modify** `docupipe_manager/models/pipeline_run.py` — 瘦身（Task 3）
- **Modify** `docupipe_manager/services/runner_service.py` — 写 Job、读 Job
- **Modify** `docupipe_manager/services/scheduler_service.py` — keepalive job 类型
- **Modify** `docupipe_manager/services/credential_service.py` — `refresh_credential` + `_run_dws`
- **Modify** `docupipe_manager/api/runs.py` — join jobs 读取
- **Modify** `docupipe_manager/api/credentials.py` — create/revoke 钩子
- **Modify** `docupipe_manager/main.py` — 启动 SQL 改打 jobs；SchedulerService 装配
- **Modify** `docupipe_manager/config.py` — keepalive 配置
- **Modify** `tests/unit/test_models.py` / `test_pipeline_run_model.py` / `tests/services/test_runner_service.py` / `test_credential_service.py` / `test_scheduler_service.py` / `tests/api/test_runs.py` / `test_credentials.py`

---

### Task 1: Job 模型 + 枚举 + 迁移 0006（建表+回填+job_id）

**Files:**
- Create: `docupipe_manager/models/job.py`
- Create: `docupipe_manager/migrations/versions/0006_create_jobs_and_backfill.py`
- Modify: `docupipe_manager/models/__init__.py`
- Test: `tests/unit/test_models.py`

**Interfaces:**
- Produces: `Job`, `JobKind`, `JobStatus`, `JobTriggerType`（值见下）。`JobStatus`/`JobTriggerType` 值与旧 `RunStatus`/`RunTriggerType` 完全相同。

- [ ] **Step 1: 写失败测试**

追加到 `tests/unit/test_models.py`：

```python
def test_job_model_has_required_columns():
    from docupipe_manager.models.job import Job
    cols = {c.name for c in Job.__table__.columns}
    assert {"id", "kind", "status", "pid", "exit_code", "started_at",
            "completed_at", "log_path", "command_text", "error_message",
            "trigger_type", "credential_id", "created_at"} <= cols


def test_job_kind_enum_values():
    from docupipe_manager.models.job import JobKind, JobStatus, JobTriggerType
    assert {k.value for k in JobKind} == {"docupipe_run", "credential_keepalive"}
    assert {k.value for k in JobStatus} == {"pending", "running", "succeeded", "failed", "cancelled"}
    assert {k.value for k in JobTriggerType} == {"manual", "scheduled"}


def test_job_credential_id_nullable():
    from docupipe_manager.models.job import Job
    assert Job.__table__.columns["credential_id"].nullable is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/unit/test_models.py -v`
Expected: FAIL — `No module named 'docupipe_manager.models.job'`

- [ ] **Step 3: 创建 Job 模型**

`docupipe_manager/models/job.py`：

```python
import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from docupipe_manager.models.base import Base

_SCHEMA = "docupipe_manager"


class JobKind(str, enum.Enum):
    docupipe_run = "docupipe_run"
    credential_keepalive = "credential_keepalive"


class JobStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class JobTriggerType(str, enum.Enum):
    manual = "manual"
    scheduled = "scheduled"


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    kind: Mapped[JobKind] = mapped_column(
        Enum(JobKind, name="job_kind", schema=_SCHEMA, create_constraint=True),
        nullable=False,
    )
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status", schema=_SCHEMA, create_constraint=True),
        default=JobStatus.pending, nullable=False,
    )
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    log_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    command_text: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    trigger_type: Mapped[JobTriggerType] = mapped_column(
        Enum(JobTriggerType, name="job_trigger_type", schema=_SCHEMA, create_constraint=True),
        nullable=False,
    )
    triggered_by: Mapped[uuid.UUID | None] = mapped_column(UUID, nullable=True)
    credential_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID, ForeignKey(f"{_SCHEMA}.dws_credentials.id", ondelete="SET NULL"), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

- [ ] **Step 4: 导出**

修改 `docupipe_manager/models/__init__.py`，在顶部 import 区加：

```python
from docupipe_manager.models.job import Job, JobKind, JobStatus, JobTriggerType
```

`__all__` 列表加入 `"Job"`, `"JobKind"`, `"JobStatus"`, `"JobTriggerType"`。

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/unit/test_models.py -v`
Expected: PASS

- [ ] **Step 6: 写迁移 0006**

`docupipe_manager/migrations/versions/0006_create_jobs_and_backfill.py`：

```python
"""Create jobs table, backfill from pipeline_runs, add pipeline_runs.job_id.

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-27
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
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
        "SELECT id, 'docupipe_run', status, pid, exit_code, started_at, completed_at, "
        "       log_path, command_text, error_message, trigger_type, triggered_by, created_at "
        "FROM docupipe_manager.pipeline_runs"
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
```

- [ ] **Step 7: 提交**

```bash
git add docupipe_manager/models/job.py docupipe_manager/models/__init__.py \
        docupipe_manager/migrations/versions/0006_create_jobs_and_backfill.py \
        tests/unit/test_models.py
git commit -m "feat: add Job model and migration 0006 (create+backfill+job_id)"
```

---

### Task 2: Runner 以 Job 为执行状态写入对象（双写期）

> 本任务让 runner 在创建/更新执行状态时**同时写 Job**（与现有 PipelineRun 写入并行）。读端仍读 PipelineRun（下一任务再切）。这是为保持每个 commit 绿的过渡双写，Task 4 删除 PipelineRun 侧。

**Files:**
- Modify: `docupipe_manager/services/runner_service.py`
- Test: `tests/services/test_runner_service.py`

**Interfaces:**
- Produces: `start_run` 创建 `Job(id=run.id, kind=docupipe_run, trigger_type=...)` + `PipelineRun(id=run.id, job_id=run.id, ...)`；`_stream_subprocess`/`_finalize_run`/`_mark_run_failed`/`cancel_run` 的 status/pid/exit_code/时间戳/log_path/command_text/error_message 同时更新到 Job。

- [ ] **Step 1: 写失败测试**

追加到 `tests/services/test_runner_service.py`：

```python
@pytest.mark.asyncio
async def test_start_run_creates_job_and_pipeline_run(runner_service):
    """start_run 同时创建 Job(共享 id) 和 PipelineRun(job_id=run.id)。"""
    from docupipe_manager.models.job import Job, JobKind, JobTriggerType
    task_id = uuid.uuid4()
    added = []
    with patch.object(runner_service, "_session_factory") as mock_sf:
        ms = AsyncMock(); ms.__aenter__.return_value = ms
        ms.add = MagicMock(side_effect=lambda obj: added.append(obj))
        ms.commit = AsyncMock(); ms.refresh = AsyncMock()
        mock_sf.return_value = ms
        with patch.object(runner_service, "_execute_run", new=AsyncMock()):
            run = await runner_service.start_run(
                task_id=task_id, trigger_type="manual", triggered_by=uuid.uuid4(),
            )
    kinds = [type(a).__name__ for a in added]
    assert "PipelineRun" in kinds and "Job" in kinds
    job = next(a for a in added if isinstance(a, Job))
    assert job.id == run.id                       # 共享 id
    assert job.kind == JobKind.docupipe_run
    assert job.trigger_type.value == "manual"
    run_obj = next(a for a in added if type(a).__name__ == "PipelineRun")
    assert run_obj.job_id == run.id
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/services/test_runner_service.py::test_start_run_creates_job_and_pipeline_run -v`
Expected: FAIL — Job 未被创建（added 里没有 Job）

- [ ] **Step 3: 改 start_run 创建 Job**

修改 `runner_service.py` 顶部 import：

```python
from docupipe_manager.models.job import Job, JobKind, JobStatus, JobTriggerType
from docupipe_manager.models.pipeline_run import PipelineRun, RunStatus
```

（保留 `RunStatus` 别名以最小化后续改动；本任务暂不改 PipelineRun 模型。）

改写 `start_run`（`runner_service.py:84`）：

```python
async def start_run(
    self,
    task_id: uuid.UUID,
    trigger_type: str,
    triggered_by: uuid.UUID | None,
    pipeline_name: str | None = None,
    mode: str = "incremental",
) -> PipelineRun:
    run_id = uuid.uuid4()
    job = Job(
        id=run_id,
        kind=JobKind.docupipe_run,
        status=JobStatus.pending,
        trigger_type=JobTriggerType(trigger_type),
        triggered_by=triggered_by,
        command_text=None,
    )
    run = PipelineRun(
        id=run_id,
        job_id=run_id,
        task_id=task_id,
        pipeline_name=pipeline_name,
        mode=mode,
        status=RunStatus.pending,
        trigger_type=trigger_type,
        triggered_by=triggered_by,
    )
    async with self._session_factory() as session:
        session.add(job)
        session.add(run)
        await session.commit()
        await session.refresh(run)

    asyncio.create_task(self._execute_run(run.id))
    return run
```

> 注：PipelineRun 现仍有 status/trigger_type 等列（Task 4 才删），这里继续写入以保持读端工作（双写）。

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/services/test_runner_service.py::test_start_run_creates_job_and_pipeline_run -v`
Expected: PASS

- [ ] **Step 5: 给 _stream_subprocess / _finalize_run / _mark_run_failed / cancel_run 加 Job 双写**

在 `_stream_subprocess`（`runner_service.py:236-240`）更新 pid 的 session 块里，追加一条对 Job 的 update：

```python
async with self._session_factory() as session:
    await session.execute(
        update(PipelineRun).where(PipelineRun.id == run_id).values(pid=proc.pid)
    )
    await session.execute(
        update(Job).where(Job.id == run_id).values(
            pid=proc.pid, status=JobStatus.running, started_at=datetime.now(timezone.utc),
        )
    )
    await session.commit()
```

在 `_finalize_run`（`runner_service.py:274-284`）的 update 块追加 Job：

```python
async with self._session_factory() as session:
    await session.execute(
        update(PipelineRun).where(PipelineRun.id == run_id).values(
            status=status, exit_code=exit_code, completed_at=completed_at,
            error_message=error_message, pid=None,
        )
    )
    await session.execute(
        update(Job).where(Job.id == run_id).values(
            status=JobStatus(status.value), exit_code=exit_code, completed_at=completed_at,
            error_message=error_message, pid=None, log_path=None,  # log_path 留待读端切后再迁移
        )
    )
    await session.commit()
```

在 `_do_execute` 里把 running 标记那块（`runner_service.py:322-331`）也补 Job 的 status=running + command_text + log_path：

```python
async with self._session_factory() as session:
    await session.execute(
        update(PipelineRun).where(PipelineRun.id == run_id).values(
            status=RunStatus.running, started_at=started_at,
            log_path=log_path, command_text=command_text,
        )
    )
    await session.execute(
        update(Job).where(Job.id == run_id).values(
            status=JobStatus.running, started_at=started_at,
            log_path=log_path, command_text=command_text,
        )
    )
    await session.commit()
```

`_mark_run_failed`（`runner_service.py:355-365`）追加 Job：

```python
async with self._session_factory() as session:
    await session.execute(
        update(PipelineRun).where(PipelineRun.id == run_id).values(
            status=RunStatus.failed, error_message=error_message[:2048],
            completed_at=datetime.now(timezone.utc),
        )
    )
    await session.execute(
        update(Job).where(Job.id == run_id).values(
            status=JobStatus.failed, error_message=error_message[:2048],
            completed_at=datetime.now(timezone.utc),
        )
    )
    await session.commit()
```

`cancel_run`（`runner_service.py:108-123`）：在改 PipelineRun.status 的同时改 Job.status：

```python
async def cancel_run(self, run_id: uuid.UUID) -> None:
    async with self._session_factory() as session:
        run = await session.get(PipelineRun, run_id)
        if run is None:
            raise ValueError("Run not found")
        if run.status == RunStatus.pending:
            run.status = RunStatus.cancelled
            await session.execute(
                update(Job).where(Job.id == run_id).values(status=JobStatus.cancelled)
            )
            await session.commit()
        elif run.status == RunStatus.running and run.pid:
            try:
                os.kill(run.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            run.status = RunStatus.cancelled
            await session.execute(
                update(Job).where(Job.id == run_id).values(status=JobStatus.cancelled, pid=None)
            )
            await session.commit()
```

> `update` 已在文件顶部从 sqlalchemy 导入。确认 `Job`/`JobStatus` 已在 import 中。

- [ ] **Step 6: 跑全部 runner 测试**

Run: `pytest tests/services/test_runner_service.py -v`
Expected: PASS（现有用例因双写仍读 PipelineRun，断言不变）

- [ ] **Step 7: 提交**

```bash
git add docupipe_manager/services/runner_service.py tests/services/test_runner_service.py
git commit -m "feat: runner dual-writes execution state to Job (transition)"
```

---

### Task 3: 读端切换到 Job（api/runs.py + main.py 启动 SQL）

**Files:**
- Modify: `docupipe_manager/api/runs.py`
- Modify: `docupipe_manager/main.py:44-49`
- Test: `tests/api/test_runs.py`, `tests/unit/test_pipeline_run_model.py`

**Interfaces:**
- Consumes: Job 表（执行字段）；PipelineRun（task_id/pipeline_name/mode/job_id）。
- 读端契约：执行状态字段（status/exit_code/command_text/log_path/started_at/completed_at/error_message/trigger_type/triggered_by/created_at）一律取自 join 到的 Job；task_id/pipeline_name/mode 取自 PipelineRun。

- [ ] **Step 1: 更新会失效的模型测试**

`test_pipeline_run_model.py` 当前断言 `command_text` 在 PipelineRun——Task 4 才真正删列，但读端切到 Job 后该测试与本任务无冲突；不过它断言的列将在 Task 4 删除。**本任务暂不改它**（列还在）。先聚焦 runs API。

写新失败测试——更新 `tests/api/test_runs.py` 的 `test_get_run_includes_command_and_task_name`，把执行字段从 run_mock 移到 job_mock。先看新读端契约：`_run_detail` 将 select `PipelineRun` join `Job`，返回单行带两者字段。

替换该测试为：

```python
@pytest.mark.asyncio
async def test_get_run_includes_command_and_task_name(async_client):
    rid = uuid.uuid4()
    job_mock = MagicMock()
    job_mock.id = rid
    job_mock.command_text = "python -m docupipe run"
    job_mock.log_path = "/tmp/x.log"
    job_mock.exit_code = 0
    job_mock.status = "succeeded"
    job_mock.started_at = None
    job_mock.completed_at = None
    job_mock.error_message = None
    job_mock.trigger_type = "manual"
    job_mock.triggered_by = None
    job_mock.created_at = "2026-06-23"
    run_mock = MagicMock()
    run_mock.id = rid
    run_mock.task_id = uuid.uuid4()
    run_mock.pipeline_name = None
    run_mock.mode = "incremental"
    task_mock = MagicMock()
    task_mock.name = "demo-task"
    task_mock.project_id = uuid.uuid4()

    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    with patch("docupipe_manager.deps.get_engine") as mock_get_engine:
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(side_effect=[
            MagicMock(one_or_none=MagicMock(return_value=run_mock)),  # access
            MagicMock(one_or_none=MagicMock(return_value=(run_mock, job_mock))),  # detail
            MagicMock(one_or_none=MagicMock(return_value=task_mock)),
        ])
        mock_engine = MagicMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_get_engine.return_value = mock_engine

        r = await async_client.get(f"/docupipe/api/runs/{rid}")
        assert r.status_code == 200
        data = r.json()
        assert data["command_text"] == "python -m docupipe run"
        assert data["task_name"] == "demo-task"
        assert "project_id" in data
    clear_overrides()
```

同理更新 `test_stream_completed_run_reads_file`、`test_stream_active_run_replays_history_then_live_then_end`、`test_cancel_run`：access 查返回 run_mock（带 task_id）；detail 查返回 `(run_mock, job_mock)` 元组；其中 `job_mock.log_path`/`command_text`/`status`/`exit_code` 提供值。`test_list_runs_admin`/`test_list_runs_non_admin_empty`/`test_get_run_not_found` 用空/None，无需改结构。

> 工程师执行时：对每个 stream 测试，把原 `run_mock` 的执行字段复制到一个新 `job_mock`，detail 的 `one_or_none` 返回 `(run_mock, job_mock)`。

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/api/test_runs.py -v`
Expected: FAIL（detail 读到的不再是单个 run 对象）

- [ ] **Step 3: 改 _verify_run_access 返回 (run, job) 或足够信息**

`api/runs.py` 的 `_verify_run_access` 现返回 `run`（PipelineRun），多处用到 `run.task_id`、`run.log_path`。改：查 PipelineRun + join Job，返回 `(run, job)`。调用处取 `run.task_id` 与 `job.log_path`。

替换 `_verify_run_access`：

```python
async def _verify_run_access(run_id: uuid.UUID, user: dict):
    from sqlalchemy import select, text
    from docupipe_manager.models.pipeline_run import PipelineRun
    from docupipe_manager.models.job import Job

    engine = deps.get_engine()
    async with engine.begin() as conn:
        row = (await conn.execute(
            select(PipelineRun, Job).join(Job, PipelineRun.job_id == Job.id)
            .where(PipelineRun.id == run_id)
        )).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Run not found")
    run, job = row
    if user.get("role") != "admin":
        async with engine.begin() as conn:
            m = await conn.execute(text("""
                SELECT 1 FROM docupipe_manager.tasks t
                JOIN docupipe_manager.projects p ON p.id = t.project_id
                WHERE t.id = :tid AND p.id IN (
                    SELECT pm.project_id FROM docupipe_manager.project_members pm WHERE pm.user_id = :uid
                )
            """), {"tid": str(run.task_id), "uid": user["id"]})
            if not m.fetchone():
                raise HTTPException(status_code=404, detail="Run not found")
    return run, job
```

- [ ] **Step 4: 改 _run_detail 读 Job**

```python
async def _run_detail(run_id: uuid.UUID) -> dict:
    from sqlalchemy import select
    from docupipe_manager.models.pipeline_run import PipelineRun
    from docupipe_manager.models.job import Job
    from docupipe_manager.models.task import Task

    engine = deps.get_engine()
    async with engine.begin() as conn:
        row = (await conn.execute(
            select(PipelineRun, Job).join(Job, PipelineRun.job_id == Job.id)
            .where(PipelineRun.id == run_id)
        )).one_or_none()
        task = None
        if row is not None:
            run, job = row
            task = (await conn.execute(
                select(Task).where(Task.id == run.task_id)
            )).one_or_none()

    if row is None:
        return {}
    run, job = row

    def _v(x):
        return x.value if hasattr(x, "value") else x

    return {
        "id": str(run.id),
        "task_id": str(run.task_id),
        "task_name": task.name if task else None,
        "project_id": str(task.project_id) if task else None,
        "trigger_type": _v(job.trigger_type),
        "triggered_by": str(job.triggered_by) if job.triggered_by else None,
        "pipeline_name": run.pipeline_name,
        "mode": run.mode,
        "status": _v(job.status),
        "exit_code": job.exit_code,
        "command_text": job.command_text,
        "started_at": str(job.started_at) if job.started_at else None,
        "completed_at": str(job.completed_at) if job.completed_at else None,
        "error_message": job.error_message,
        "log_path": job.log_path,
        "created_at": str(job.created_at),
    }
```

- [ ] **Step 5: 改 list_runs 读 Job**

把 `list_runs` 的主查询改为 join Job，字段从 job 取：

```python
from docupipe_manager.models.job import Job
# ...
q = select(
    PipelineRun.id.label("id"),
    PipelineRun.task_id.label("task_id"),
    Task.name.label("task_name"),
    Project.id.label("proj_id"),
    Project.name.label("project_name"),
    PipelineRun.pipeline_name.label("pipeline_name"),
    PipelineRun.mode.label("mode"),
    Job.trigger_type.label("trigger_type"),
    Job.status.label("status"),
    Job.started_at.label("started_at"),
    Job.completed_at.label("completed_at"),
    Job.created_at.label("created_at"),
).select_from(PipelineRun).join(
    Job, PipelineRun.job_id == Job.id
).join(
    Task, PipelineRun.task_id == Task.id, isouter=not bool(project_id)
).join(
    Project, Task.project_id == Project.id, isouter=True
).order_by(Job.created_at.desc())
```

`conditions` 里的 `PipelineRun.status == status` 改为 `Job.status == status`。response 推导里字段名映射不变（r.status / r.trigger_type 等仍可用，因为打了 label）。

`count_q` 的 `select_from(PipelineRun)` 保持（计数 run 数）；条件中若用了 status 改为 join Job 计数——简单起见计数也 join：

```python
count_q = select(func.count()).select_from(PipelineRun).join(Job, PipelineRun.job_id == Job.id)
```

- [ ] **Step 6: 改 log/stream/download/cancel 端点用 job.log_path**

`get_run_log`、`download_run_log`、`stream_run` 中 `_verify_run_access` 现返回 `(run, job)`：

```python
run, job = await _verify_run_access(run_id, user)
# 用 job.log_path 替代 run.log_path
```

`stream_run` 内 `meta = await _run_detail(run_id)` 已返回含 log_path 的 dict，无需改。

`cancel_run` 端点：`await _verify_run_access` 解包为 `(run, job)`，其余不变。

- [ ] **Step 7: 改 main.py 启动 SQL 打 jobs**

`main.py:44-49` 改为：

```python
async with engine.begin() as conn:
    await conn.execute(text(
        f"UPDATE {settings.manager_schema}.jobs "
        "SET status='failed', error_message='process restart' "
        "WHERE status IN ('pending', 'running')"
    ))
```

- [ ] **Step 8: 跑 runs API 测试 + 全套**

Run: `pytest tests/api/test_runs.py tests/services/test_runner_service.py -v`
Expected: PASS

- [ ] **Step 9: 提交**

```bash
git add docupipe_manager/api/runs.py docupipe_manager/main.py tests/api/test_runs.py
git commit -m "refactor: run read-path and startup SQL switch to Job as source of truth"
```

---

### Task 4: 删除 pipeline_runs 已搬迁列（迁移 0007 + 模型瘦身 + 清理双写）

**Files:**
- Create: `docupipe_manager/migrations/versions/0007_drop_moved_run_columns.py`
- Modify: `docupipe_manager/models/pipeline_run.py`
- Modify: `docupipe_manager/services/runner_service.py`（移除 PipelineRun 侧的执行字段写入）
- Modify: `tests/unit/test_pipeline_run_model.py`
- Modify: `docupipe_manager/models/__init__.py`（RunStatus/RunTriggerType 导出处理）

**Interfaces:**
- PipelineRun 最终字段：`id, job_id(FK→jobs, unique), task_id, pipeline_name, mode`。无 status/pid/exit_code/时间戳/log_path/command_text/error_message/trigger_type/triggered_by/created_at。

- [ ] **Step 1: 改模型测试反映最终结构**

`tests/unit/test_pipeline_run_model.py` 全文替换为：

```python
from docupipe_manager.models.pipeline_run import PipelineRun


def test_pipeline_run_keeps_task_binding_and_job_ref():
    cols = {c.name for c in PipelineRun.__table__.columns}
    assert {"id", "job_id", "task_id", "pipeline_name", "mode"} == cols


def test_pipeline_run_job_id_not_nullable():
    col = PipelineRun.__table__.columns["job_id"]
    assert col.nullable is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/unit/test_pipeline_run_model.py -v`
Expected: FAIL（command_text 等列还在）

- [ ] **Step 3: 写迁移 0007**

`docupipe_manager/migrations/versions/0007_drop_moved_run_columns.py`：

```python
"""Drop execution columns moved from pipeline_runs to jobs.

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


_DROPPED = ["status", "pid", "exit_code", "started_at", "completed_at",
            "log_path", "command_text", "error_message", "trigger_type",
            "triggered_by", "created_at"]


def upgrade() -> None:
    for col in _DROPPED:
        op.execute(f"ALTER TABLE docupipe_manager.pipeline_runs DROP COLUMN IF EXISTS {col}")
    # run_status / run_trigger_type 枚举类型已无表引用，清理
    op.execute("DROP TYPE IF EXISTS docupipe_manager.run_trigger_type")
    op.execute("DROP TYPE IF EXISTS docupipe_manager.run_status")


def downgrade() -> None:
    # 注意：downgrade 不恢复历史数据，仅恢复结构（值默认 pending/manual）
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
```

- [ ] **Step 4: 瘦身 PipelineRun 模型**

`docupipe_manager/models/pipeline_run.py` 全文替换为：

```python
import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from docupipe_manager.models.base import Base

_SCHEMA = "docupipe_manager"


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey(f"{_SCHEMA}.jobs.id", ondelete="CASCADE"), nullable=False, unique=True,
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey(f"{_SCHEMA}.tasks.id", ondelete="CASCADE"), nullable=False
    )
    pipeline_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
```

> 删除 `RunStatus`、`RunTriggerType` 枚举类。检查全局引用：`grep -rn "RunStatus\|RunTriggerType" docupipe_manager tests`。runner 用 `JobStatus` 替代（已在 Task 2 引入）。`models/__init__.py` 移除 `RunStatus, RunTriggerType` 导出。

- [ ] **Step 5: 清理 runner 的 PipelineRun 侧写入（去双写）**

在 `runner_service.py`：
- `start_run`：`PipelineRun(...)` 构造去掉 status/trigger_type/triggered_by（只留 id/job_id/task_id/pipeline_name/mode）。删去 `from ... import RunStatus`。
- `_stream_subprocess`、`_finalize_run`、`_mark_run_failed`、`_do_execute` 里所有 `update(PipelineRun)...` 执行字段写入**删除**（只留 Job 的 update）。
- `cancel_run`：改为读 Job 判定状态（不再依赖 PipelineRun.status）：

```python
async def cancel_run(self, run_id: uuid.UUID) -> None:
    async with self._session_factory() as session:
        job = await session.get(Job, run_id)
        if job is None:
            raise ValueError("Run not found")
        from docupipe_manager.models.job import JobStatus
        if job.status == JobStatus.pending:
            job.status = JobStatus.cancelled
            await session.commit()
        elif job.status == JobStatus.running and job.pid:
            try:
                os.kill(job.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            job.status = JobStatus.cancelled
            job.pid = None
            await session.commit()
```

- `_load_context` 里 `run.task_id` 仍可用（PipelineRun 保留 task_id）；其余不变。

- [ ] **Step 6: 修复 runner 测试里对 PipelineRun 执行字段的断言**

`test_runner_service.py` 现有测试（test_cancel_pending_run、test_mark_run_failed、test_start_run_creates_run_with_task_id 等）若断言 PipelineRun.status / 用 RunStatus，改为基于 Job/JobStatus。例：

```python
# from docupipe_manager.models.pipeline_run import RunStatus  → 改为
from docupipe_manager.models.job import JobStatus
```

`test_cancel_pending_run` 改为 mock `session.get(Job)` 返回 job_mock(job.status=pending)。`test_mark_run_failed` 断言 session.execute 被调用（更新 Job）。`test_start_run_creates_run_with_task_id` 断言 `run.task_id == task_id`（仍成立，PipelineRun 保留 task_id）。端到端 `_do_execute` 测试（test_do_execute_flushes...）mock 的 run_mock 需保留 task_id/mode/pipeline_name；sessions 数量因去掉 PipelineRun update 会减少——按实际调整 session mock 数量与断言（command_text 现在写入 Job 的 update）。

> 工程师执行时跑 `pytest tests/services/test_runner_service.py -v`，按失败信息逐个修 mock（核心：执行字段写入对象由 PipelineRun 改为 Job；session execute 次数相应减少）。

- [ ] **Step 7: 跑全套确认绿**

Run: `pytest -q`
Expected: PASS

- [ ] **Step 8: 提交**

```bash
git add docupipe_manager/models/pipeline_run.py docupipe_manager/models/__init__.py \
        docupipe_manager/migrations/versions/0007_drop_moved_run_columns.py \
        docupipe_manager/services/runner_service.py \
        tests/unit/test_pipeline_run_model.py tests/services/test_runner_service.py
git commit -m "refactor: slim PipelineRun to task binding; execution state lives on Job"
```

---

### Task 5: keepalive 配置项

**Files:**
- Modify: `docupipe_manager/config.py`
- Test: `tests/unit/test_models.py`（轻量，或新建 test_config）

**Interfaces:**
- Produces: `Settings.credential_keepalive_enabled: bool = True`、`Settings.credential_keepalive_cron: str = "0 3 * * *"`。

- [ ] **Step 1: 写失败测试**

追加到 `tests/unit/test_models.py`：

```python
def test_keepalive_config_defaults():
    from docupipe_manager.config import Settings
    import os
    s = Settings()
    assert s.credential_keepalive_enabled is True
    assert s.credential_keepalive_cron == "0 3 * * *"
```

- [ ] **Step 2: 跑确认失败**

Run: `pytest tests/unit/test_models.py::test_keepalive_config_defaults -v`
Expected: FAIL（属性不存在）

- [ ] **Step 3: 加配置**

`config.py` 在 `run_log_max_bytes` 之后加：

```python
    credential_keepalive_enabled: bool = True
    credential_keepalive_cron: str = "0 3 * * *"
```

- [ ] **Step 4: 跑确认通过 + 提交**

```bash
pytest tests/unit/test_models.py::test_keepalive_config_defaults -v
git add docupipe_manager/config.py tests/unit/test_models.py
git commit -m "feat: add credential keepalive config"
```

---

### Task 6: SchedulerService keepalive job 类型

**Files:**
- Modify: `docupipe_manager/services/scheduler_service.py`
- Modify: `docupipe_manager/main.py`（装配处给 SchedulerService 传 credential_service）
- Test: `tests/services/test_scheduler_service.py`

**Interfaces:**
- Consumes: `CredentialService`（构造注入，存 `self._credential`）；`settings.credential_keepalive_enabled` / `credential_keepalive_cron`。
- Produces: `schedule_keepalive(credential_id)`, `unschedule_keepalive(credential_id)`, `_scheduled_keepalive(credential_id)`。`_reload_all` 额外为每个 active 凭证注册 keepalive job。
- 构造签名变更：`SchedulerService(runner, credential_service, engine, settings)`。

- [ ] **Step 1: 写失败测试**

追加到 `tests/services/test_scheduler_service.py`：

```python
@pytest.fixture
def scheduler_service():
    runner = MagicMock()
    runner.start_run = AsyncMock()
    credential = MagicMock()
    credential.refresh_credential = AsyncMock()
    engine = MagicMock()
    settings = MagicMock()
    settings.credential_keepalive_enabled = True
    settings.credential_keepalive_cron = "0 3 * * *"
    return SchedulerService(runner, credential, engine, settings)


@pytest.mark.asyncio
async def test_schedule_keepalive_registers_job(scheduler_service):
    cid = uuid.uuid4()
    await scheduler_service.schedule_keepalive(cid)
    assert scheduler_service._scheduler.get_job(f"keepalive-{cid}") is not None


@pytest.mark.asyncio
async def test_unschedule_keepalive_removes_job(scheduler_service):
    cid = uuid.uuid4()
    scheduler_service._scheduler.add_job(lambda: None, "interval", seconds=60, id=f"keepalive-{cid}")
    await scheduler_service.unschedule_keepalive(cid)
    assert scheduler_service._scheduler.get_job(f"keepalive-{cid}") is None


@pytest.mark.asyncio
async def test_keepalive_disabled_does_not_register(scheduler_service):
    scheduler_service._settings.credential_keepalive_enabled = False
    cid = uuid.uuid4()
    await scheduler_service.schedule_keepalive(cid)
    assert scheduler_service._scheduler.get_job(f"keepalive-{cid}") is None


@pytest.mark.asyncio
async def test_scheduled_keepalive_calls_refresh(scheduler_service):
    from docupipe_manager.models.dws_credential import CredentialStatus
    cid = uuid.uuid4()
    cred_mock = MagicMock(); cred_mock.status = CredentialStatus.active
    with patch.object(scheduler_service, "_session_factory") as mock_sf:
        ms = AsyncMock(); ms.__aenter__.return_value = ms
        ms.get = AsyncMock(return_value=cred_mock)
        mock_sf.return_value = ms
        await scheduler_service._scheduled_keepalive(cid)
    scheduler_service._credential.refresh_credential.assert_awaited_once_with(cid)


@pytest.mark.asyncio
async def test_scheduled_keepalive_skips_inactive(scheduler_service):
    from docupipe_manager.models.dws_credential import CredentialStatus
    cid = uuid.uuid4()
    cred_mock = MagicMock(); cred_mock.status = CredentialStatus.revoked
    with patch.object(scheduler_service, "_session_factory") as mock_sf:
        ms = AsyncMock(); ms.__aenter__.return_value = ms
        ms.get = AsyncMock(return_value=cred_mock)
        mock_sf.return_value = ms
        await scheduler_service._scheduled_keepalive(cid)
    scheduler_service._credential.refresh_credential.assert_not_awaited()
```

> 注意：原 `scheduler_service` fixture 签名是 `(runner, engine, settings)`，需改为 `(runner, credential, engine, settings)`。原 4 个测试（test_schedule_task 等）不受影响。

- [ ] **Step 2: 跑确认失败**

Run: `pytest tests/services/test_scheduler_service.py -v`
Expected: FAIL（构造参数不符 / 方法不存在）

- [ ] **Step 3: 改 SchedulerService**

`scheduler_service.py` 修改：

构造函数：

```python
def __init__(self, runner, credential_service, engine, settings):
    self._runner = runner
    self._credential = credential_service
    self._engine = engine
    self._settings = settings
    self._session_factory = async_sessionmaker(engine, expire_on_commit=False)
    self._scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
```

import 顶部加：

```python
from docupipe_manager.models.dws_credential import CredentialStatus, DwsCredential
```

新增方法（放在 `unschedule_task` 之后）：

```python
async def schedule_keepalive(self, credential_id: uuid.UUID) -> None:
    """Register keepalive cron job for a credential (if enabled)."""
    if not self._settings.credential_keepalive_enabled:
        return
    job_id = f"keepalive-{credential_id}"
    try:
        self._scheduler.remove_job(job_id)
    except Exception:
        pass
    cron = self._settings.credential_keepalive_cron
    if not croniter.is_valid(cron):
        logger.warning("Invalid keepalive cron: %s", cron)
        return
    trigger = CronTrigger.from_crontab(cron)
    self._scheduler.add_job(
        self._scheduled_keepalive,
        trigger,
        args=[credential_id],
        id=job_id,
        replace_existing=True,
        name=f"keepalive-{credential_id}",
    )
    logger.info("Scheduled keepalive for credential %s", credential_id)

async def unschedule_keepalive(self, credential_id: uuid.UUID) -> None:
    job_id = f"keepalive-{credential_id}"
    try:
        self._scheduler.remove_job(job_id)
    except Exception:
        pass

async def _scheduled_keepalive(self, credential_id: uuid.UUID) -> None:
    async with self._session_factory() as session:
        cred = await session.get(DwsCredential, credential_id)
        if cred is None or cred.status != CredentialStatus.active:
            return
    await self._credential.refresh_credential(credential_id)
```

`_reload_all` 末尾追加（加载完 task job 之后）：

```python
        # keepalive jobs
        if self._settings.credential_keepalive_enabled:
            result = await session.execute(
                select(DwsCredential).where(DwsCredential.status == CredentialStatus.active)
            )
            for cred in result.scalars().all():
                await self.schedule_keepalive(cred.id)
```

> `_reload_all` 现有 session 块内已有 `select(Task)...`；把 keepalive 的 select 放在同一 session 内（复用 `session`），循环在 session 关闭后执行 schedule_keepalive。

- [ ] **Step 4: 改 main.py 装配**

`main.py:92`：

```python
scheduler = SchedulerService(runner, credential, engine, settings)
```

（`credential` 必须在 `scheduler` 之前实例化——把 `credential = CredentialService(...)` 行移到 `scheduler = ...` 之前。当前顺序是 runner→scheduler→credential，改为 runner→credential→scheduler。）

- [ ] **Step 5: 跑测试**

Run: `pytest tests/services/test_scheduler_service.py -v`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add docupipe_manager/services/scheduler_service.py docupipe_manager/main.py tests/services/test_scheduler_service.py
git commit -m "feat: SchedulerService manages credential keepalive jobs"
```

---

### Task 7: CredentialService.refresh_credential

**Files:**
- Modify: `docupipe_manager/services/credential_service.py`
- Test: `tests/services/test_credential_service.py`

**Interfaces:**
- Produces: `refresh_credential(credential_id: uuid.UUID) -> None`。语义：active 凭证才处理；建 Job(kind=credential_keepalive) 记录执行；真实 HOME 下 import→wiki space list→status→export→SM4 加密回写 cred（auth_blob/token_expires_at/refresh_token_expires_at/last_refreshed_at）；失败记 Job(failed)+审计，不改 cred.status。
- 复用 `_dws_lock`（interim 串行）。
- 新增私有 `_run_dws(args, log_path=None) -> (exit_code, stdout, stderr)`：统一 dws 子进程调用（真实 HOME，无超时或默认 60s）。

- [ ] **Step 1: 写失败测试**

追加到 `tests/services/test_credential_service.py`：

```python
@pytest.mark.asyncio
async def test_refresh_credential_success_writes_back(credential_service, tmp_path):
    from docupipe_manager.models.dws_credential import CredentialStatus
    cid = uuid.uuid4()
    cred = MagicMock()
    cred.id = cid
    cred.status = CredentialStatus.active
    cred.auth_blob = b"\x00\x01"
    cred.token_expires_at = None
    cred.refresh_token_expires_at = None

    # session 序列：0 读 cred；1 建 job；2 回写 cred+job(success)
    sessions = []
    for _ in range(3):
        ms = AsyncMock(); ms.__aenter__ = AsyncMock(return_value=ms); ms.__aexit__ = AsyncMock(return_value=None)
        ms.commit = AsyncMock()
        sessions.append(ms)
    idx = {"i": 0}
    def factory():
        s = sessions[idx["i"]]; idx["i"] += 1; return s
    sessions[0].get = AsyncMock(return_value=cred)
    sessions[1].add = MagicMock()
    sessions[2].get = AsyncMock(return_value=cred)
    credential_service._session_factory = factory
    credential_service._settings.data_dir = str(tmp_path)

    def fake_exec(*args, **kwargs):
        proc = AsyncMock()
        proc.returncode = 0
        if "status" in args:
            proc.communicate = AsyncMock(return_value=(b'{"corp_id":"c","expires_at":"2099-12-31T00:00:00Z","refresh_expires_at":"2099-12-31T00:00:00Z"}', b""))
        elif "export" in args:
            proc.communicate = AsyncMock(return_value=(b"", b""))
        else:
            proc.communicate = AsyncMock(return_value=(b"out", b""))
        return proc

    with patch("docupipe_manager.services.credential_service.decrypt_sm4", return_value="b64"), \
         patch("docupipe_manager.services.credential_service.encrypt_sm4", return_value="deadbeef"), \
         patch("docupipe_manager.services.credential_service.asyncio.create_subprocess_exec", side_effect=fake_exec), \
         patch("builtins.open", mock_open()), \
         patch("docupipe_manager.services.credential_service.os.path.exists", return_value=True), \
         patch("docupipe_manager.services.credential_service.os.makedirs"):
        await credential_service.refresh_credential(cid)

    assert cred.last_refreshed_at is not None
    assert cred.token_expires_at is not None


@pytest.mark.asyncio
async def test_refresh_credential_skips_inactive(credential_service):
    from docupipe_manager.models.dws_credential import CredentialStatus
    cid = uuid.uuid4()
    cred = MagicMock(); cred.status = CredentialStatus.revoked
    with patch.object(credential_service, "_session_factory") as mock_sf:
        ms = AsyncMock(); ms.__aenter__.return_value = ms
        ms.get = AsyncMock(return_value=cred)
        mock_sf.return_value = ms
        await credential_service.refresh_credential(cid)
    # 未建 job、未解密


@pytest.mark.asyncio
async def test_refresh_credential_api_failure_marks_job_failed(credential_service, tmp_path):
    from docupipe_manager.models.dws_credential import CredentialStatus
    from docupipe_manager.models.job import JobStatus
    cid = uuid.uuid4()
    cred = MagicMock(); cred.id = cid; cred.status = CredentialStatus.active; cred.auth_blob = b"\x00"
    sessions = []
    for _ in range(3):
        ms = AsyncMock(); ms.__aenter__ = AsyncMock(return_value=ms); ms.__aexit__ = AsyncMock(return_value=None)
        ms.commit = AsyncMock()
        sessions.append(ms)
    idx = {"i": 0}
    def factory():
        s = sessions[idx["i"]]; idx["i"] += 1; return s
    sessions[0].get = AsyncMock(return_value=cred)
    added = []
    sessions[1].add = MagicMock(side_effect=lambda o: added.append(o))
    sessions[2].execute = AsyncMock()  # 更新 job=failed
    credential_service._session_factory = factory
    credential_service._settings.data_dir = str(tmp_path)

    def fake_exec(*args, **kwargs):
        proc = AsyncMock(); proc.returncode = 1
        proc.communicate = AsyncMock(return_value=(b"", b"boom"))
        return proc

    with patch("docupipe_manager.services.credential_service.decrypt_sm4", return_value="b64"), \
         patch("docupipe_manager.services.credential_service.asyncio.create_subprocess_exec", side_effect=fake_exec), \
         patch("builtins.open", mock_open()), \
         patch("docupipe_manager.services.credential_service.os.makedirs"):
        await credential_service.refresh_credential(cid)

    # cred.status 未被改成 expired
    assert cred.status == CredentialStatus.active
    # 至少建了一个 job（kind=keepalive）
    from docupipe_manager.models.job import JobKind
    assert any(getattr(a, "kind", None) == JobKind.credential_keepalive for a in added)
```

- [ ] **Step 2: 跑确认失败**

Run: `pytest tests/services/test_credential_service.py -v`
Expected: FAIL（refresh_credential 不存在）

- [ ] **Step 3: 实现 _run_dws 与 refresh_credential**

在 `credential_service.py` 顶部 import 加：

```python
import tempfile
from sqlalchemy import update
from docupipe_manager.models.job import Job, JobKind, JobStatus, JobTriggerType
from docupipe_manager.models.dws_credential import CredentialStatus, DwsCredential
```

新增私有辅助（放在 `_probe_auth_blob` 之后）：

```python
async def _run_dws(self, args: list[str], log_path: str | None = None,
                   timeout: float = 120.0) -> tuple[int, bytes, bytes]:
    """跑一次 dws 子进程（真实 HOME）。返回 (exit_code, stdout, stderr)。
    可选把 stdout+stderr 追加写 log_path。"""
    proc = await asyncio.create_subprocess_exec(
        self._settings.dws_cli_path, *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise
    if log_path:
        try:
            with open(log_path, "a") as f:
                f.write(stdout.decode("utf-8", "replace"))
                f.write(stderr.decode("utf-8", "replace"))
        except OSError:
            pass
    return proc.returncode, stdout, stderr
```

新增 `refresh_credential`（放在 `check_status` 之后）：

```python
async def refresh_credential(self, credential_id: uuid.UUID) -> None:
    """定时保活：真实 HOME 调一次业务 API 触发刷新，回写 DB。
    失败仅记 Job(failed)+审计，不改 cred.status。"""
    async with self._session_factory() as session:
        cred = await session.get(DwsCredential, credential_id)
        if cred is None or cred.status != CredentialStatus.active:
            return
        key_hex = self._settings.encryption_key
        auth_b64 = decrypt_sm4(cred.auth_blob.hex(), key_hex)

    log_dir = os.path.join(self._settings.data_dir, "credentials",
                           str(credential_id), "jobs")
    job = Job(
        kind=JobKind.credential_keepalive,
        status=JobStatus.pending,
        trigger_type=JobTriggerType.scheduled,
        command_text="dws wiki space list",
        credential_id=credential_id,
    )
    async with self._session_factory() as session:
        session.add(job)
        await session.commit()
        await session.refresh(job)

    log_path = os.path.join(log_dir, f"{job.id}.log")
    os.makedirs(log_dir, exist_ok=True)
    started_at = datetime.now(timezone.utc)

    try:
        async with self._dws_lock:
            fd, tmp_import = tempfile.mkstemp(suffix=".b64", prefix="dws-keepalive-")
            os.close(fd)
            try:
                with open(tmp_import, "w") as f:
                    f.write(auth_b64)
                await self._run_dws(["auth", "logout"])
                rc, _, _ = await self._run_dws(["auth", "import", "--base64", "-i", tmp_import],
                                               log_path=log_path)
                if rc != 0:
                    raise RuntimeError(f"dws auth import failed (exit {rc})")

                async with self._session_factory() as session:
                    await session.execute(update(Job).where(Job.id == job.id).values(
                        status=JobStatus.running, started_at=started_at, log_path=log_path))
                    await session.commit()

                rc, _, _ = await self._run_dws(["wiki", "space", "list"], log_path=log_path)
                if rc != 0:
                    raise RuntimeError(f"dws wiki space list failed (exit {rc})")

                rc, status_out, _ = await self._run_dws(["auth", "status", "--format", "json"],
                                                        log_path=log_path)
                meta = json.loads(status_out.decode()) if status_out else {}

                fd2, tmp_export = tempfile.mkstemp(suffix=".b64", prefix="dws-keepalive-export-")
                os.close(fd2)
                rc, _, _ = await self._run_dws(["auth", "export", "--base64", "-o", tmp_export],
                                               log_path=log_path)
                if rc != 0 or not os.path.exists(tmp_export):
                    raise RuntimeError("dws auth export failed")
                with open(tmp_export, "r") as f:
                    new_blob = f.read().strip()
                os.unlink(tmp_export)
            finally:
                try:
                    os.unlink(tmp_import)
                except OSError:
                    pass
                await self._run_dws(["auth", "logout"])

        new_blob_hex = encrypt_sm4(new_blob, key_hex)
        token_exp = _parse_dt(meta.get("expires_at"))
        refresh_exp = _parse_dt(meta.get("refresh_expires_at"))
        async with self._session_factory() as session:
            cred = await session.get(DwsCredential, credential_id)
            cred.auth_blob = bytes.fromhex(new_blob_hex)
            if token_exp is not None:
                cred.token_expires_at = token_exp
            if refresh_exp is not None:
                cred.refresh_token_expires_at = refresh_exp
            cred.last_refreshed_at = datetime.now(timezone.utc)
            await session.execute(update(Job).where(Job.id == job.id).values(
                status=JobStatus.succeeded, exit_code=0,
                completed_at=datetime.now(timezone.utc), log_path=log_path))
            await session.commit()

        asyncio.create_task(self._platform_client.push_audit({
            "event": "docupipe.credential.refresh.success",
            "credential_id": str(credential_id), "job_id": str(job.id),
        }))
    except Exception as e:
        logger.warning("Keepalive failed for %s: %s", credential_id, e)
        try:
            async with self._session_factory() as session:
                await session.execute(update(Job).where(Job.id == job.id).values(
                    status=JobStatus.failed, error_message=str(e)[:2048],
                    completed_at=datetime.now(timezone.utc), log_path=log_path))
                await session.commit()
        except Exception:
            pass
        asyncio.create_task(self._platform_client.push_audit({
            "event": "docupipe.credential.refresh.fail",
            "credential_id": str(credential_id), "error": str(e)[:2048],
        }))
```

> `tempfile` 已在文件顶部 import（确认；若无需补 `import tempfile`）。`datetime`/`timezone`/`os`/`json` 已在顶部 import。

- [ ] **Step 4: 跑测试**

Run: `pytest tests/services/test_credential_service.py -v`
Expected: PASS（按失败信息微调 mock，如 session 序列数量）

- [ ] **Step 5: 提交**

```bash
git add docupipe_manager/services/credential_service.py tests/services/test_credential_service.py
git commit -m "feat: CredentialService.refresh_credential keepalive with Job records"
```

---

### Task 8: 凭证创建/吊销钩子 + 装配校验

**Files:**
- Modify: `docupipe_manager/api/credentials.py`
- Test: `tests/api/test_credentials.py`

**Interfaces:**
- Consumes: `deps.get_scheduler().schedule_keepalive` / `unschedule_keepalive`。
- 钩子：`create_from_import`/`finalize_login` 成功后调 `schedule_keepalive(cred.id)`；`revoke` 后调 `unschedule_keepalive(cred.id)`。

- [ ] **Step 1: 写失败测试**

追加到 `tests/api/test_credentials.py`：

```python
@pytest.mark.asyncio
async def test_import_credential_schedules_keepalive(async_client):
    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    pid = uuid.uuid4()
    mock_cred = MagicMock(); mock_cred.id = uuid.uuid4()
    with (
        patch("docupipe_manager.api.credentials._require_access_async", new=AsyncMock(return_value={"role": "admin"})),
        patch("docupipe_manager.deps.get_credential") as mock_get_credential,
        patch("docupipe_manager.deps.get_scheduler") as mock_get_scheduler,
    ):
        mock_get_credential.return_value.create_from_import = AsyncMock(return_value=mock_cred)
        sched = MagicMock(); sched.schedule_keepalive = AsyncMock()
        mock_get_scheduler.return_value = sched
        r = await async_client.post(
            f"/docupipe/api/projects/{pid}/credentials/import",
            json={"name": "imp", "auth_blob": "YWJj"},
        )
        assert r.status_code == 200
        sched.schedule_keepalive.assert_awaited_once_with(mock_cred.id)
    clear_overrides()


@pytest.mark.asyncio
async def test_revoke_credential_unschedules_keepalive(async_client):
    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    pid = uuid.uuid4(); cid = uuid.uuid4()
    with (
        patch("docupipe_manager.api.credentials._require_access_async", new=AsyncMock(return_value={"role": "admin"})),
        patch("docupipe_manager.deps.get_credential") as mock_get_credential,
        patch("docupipe_manager.deps.get_scheduler") as mock_get_scheduler,
    ):
        mock_get_credential.return_value.revoke = AsyncMock(return_value=None)
        sched = MagicMock(); sched.unschedule_keepalive = AsyncMock()
        mock_get_scheduler.return_value = sched
        r = await async_client.delete(f"/docupipe/api/projects/{pid}/credentials/{cid}")
        assert r.status_code == 200
        sched.unschedule_keepalive.assert_awaited_once_with(cid)
    clear_overrides()
```

- [ ] **Step 2: 跑确认失败**

Run: `pytest tests/api/test_credentials.py::test_import_credential_schedules_keepalive tests/api/test_credentials.py::test_revoke_credential_unschedules_keepalive -v`
Expected: FAIL（钩子未调）

- [ ] **Step 3: 加钩子**

`api/credentials.py` 的 `import_credential` 在 `return` 前加：

```python
    await deps.get_scheduler().schedule_keepalive(cred.id)
    return {"id": str(cred.id), "status": "active"}
```

`finalize_device_login` 同理在 `return` 前加：

```python
    await deps.get_scheduler().schedule_keepalive(cred.id)
    return {"id": str(cred.id), "status": "active"}
```

`revoke_credential` 在 `revoke(...)` 之后、`return` 之前加：

```python
        await deps.get_credential().revoke(credential_id, uuid.UUID(user["id"]), project_id)
        await deps.get_scheduler().unschedule_keepalive(credential_id)
        return {"status": "revoked"}
```

- [ ] **Step 4: 跑测试**

Run: `pytest tests/api/test_credentials.py -v`
Expected: PASS

- [ ] **Step 5: 全套回归**

Run: `pytest -q`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add docupipe_manager/api/credentials.py tests/api/test_credentials.py
git commit -m "feat: wire credential create/revoke to keepalive schedule hooks"
```

---

## Self-Review

**1. Spec coverage（对照 spec 各节）：**
- 数据模型 Job/枚举/共享 id → Task 1（建）、Task 4（PipelineRun 瘦身）。✓
- 迁移 0006 回填 + job_id → Task 1；0007 删列 → Task 4。✓（spec 写单迁移 0006，plan 拆 0006/0007 保绿，等价且更安全。）
- SchedulerService 泛化（schedule/unschedule/reload/_scheduled_keepalive + 注入 credential_service）→ Task 6。✓
- CredentialService.refresh_credential（真实 HOME、_dws_lock、Job 记录、失败不改 status）→ Task 7。✓
- Runner 适配 Job → Task 2/4。✓
- main.py 启动 SQL 改 jobs → Task 3。✓
- config 两项 → Task 5。✓
- 凭证 API 钩子（import/finalize/revoke）→ Task 8。✓
- 测试策略各层 → 每 Task 内嵌对应测试。✓
- 非目标（进程级锁/保活 UI/per-credential cron）→ 均未实现。✓

**2. Placeholder scan：** 无 TBD/TODO；refactor 步骤给出关键代码 + 明确指引（如 Task 4 Step 6 按 pytest 失败逐个修 mock）。runner mock 修整留给执行者按真实失败调整，因 mock 数量依赖实现细节，已说明原则。可接受。

**3. Type consistency：** `Job`/`JobKind`/`JobStatus`/`JobTriggerType` 全程一致；`SchedulerService(runner, credential_service, engine, settings)` 在 Task 6 定义、Task 6 Step 4 main.py 同步；`refresh_credential(credential_id)` 签名 Task 7 定义、Task 8 调用一致；`schedule_keepalive/unschedule_keepalive(credential_id)` Task 6 定义、Task 8 调用一致。`RunStatus` 在 Task 4 移除后，runner 测试 import 改 `JobStatus`（Task 4 Step 6 指明）。✓

**4. 已知执行期风险（写入 spec 风险节，执行时留意）：**
- `dws wiki space list` 是否真触发刷新需集成验证（Task 7 mock 无法验证）。
- 迁移 0006 回填依赖 pipeline_runs 现有列与 jobs 列名一一对应；上线前在 dump 上跑一次。
- Task 4 Step 6 runner 测试 mock 调整量较大，按实际 pytest 输出收敛。
