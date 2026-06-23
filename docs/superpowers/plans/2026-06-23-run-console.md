# 运行控制台（虚拟控制台）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让任务触发后能在独立详情页以 SSE 实时流看到启动命令行与 stdout/stderr 逐行输出，运行中刷新或事后打开都能看完整日志。

**Architecture:** RunnerService 增加内存日志总线（`_active_runs` 活性集合 + `_log_buffers` 环形缓冲 + `_subscribers` 订阅者 Queue）；`_do_execute` 的 readline 循环每行 flush 落盘并广播；新增 SSE 端点 `/api/runs/{id}/stream` 按 meta→历史行→实时行→end 序列推送；新增详情页路由 + 模板 + JS 订阅 EventSource；触发后自动跳转。`PipelineRun` 增 `command_text` 字段持久化启动命令。

**Tech Stack:** FastAPI（StreamingResponse + SSE）、SQLAlchemy 2.x async、Alembic（raw SQL 幂等迁移）、Jinja2 模板、原生 JS + EventSource、pytest + unittest.mock。

## Global Constraints

- 测试运行命令：`uv run pytest`（项目用 uv，见 `uv.lock`/`pyproject.toml`；CI 无 DB，测试一律 mock engine，不真连 PG）。
- 迁移沿用 `docupipe_manager/migrations/versions/` 风格：raw SQL + `IF EXISTS/IF NOT EXISTS` 幂等，revision 递增。
- 前端无 JS 测试栈；JS 改动只做手动验收（见各任务验收步骤）。
- 复用 `docupipe_manager/static/css/docupipe.css` 既有 CSS 变量（`--bg`/`--surface`/`--text`/`--text-muted`/`--border`/`--primary`/`--radius` 等）与组件类（`status-tag`/`card-row`/`btn`/`stack`）。
- 提交信息沿用现有风格：`feat:`/`fix:` 前缀 + 简短描述。
- SSE 单行载荷用 `json.dumps(line)` 编码，前端 `JSON.parse(e.data)` 还原，杜绝换行/特殊字符破坏 SSE 帧。
- 进程重启时 `main.py` lifespan 会把 pending/running 标记为 failed（既有行为），故 SSE 的文件回退分支覆盖所有"非本进程 active"场景。

参考设计：`docs/superpowers/specs/2026-06-23-run-console-design.md`

---

### Task 1: PipelineRun 增加 command_text 字段 + 迁移

**Files:**
- Modify: `docupipe_manager/models/pipeline_run.py:50`（在 `log_path` 后新增字段）
- Create: `docupipe_manager/migrations/versions/0002_add_run_command_text.py`
- Test: `tests/unit/test_pipeline_run_model.py`（Create）

**Interfaces:**
- Consumes: 无
- Produces: `PipelineRun.command_text` 属性（`Mapped[str | None]`）；迁移 `0002`（revision="0002", down_revision="0001"）

- [ ] **Step 1: 写失败测试（验证 model 字段存在）**

Create `tests/unit/test_pipeline_run_model.py`:
```python
from docupipe_manager.models.pipeline_run import PipelineRun


def test_pipeline_run_has_command_text_column():
    cols = {c.name for c in PipelineRun.__table__.columns}
    assert "command_text" in cols


def test_pipeline_run_command_text_nullable():
    col = PipelineRun.__table__.columns["command_text"]
    assert col.nullable is True
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/unit/test_pipeline_run_model.py -v`
Expected: FAIL，`AssertionError: assert 'command_text' in {...}`

- [ ] **Step 3: 给 model 加字段**

Modify `docupipe_manager/models/pipeline_run.py`，在 `log_path` 字段（第 50 行）后插入：
```python
    command_text: Mapped[str | None] = mapped_column(String(1024), nullable=True)
```
（`String` 已在文件顶部 import）

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/unit/test_pipeline_run_model.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: 创建迁移文件**

Create `docupipe_manager/migrations/versions/0002_add_run_command_text.py`:
```python
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
```

- [ ] **Step 6: 验证迁移可被 alembic 识别**

Run: `uv run alembic -c alembic.ini history 2>/dev/null || uv run python -c "from alembic.config import Config; from alembic.script import ScriptDirectory; s=ScriptDirectory(Config('alembic.ini')); print([r.revision for r in s.walk_revisions()])"`
Expected: 输出包含 `0002` 与 `0001`。

- [ ] **Step 7: 提交**

```bash
git add docupipe_manager/models/pipeline_run.py \
        docupipe_manager/migrations/versions/0002_add_run_command_text.py \
        tests/unit/test_pipeline_run_model.py
git commit -m "feat: add command_text column to pipeline_runs"
```

---

### Task 2: RunnerService 内存日志总线 + flush 修复 + 活性管理

**Files:**
- Modify: `docupipe_manager/services/runner_service.py`（`__init__`、`_execute_run`、`_do_execute`；新增 `subscribe`/`unsubscribe`/`_broadcast`/`_close_subscribers`）
- Test: `tests/services/test_runner_service.py`（追加）

**Interfaces:**
- Consumes: Task 1 的 `PipelineRun.command_text`
- Produces:
  - `RunnerService.subscribe(run_id: uuid.UUID) -> tuple[list[str], asyncio.Queue]`：返回历史行副本 + 新建 Queue（已加入 `_subscribers`）
  - `RunnerService.unsubscribe(run_id: uuid.UUID, queue: asyncio.Queue) -> None`
  - `RunnerService.is_active(run_id: uuid.UUID) -> bool`：`run_id in self._active_runs`
  - 约定：订阅者 Queue 收到哨兵 `None` 表示流结束

- [ ] **Step 1: 写失败测试 —— subscribe 返回历史后收新行**

追加到 `tests/services/test_runner_service.py`:
```python
@pytest.mark.asyncio
async def test_subscribe_returns_buffer_then_live(runner_service):
    import asyncio as anyio  # 仅占位，实际用 runner_service 内事件循环
    rid = uuid.uuid4()
    runner_service._broadcast(rid, "old line 1")
    runner_service._broadcast(rid, "old line 2")
    history, queue = runner_service.subscribe(rid)
    assert history == ["old line 1", "old line 2"]
    runner_service._broadcast(rid, "live line")
    assert queue.get_nowait() == "live line"


@pytest.mark.asyncio
async def test_unsubscribe_stops_broadcast(runner_service):
    rid = uuid.uuid4()
    history, queue = runner_service.subscribe(rid)
    runner_service.unsubscribe(rid, queue)
    runner_service._broadcast(rid, "after")
    assert queue.empty()
    # subscribers 已清理
    assert rid not in runner_service._subscribers


def test_broadcast_drops_old_lines_beyond_maxlen(runner_service):
    rid = uuid.uuid4()
    for i in range(2500):
        runner_service._broadcast(rid, f"line {i}")
    # maxlen=2000，仅保留最后 2000 行
    history, _ = runner_service.subscribe(rid)
    assert len(history) == 2000
    assert history[0] == "line 500"
    assert history[-1] == "line 2499"


@pytest.mark.asyncio
async def test_close_subscribers_sends_sentinel_and_cleans(runner_service):
    rid = uuid.uuid4()
    _, queue = runner_service.subscribe(rid)
    runner_service._broadcast(rid, "x")
    await runner_service._close_subscribers(rid)
    # 先收到存量 x，再收到哨兵 None
    assert queue.get_nowait() == "x"
    assert queue.get_nowait() is None
    assert rid not in runner_service._subscribers
    assert rid not in runner_service._log_buffers
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/services/test_runner_service.py -k "subscribe or unsubscribe or broadcast or close_subscribers" -v`
Expected: FAIL（`AttributeError: 'RunnerService' object has no attribute '_broadcast'` 等）

- [ ] **Step 3: 实现 —— __init__ 加总线成员**

Modify `runner_service.py` 的 `__init__`，在 `self._semaphore = ...` 行后追加：
```python
        self._log_buffers: dict[uuid.UUID, "deque[str]"] = {}
        self._subscribers: dict[uuid.UUID, set[asyncio.Queue]] = {}
        self._active_runs: set[uuid.UUID] = set()
```
并在文件顶部 import 区（`import uuid` 已有）加：
```python
from collections import deque
```

- [ ] **Step 4: 实现 —— 新增 subscribe/unsubscribe/broadcast/close/is_active 方法**

在 `RunnerService` 类内（`start_run` 方法之前）插入：
```python
    def is_active(self, run_id: uuid.UUID) -> bool:
        return run_id in self._active_runs

    def subscribe(self, run_id: uuid.UUID) -> tuple[list[str], asyncio.Queue]:
        buffer = self._log_buffers.get(run_id)
        history = list(buffer) if buffer else []
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.setdefault(run_id, set()).add(queue)
        return history, queue

    def unsubscribe(self, run_id: uuid.UUID, queue: asyncio.Queue) -> None:
        subs = self._subscribers.get(run_id)
        if subs and queue in subs:
            subs.discard(queue)
            if not subs:
                self._subscribers.pop(run_id, None)

    def _broadcast(self, run_id: uuid.UUID, line: str) -> None:
        buffer = self._log_buffers.get(run_id)
        if buffer is None:
            buffer = deque(maxlen=2000)
            self._log_buffers[run_id] = buffer
        buffer.append(line)
        for q in list(self._subscribers.get(run_id, ())):
            q.put_nowait(line)

    async def _close_subscribers(self, run_id: uuid.UUID) -> None:
        for q in list(self._subscribers.get(run_id, ())):
            q.put_nowait(None)
        self._subscribers.pop(run_id, None)
        self._log_buffers.pop(run_id, None)
```

- [ ] **Step 5: 运行总线测试确认通过**

Run: `uv run pytest tests/services/test_runner_service.py -k "subscribe or unsubscribe or broadcast or close_subscribers" -v`
Expected: PASS（4 passed）

- [ ] **Step 6: 写失败测试 —— _execute_run 活性管理 + 结束清理**

追加到 `tests/services/test_runner_service.py`:
```python
@pytest.mark.asyncio
async def test_execute_run_marks_active_and_closes_on_success(runner_service):
    rid = uuid.uuid4()
    runner_service._do_execute = AsyncMock()
    _, queue = runner_service.subscribe(rid)  # 预先订阅
    await runner_service._execute_run(rid)
    assert not runner_service.is_active(rid)
    # 结束后订阅者收到哨兵
    assert queue.get_nowait() is None


@pytest.mark.asyncio
async def test_execute_run_closes_subscribers_on_exception(runner_service):
    rid = uuid.uuid4()
    runner_service._do_execute = AsyncMock(side_effect=RuntimeError("boom"))
    runner_service._mark_run_failed = AsyncMock()
    _, queue = runner_service.subscribe(rid)
    await runner_service._execute_run(rid)
    assert queue.get_nowait() is None
    assert not runner_service.is_active(rid)
```

- [ ] **Step 7: 运行测试确认失败**

Run: `uv run pytest tests/services/test_runner_service.py -k "execute_run" -v`
Expected: FAIL（`_execute_run` 当前不管理 active/close）

- [ ] **Step 8: 实现 —— _execute_run 加活性管理与结束清理**

Modify `_execute_run` 改为：
```python
    async def _execute_run(self, run_id: uuid.UUID) -> None:
        """Run the pipeline in a subprocess. Fire-and-forget."""
        self._active_runs.add(run_id)
        async with self._semaphore:
            try:
                await self._do_execute(run_id)
            except asyncio.CancelledError:
                logger.info("Run %s cancelled during shutdown", run_id)
                await self._mark_run_failed(run_id, "server shutdown")
                raise
            except Exception as e:
                logger.error("Run %s failed: %s", run_id, e)
                await self._mark_run_failed(run_id, str(e))
            finally:
                self._active_runs.discard(run_id)
                await self._close_subscribers(run_id)
```

- [ ] **Step 9: 运行测试确认通过**

Run: `uv run pytest tests/services/test_runner_service.py -k "execute_run" -v`
Expected: PASS（2 passed）

- [ ] **Step 10: 写失败测试 —— _do_execute 每行 flush + broadcast + 写 command_text**

追加到 `tests/services/test_runner_service.py`:
```python
@pytest.mark.asyncio
async def test_do_execute_flushes_and_broadcasts_each_line(runner_service, tmp_path):
    """端到端 mock：验证写文件 flush、广播、command_text 持久化。"""
    import asyncio
    rid = uuid.uuid4()
    task_id = uuid.uuid4()

    # --- 准备 run/task/credential mocks ---
    run_mock = MagicMock()
    run_mock.id = rid
    run_mock.task_id = task_id
    run_mock.mode = "incremental"
    run_mock.pipeline_name = None
    task_mock = MagicMock()
    task_mock.id = task_id
    task_mock.credential_id = uuid.uuid4()
    task_mock.credential_type = CredentialType.dws
    task_mock.config_yaml = "k: v"
    task_mock.slug = "demo"
    cred_mock = MagicMock()
    cred_mock.auth_blob = MagicMock()
    cred_mock.auth_blob.hex = "00" * 16

    sessions = [MagicMock(), MagicMock(), MagicMock()]  # _do_execute 内开 3 次 session
    idx = {"i": 0}

    def fake_factory():
        s = sessions[idx["i"]]; idx["i"] += 1
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=s)
        cm.__aexit__ = AsyncMock(return_value=None)
        return cm

    # session 0: get(run)->run_mock, get(task)->task_mock, get(cred)->cred_mock
    sessions[0].get = AsyncMock(side_effect=[run_mock, task_mock, cred_mock])
    # session 1: update running（标记 started_at + command_text）
    sessions[1].execute = AsyncMock()
    sessions[1].commit = AsyncMock()
    # session 2: update pid
    sessions[2].execute = AsyncMock()
    sessions[2].commit = AsyncMock()
    runner_service._session_factory = fake_factory

    # --- decrypt / subprocess mocks ---
    with patch("docupipe_manager.services.runner_service.decrypt_sm4", return_value=b"auth"), \
         patch("docupipe_manager.services.runner_service.mkdtemp", return_value=str(tmp_path)), \
         patch("asyncio.create_subprocess_exec", new=AsyncMock()) as mock_sub, \
         patch("docupipe_manager.services.runner_service.os.makedirs"):
        proc1 = MagicMock(); proc1.communicate = AsyncMock(return_value=(b"", b""))
        proc2 = MagicMock()
        # 模拟 stdout 两行后 EOF
        proc2.stdout.readline = AsyncMock(side_effect=[b"line A\n", b"line B\n", b""])
        proc2.wait = AsyncMock(return_value=0)
        proc2.pid = 12345
        mock_sub.side_effect = [proc1, proc2]

        _, queue = runner_service.subscribe(rid)
        await runner_service._do_execute(rid)

    # 广播了两行
    assert queue.get_nowait() == "line A"
    assert queue.get_nowait() == "line B"
    # command_text 写入了 session1 的 update（第二个 session）
    update_call = sessions[1].execute.call_args[0][0]
    # SQLAlchemy update 语句的 compile 取 text 太重，改为断言 command_text 在 values
    compiled = update_call.compile()
    assert "command_text" in str(compiled)
```

- [ ] **Step 11: 运行测试确认失败**

Run: `uv run pytest tests/services/test_runner_service.py -k "flushes_and_broadcasts" -v`
Expected: FAIL（当前 `_do_execute` 不 flush 也不 broadcast）

- [ ] **Step 12: 实现 —— _do_execute 改 readline 循环 + flush + broadcast + command_text**

Modify `docupipe_manager/services/runner_service.py`：

(a) 顶部 import 区加：
```python
import shlex
```

(b) 在 `cmd` 拼装完成（`if pipeline_name:` 块之后）加一行：
```python
            command_text = " ".join(shlex.quote(c) for c in cmd)
```

(c) 把标记 running 的 UPDATE（当前 `values(status=RunStatus.running, started_at=started_at, log_path=log_path)`）改为同时写 command_text：
```python
                await session.execute(
                    update(PipelineRun).where(PipelineRun.id == run_id).values(
                        status=RunStatus.running,
                        started_at=started_at,
                        log_path=log_path,
                        command_text=command_text,
                    )
                )
```

(d) 把 readline 循环（当前 `log_file.write(line.decode(...))` 段）替换为 flush + broadcast 版本：
```python
            with open(log_path, "w") as log_file:
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        break
                    text = line.decode("utf-8", errors="replace")
                    log_file.write(text)
                    log_file.flush()
                    self._broadcast(run_id, text.rstrip("\n"))
```

- [ ] **Step 13: 运行该测试确认通过**

Run: `uv run pytest tests/services/test_runner_service.py -k "flushes_and_broadcasts" -v`
Expected: PASS

- [ ] **Step 14: 跑 runner service 全量回归**

Run: `uv run pytest tests/services/test_runner_service.py -v`
Expected: 全部 PASS（含既有 `test_start_run_creates_run_with_task_id`、`test_cancel_pending_run`、`test_mark_run_failed`）

- [ ] **Step 15: 提交**

```bash
git add docupipe_manager/services/runner_service.py tests/services/test_runner_service.py
git commit -m "feat: add in-memory log bus, flush fix, command_text in runner"
```

---

### Task 3: SSE 端点 + run detail 查询增强

**Files:**
- Modify: `docupipe_manager/api/runs.py`（新增 `_run_detail`、`stream_run`；`get_run` 复用 `_run_detail`）
- Test: `tests/api/test_runs.py`（追加）

**Interfaces:**
- Consumes: Task 2 的 `RunnerService.subscribe`/`unsubscribe`/`is_active`
- Produces:
  - `GET /api/runs/{run_id}/stream` → `text/event-stream`，事件序列 `meta`→(`log`)*→`end`
  - `GET /api/runs/{run_id}` 返回新增字段：`command_text`、`task_name`、`project_id`

- [ ] **Step 1: 写失败测试 —— get_run 返回新字段**

追加到 `tests/api/test_runs.py`:
```python
@pytest.mark.asyncio
async def test_get_run_includes_command_and_task_name(async_client):
    rid = uuid.uuid4()
    run_mock = MagicMock()
    run_mock.id = rid
    run_mock.task_id = uuid.uuid4()
    run_mock.trigger_type = "manual"
    run_mock.triggered_by = None
    run_mock.pipeline_name = None
    run_mock.mode = "incremental"
    run_mock.status = "succeeded"
    run_mock.exit_code = 0
    run_mock.started_at = None
    run_mock.completed_at = None
    run_mock.command_text = "python -m docupipe run"
    run_mock.log_path = "/tmp/x.log"
    run_mock.error_message = None
    run_mock.created_at = "2026-06-23"
    task_mock = MagicMock()
    task_mock.name = "demo-task"
    task_mock.project_id = uuid.uuid4()

    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    with patch("docupipe_manager.main.app") as mock_app:
        mock_conn = AsyncMock()
        # _verify_run_access 查 run；_run_detail 再查 run + task
        mock_conn.execute = AsyncMock(side_effect=[
            MagicMock(scalar_one_or_none=MagicMock(return_value=run_mock)),  # access
            MagicMock(scalar_one_or_none=MagicMock(return_value=run_mock)),  # detail run
            MagicMock(scalar_one_or_none=MagicMock(return_value=task_mock)), # detail task
        ])
        mock_engine = MagicMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_app.state.engine = mock_engine

        r = await async_client.get(f"/api/runs/{rid}")
        assert r.status_code == 200
        data = r.json()
        assert data["command_text"] == "python -m docupipe run"
        assert data["task_name"] == "demo-task"
        assert "project_id" in data
    clear_overrides()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/api/test_runs.py::test_get_run_includes_command_and_task_name -v`
Expected: FAIL（KeyError: 'command_text'）

- [ ] **Step 3: 实现 —— 新增 _run_detail 并让 get_run 复用**

Modify `docupipe_manager/api/runs.py`：

(a) 顶部 import 区加：
```python
import asyncio
import json
```
并在 `from fastapi import ...` 行补 `Request`（若未引入），用于 SSE。

(b) 在 `get_run` 上方新增 `_run_detail`：
```python
async def _run_detail(run_id: uuid.UUID) -> dict:
    from sqlalchemy import select
    from docupipe_manager.models.pipeline_run import PipelineRun
    from docupipe_manager.models.task import Task

    engine = _get_engine()
    async with engine.begin() as conn:
        run = (await conn.execute(
            select(PipelineRun).where(PipelineRun.id == run_id)
        )).scalar_one_or_none()
        task = None
        if run is not None:
            task = (await conn.execute(
                select(Task).where(Task.id == run.task_id)
            )).scalar_one_or_none()

    if run is None:
        return {}

    def _v(x):
        return x.value if hasattr(x, "value") else x

    return {
        "id": str(run.id),
        "task_id": str(run.task_id),
        "task_name": task.name if task else None,
        "project_id": str(task.project_id) if task else None,
        "trigger_type": _v(run.trigger_type),
        "triggered_by": str(run.triggered_by) if run.triggered_by else None,
        "pipeline_name": run.pipeline_name,
        "mode": run.mode,
        "status": _v(run.status),
        "exit_code": run.exit_code,
        "command_text": run.command_text,
        "started_at": str(run.started_at) if run.started_at else None,
        "completed_at": str(run.completed_at) if run.completed_at else None,
        "error_message": run.error_message,
        "log_path": run.log_path,
        "created_at": str(run.created_at),
    }
```

(c) 把 `get_run` 的返回体替换为复用 `_run_detail`：
```python
@router.get("/{run_id}")
async def get_run(run_id: uuid.UUID, user: dict = Depends(get_current_user)):
    await _verify_run_access(run_id, user)
    detail = await _run_detail(run_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Run not found")
    return detail
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/api/test_runs.py::test_get_run_includes_command_and_task_name -v`
Expected: PASS

- [ ] **Step 5: 写失败测试 —— SSE 已完成 run 从文件读取**

追加到 `tests/api/test_runs.py`:
```python
@pytest.mark.asyncio
async def test_stream_completed_run_reads_file(async_client, tmp_path):
    rid = uuid.uuid4()
    log_file = tmp_path / "run.log"
    log_file.write_text("alpha\nbeta\n")

    run_mock = MagicMock()
    run_mock.id = rid
    run_mock.task_id = uuid.uuid4()
    run_mock.status = "succeeded"
    run_mock.exit_code = 0
    run_mock.command_text = "cmd"
    run_mock.started_at = None
    run_mock.completed_at = None
    run_mock.log_path = str(log_file)
    task_mock = MagicMock()
    task_mock.name = "t"
    task_mock.project_id = uuid.uuid4()

    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    with patch("docupipe_manager.main.app") as mock_app:
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(side_effect=[
            MagicMock(scalar_one_or_none=MagicMock(return_value=run_mock)),  # access
            MagicMock(scalar_one_or_none=MagicMock(return_value=run_mock)),  # detail(meta)
            MagicMock(scalar_one_or_none=MagicMock(return_value=task_mock)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=run_mock)),  # detail(end)
            MagicMock(scalar_one_or_none=MagicMock(return_value=task_mock)),
        ])
        mock_engine = MagicMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_app.state.engine = mock_engine
        runner = MagicMock()
        runner.is_active = MagicMock(return_value=False)
        mock_app.state.runner = runner

        r = await async_client.get(f"/api/runs/{rid}/stream")
        assert r.status_code == 200
        text = r.text
        assert "event: meta" in text
        assert '"alpha"' in text and '"beta"' in text
        assert "event: end" in text
    clear_overrides()
```

- [ ] **Step 6: 运行测试确认失败**

Run: `uv run pytest tests/api/test_runs.py::test_stream_completed_run_reads_file -v`
Expected: FAIL（404 或路由不存在）

- [ ] **Step 7: 实现 —— SSE 端点**

在 `docupipe_manager/api/runs.py`（`download_run_log` 之前）新增：
```python
def _sse(event: str, payload) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


@router.get("/{run_id}/stream")
async def stream_run(run_id: uuid.UUID, user: dict = Depends(get_current_user)):
    from fastapi.responses import StreamingResponse
    from docupipe_manager.main import app

    await _verify_run_access(run_id, user)
    runner = app.state.runner
    log_path = (await _run_detail(run_id)).get("log_path")

    async def event_stream():
        meta = await _run_detail(run_id)
        yield _sse("meta", meta)

        if runner.is_active(run_id):
            history, queue = runner.subscribe(run_id)
            try:
                for line in history:
                    yield _sse("log", line)
                while True:
                    try:
                        line = await asyncio.wait_for(queue.get(), timeout=15)
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    if line is None:  # sentinel
                        break
                    yield _sse("log", line)
            finally:
                runner.unsubscribe(run_id, queue)
        elif log_path:
            try:
                with open(log_path) as f:
                    for line in f:
                        yield _sse("log", line.rstrip("\n"))
            except FileNotFoundError:
                pass

        final = await _run_detail(run_id)
        yield _sse("end", {
            "status": final.get("status"),
            "exit_code": final.get("exit_code"),
        })

    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

- [ ] **Step 8: 运行测试确认通过**

Run: `uv run pytest tests/api/test_runs.py::test_stream_completed_run_reads_file -v`
Expected: PASS

- [ ] **Step 9: 跑 runs API 全量回归**

Run: `uv run pytest tests/api/test_runs.py -v`
Expected: 全部 PASS（含既有 `test_list_runs_*`、`test_get_run_not_found`、`test_cancel_run`）

- [ ] **Step 10: 提交**

```bash
git add docupipe_manager/api/runs.py tests/api/test_runs.py
git commit -m "feat: add SSE stream endpoint and run detail with task name"
```

---

### Task 4: 运行详情页（路由 + 模板 + JS + 控制台样式）

**Files:**
- Modify: `docupipe_manager/api/pages.py:64`（`runs_list` 后新增 `run_detail` 路由）
- Create: `docupipe_manager/templates/docupipe/runs/detail.html`
- Create: `docupipe_manager/static/js/run_detail.js`
- Modify: `docupipe_manager/static/css/docupipe.css`（追加控制台样式）

**Interfaces:**
- Consumes: Task 3 的 `GET /api/runs/{id}/stream`、`GET /api/runs/{id}`、`POST /api/runs/{id}/cancel`、`GET /api/runs/{id}/download-log`
- Produces: 页面 `GET /docupipe/runs/{run_id}`，模板注入 `run_id`

- [ ] **Step 1: 新增详情页路由**

Modify `docupipe_manager/api/pages.py`，在 `runs_list` 函数（第 64-66 行）后插入：
```python
@router.get("/runs/{run_id}")
async def run_detail(request: Request, run_id: str, user: dict = Depends(get_current_user)):
    return _render(request, "docupipe/runs/detail.html",
                   {"current_user": user, "run_id": run_id})
```

- [ ] **Step 2: 创建详情页模板**

Create `docupipe_manager/templates/docupipe/runs/detail.html`:
```html
{% extends "base.html" %}
{% block title %}运行详情{% endblock %}
{% block content %}
<div data-run-id="{{ run_id }}">
  <div class="content-header">
    <h2><span id="run-task-name">加载中...</span> <span id="run-status" class="status-tag"></span></h2>
    <div class="content-header-actions">
      <a href="#" id="run-back" class="btn btn-secondary">返回</a>
    </div>
  </div>

  <div class="card" style="margin-bottom:18px">
    <div class="run-meta-row"><span class="card-row-meta">启动命令</span></div>
    <pre id="run-command" class="run-command">—</pre>
    <div class="run-meta-grid">
      <div><span class="card-row-meta">退出码</span> <span id="run-exit-code">—</span></div>
      <div><span class="card-row-meta">开始</span> <span id="run-started-at">—</span></div>
      <div><span class="card-row-meta">结束</span> <span id="run-completed-at">—</span></div>
    </div>
    <div class="form-actions">
      <a id="run-download" class="btn btn-sm btn-secondary" href="#">下载日志</a>
      <button id="run-cancel" class="btn btn-sm btn-danger hidden">取消运行</button>
    </div>
  </div>

  <div class="console-toolbar">
    <span class="card-row-meta">控制台输出</span>
    <label class="check-row"><input type="checkbox" id="autoscroll" checked> 自动滚动</label>
  </div>
  <pre id="console" class="console"></pre>
</div>
<script src="/static/js/run_detail.js"></script>
{% endblock %}
```

- [ ] **Step 3: 追加控制台样式**

在 `docupipe_manager/static/css/docupipe.css` 末尾追加：
```css

/* ── 运行控制台 ── */
.run-command {
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    padding: 10px 12px;
    margin: 8px 0 14px;
    font-family: 'SF Mono', 'Fira Code', monospace;
    font-size: 12.5px;
    color: var(--text-secondary);
    white-space: pre-wrap;
    word-break: break-all;
}
.run-meta-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 12px;
    font-size: 13px;
    color: var(--text);
    margin-bottom: 12px;
}
.console-toolbar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 8px;
}
.console {
    background: #0b0e14;
    color: #d4d4d4;
    border-radius: var(--radius);
    padding: 14px 16px;
    font-family: 'SF Mono', 'Fira Code', monospace;
    font-size: 12.5px;
    line-height: 1.55;
    height: 60vh;
    overflow-y: auto;
    white-space: pre-wrap;
    word-break: break-word;
}
.console .log-line { display: block; }
.console .log-line.is-stderr { color: #ff8b8b; }
```

- [ ] **Step 4: 创建 SSE 客户端 JS**

Create `docupipe_manager/static/js/run_detail.js`:
```js
const runId = document.querySelector("[data-run-id]").dataset.runId;

function statusTagClass(status) {
  if (status === "succeeded" || status === "active" || status === "success") return "is-success";
  if (status === "failed" || status === "error" || status === "cancelled") return "is-failed";
  if (status === "running" || status === "pending") return "is-running";
  return "";
}

const consoleEl = document.getElementById("console");
const autoscrollEl = document.getElementById("autoscroll");

function appendLine(text) {
  const div = document.createElement("span");
  div.className = "log-line";
  div.textContent = text;  // textContent 自动 HTML 转义
  consoleEl.appendChild(div);
  if (autoscrollEl.checked) {
    consoleEl.scrollTop = consoleEl.scrollHeight;
  }
}

function renderMeta(m) {
  document.getElementById("run-task-name").textContent = m.task_name || "运行";
  const tag = document.getElementById("run-status");
  tag.textContent = m.status || "";
  tag.className = "status-tag " + statusTagClass(m.status);
  document.getElementById("run-command").textContent = m.command_text || "—";
  document.getElementById("run-exit-code").textContent =
    m.exit_code === null || m.exit_code === undefined ? "—" : m.exit_code;
  document.getElementById("run-started-at").textContent = m.started_at || "—";
  document.getElementById("run-completed-at").textContent = m.completed_at || "—";
  const dl = document.getElementById("run-download");
  dl.href = `/api/runs/${runId}/download-log`;
  const cancelBtn = document.getElementById("run-cancel");
  if (m.project_id) {
    document.getElementById("run-back").href = `/docupipe/projects/${m.project_id}`;
  }
  if (m.status === "running" || m.status === "pending") {
    cancelBtn.classList.remove("hidden");
    cancelBtn.onclick = async () => {
      if (!confirm("确认取消此运行？")) return;
      const r = await fetch(`/api/runs/${runId}/cancel`, {method: "POST"});
      if (!r.ok) alert("取消失败");
    };
  }
}

function finalize(end) {
  const tag = document.getElementById("run-status");
  tag.textContent = end.status || "";
  tag.className = "status-tag " + statusTagClass(end.status);
  document.getElementById("run-exit-code").textContent =
    end.exit_code === null || end.exit_code === undefined ? "—" : end.exit_code;
}

const es = new EventSource(`/api/runs/${runId}/stream`);
es.addEventListener("meta", e => renderMeta(JSON.parse(e.data)));
es.addEventListener("log", e => appendLine(JSON.parse(e.data)));
es.addEventListener("end", e => { finalize(JSON.parse(e.data)); es.close(); });
es.onerror = () => {
  const tag = document.getElementById("run-status");
  if (!tag.textContent) tag.textContent = "重连中…";
};
```

- [ ] **Step 5: 手动验收（启动应用）**

Run: `uv run uvicorn docupipe_manager.main:app --reload --port 8002`
验收：
1. 打开任一已完成运行的 `/docupipe/runs/{run_id}`，头部显示任务名/命令/状态/退出码，控制台显示完整历史日志。
2. 浏览器 Network 面板看到 `stream` 请求收到 `event: meta`、若干 `event: log`、`event: end` 后关闭。
3. 控制台 `<...>` 内容原样显示（未被当 HTML 解析）。

- [ ] **Step 6: 提交**

```bash
git add docupipe_manager/api/pages.py \
        docupipe_manager/templates/docupipe/runs/detail.html \
        docupipe_manager/static/js/run_detail.js \
        docupipe_manager/static/css/docupipe.css
git commit -m "feat: add run detail page with SSE console"
```

---

### Task 5: 触发后跳转 + 运行历史加查看链接

**Files:**
- Modify: `docupipe_manager/static/js/project_detail.js`（`loadTasks` 的触发按钮、`loadRuns` 的列表行）

**Interfaces:**
- Consumes: Task 4 的 `/docupipe/runs/{run_id}` 页面；`trigger` 端点返回 `{run_id, status}`（`api/tasks.py:195` 已有）

- [ ] **Step 1: 修改触发按钮为跳转**

Modify `docupipe_manager/static/js/project_detail.js`，把触发按钮的 click 处理（第 42-45 行）替换为：
```js
  box.querySelectorAll(".trigger").forEach(b => b.addEventListener("click", async () => {
    const r = await fetch(`/api/projects/${pid}/tasks/${b.dataset.id}/trigger`, {method: "POST", headers: {"Content-Type": "application/json"}, body: "{}"});
    if (r.ok) {
      const data = await r.json();
      location.href = `/docupipe/runs/${data.run_id}`;
    } else {
      alert("触发失败");
    }
  }));
```

- [ ] **Step 2: 运行历史每行加"查看"链接**

Modify `project_detail.js` 的 `loadRuns`，把 card-row 模板（第 216-228 行）替换为：
```js
  box.innerHTML = '<div class="stack">' +
    allRuns.slice(0, 50).map(run => `
      <a class="card-row" href="/docupipe/runs/${run.id}">
        <div class="card-row-main">
          <span class="card-row-title">${run.task_name}</span>
          <span class="card-row-meta-inline">${run.pipeline_name || "default"} · ${run.mode}</span>
        </div>
        <div class="card-row-actions">
          <span class="status-tag ${statusTagClass(run.status)}">${run.status}</span>
          <span class="card-row-meta-inline">${run.started_at ? new Date(run.started_at).toLocaleString() : ""}</span>
        </div>
      </a>`).join("") +
    '</div>';
```

- [ ] **Step 3: 端到端手动验收**

Run: `uv run uvicorn docupipe_manager.main:app --reload --port 8002`
验收：
1. 项目详情页点"触发" → 自动跳转 `/docupipe/runs/{id}`，控制台从第一行开始实时刷出。
2. 运行历史 tab 每行可点击 → 进入对应运行详情页。
3. 运行中刷新详情页 → 历史行重放 + 继续实时追加，无重复。
4. 运行结束 → 状态/退出码更新，自动滚动停止追加。

- [ ] **Step 4: 提交**

```bash
git add docupipe_manager/static/js/project_detail.js
git commit -m "feat: jump to run console on trigger and link runs list"
```

---

## Self-Review（计划作者已完成）

- **Spec 覆盖**：flush bug→Task 2 Step 12；命令行持久化→Task 1+2；SSE 实时流→Task 3；详情页→Task 4；触发跳转→Task 5；历史查看→Task 5。全部覆盖。
- **类型一致性**：`subscribe→tuple[list[str], Queue]`、`is_active→bool`、`_broadcast`/`_close_subscribers` 在 Task 2 定义，Task 3 消费，命名一致；哨兵 `None` 协议贯穿 Task 2/3。
- **无 placeholder**：每步含完整代码或确切命令。
- **顺序依赖**：1→2→3→4→5，每任务产出供下一任务消费的接口。
