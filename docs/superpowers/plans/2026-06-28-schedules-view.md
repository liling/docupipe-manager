# 调度计划视图 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增一个 admin-only 只读「调度」页面，统一列出所有被配置为自动运行的调度（Task 调度 + Credential keepalive 调度），含下次执行时间，并暴露 keepalive 全局 cron 来源。

**Architecture:** 数据来源以 APScheduler 运行时状态为准（`scheduler.get_jobs()`，拿真实 `next_run_time` 含 jitter），用 DB 补充可读上下文（task/project/credential 名字）。`SchedulerService.list_schedules()` 做读取+enrich，`GET /api/schedules`（`require_admin`）做权限守卫与排序，`/docupipe/schedules` 页面渲染。

**Tech Stack:** FastAPI, SQLAlchemy (async), APScheduler 3.x (`AsyncIOScheduler`/`CronTrigger`), Jinja2 模板，pytest (asyncio)。

## Global Constraints

- API 路由前缀约定：router 用 `prefix="/api/schedules"`，main.py 用 `app.include_router(schedules_router, prefix="/docupipe")` → 完整路径 `/docupipe/api/schedules`（与 runs 等一致）。
- 权限：页面路由与 API 均用 `require_admin`（系统级视图，不按 project member 过滤）。
- 调度器时区固定 `Asia/Shanghai`（`SchedulerService.__init__` 已设）；`next_run_time` 序列化为 ISO 字符串。
- 复用现有 CSS 类：`data-table` / `status-tag` / `is-success` / `is-failed` / `is-running` / `empty-state` / `card-row-meta` / `members-header` / `content-header` / `btn`（来自共享 ui_common）。
- `Job` 表是执行记录（历史），不是本视图数据源；本视图只读 APScheduler + DB 配置，不写。
- 测试沿用 `tests/conftest.py` 的 `async_client` fixture、`override_get_current_user` / `clear_overrides`、`patch.object(scheduler_service, "_session_factory")` 模式。
- 无分页；排序按 `next_run_time` 升序（None 排末尾），在 API 层完成。

---

## File Structure

| 文件 | 责任 | 动作 |
|---|---|---|
| `docupipe_manager/services/scheduler_service.py` | 新增 `list_schedules()` + 模块级 `_cron_from_trigger()` helper | Modify |
| `tests/services/test_scheduler_service.py` | `list_schedules` 单测（enrich/registered/paused/未注册漂移） | Modify（追加） |
| `docupipe_manager/api/schedules.py` | `GET /api/schedules`，admin 守卫 + 排序 + 返回 keepalive cron | Create |
| `docupipe_manager/main.py` | include schedules router + nav_menu 加「调度」入口 | Modify |
| `tests/api/test_schedules.py` | API 测试（admin 返回全部/排序、非 admin 403） | Create |
| `docupipe_manager/api/pages.py` | `GET /docupipe/schedules` 页面路由（`require_admin`） | Modify |
| `docupipe_manager/templates/docupipe/schedules.html` | 调度列表页（摘要 + 表格 + 只读） | Create |
| `tests/api/test_pages_schedules.py` | 页面路由注册 + 模板存在性测试 | Create |

---

### Task 1: SchedulerService.list_schedules + cron 提取 helper

**Files:**
- Modify: `docupipe_manager/services/scheduler_service.py`（顶部 import + 文件末尾加 helper + 类内加方法）
- Test: `tests/services/test_scheduler_service.py`（追加测试）

**Interfaces:**
- Consumes: `self._scheduler.get_jobs()`（APScheduler），`self._session_factory`（DB），`self._settings.credential_keepalive_enabled` / `credential_keepalive_cron`
- Produces:
  - 模块级 `_cron_from_trigger(trigger) -> str`：从 `CronTrigger` 重建 5 字段 crontab 字符串
  - `SchedulerService.list_schedules(self) -> list[dict]`：返回统一 schedule 条目（见下形状），**未排序**

  条目形状：
  ```python
  {
    "kind": "task" | "keepalive",
    "scheduler_job_id": "task-<uuid>" | "keepalive-<uuid>" | None,  # None=未注册
    "name": "task-<slug>" | "keepalive-<credential_name>",
    "cron": "0 3 * * *",
    "next_run_time": "2026-06-29T03:00:00+08:00" | None,  # None=未注册或已暂停
    "context": { "task_id","task_name","project_id","project_name" }
    #   或 { "credential_id","credential_name","credential_status" }
    "config_enabled": bool,   # task: task.schedule_enabled; keepalive: 全局开关
    "registered": bool        # 是否在 APScheduler 里
  }
  ```

> **实现要点（已验证）：** APScheduler 的 `CronTrigger` 字段对象 `str(field)` 返回该字段表达式（如 `'*'`、`'3'`、`'0'`）。字段名含 `minute`/`hour`/`day`/`month`/`day_of_week`。**注意：** 单测里 scheduler 未 start，真实 job 无 `next_run_time` 属性 → 测试必须 mock `get_jobs()` 返回带 `next_run_time` 属性的假 job（trigger 用真 `CronTrigger`）。

- [ ] **Step 1: 写第一个失败测试（task + keepalive 两条已注册）**

追加到 `tests/services/test_scheduler_service.py` 末尾：

```python
@pytest.mark.asyncio
async def test_list_schedules_returns_task_and_keepalive(scheduler_service):
    from datetime import datetime, timezone, timedelta
    from apscheduler.triggers.cron import CronTrigger
    from docupipe_manager.models.project import Project
    from docupipe_manager.models.dws_credential import DwsCredential, CredentialStatus

    tz = timezone(timedelta(hours=8))
    tid, cid = uuid.uuid4(), uuid.uuid4()

    task_job = MagicMock()
    task_job.id = f"task-{tid}"
    task_job.trigger = CronTrigger.from_crontab("0 2 * * *")
    task_job.next_run_time = datetime(2026, 6, 29, 2, 0, tzinfo=tz)

    ka_job = MagicMock()
    ka_job.id = f"keepalive-{cid}"
    ka_job.trigger = CronTrigger.from_crontab("0 3 * * *")
    ka_job.next_run_time = datetime(2026, 6, 29, 3, 0, tzinfo=tz)

    scheduler_service._scheduler.get_jobs = MagicMock(return_value=[task_job, ka_job])

    task_mock = MagicMock(spec=Task)
    task_mock.id = tid
    task_mock.slug = "t1"
    task_mock.name = "Task One"
    task_mock.schedule_enabled = True
    task_mock.schedule_cron = "0 2 * * *"
    proj_mock = MagicMock(spec=Project)
    proj_mock.id = uuid.uuid4()
    proj_mock.name = "Proj A"

    cred_mock = MagicMock(spec=DwsCredential)
    cred_mock.id = cid
    cred_mock.name = "Cred One"
    cred_mock.status = CredentialStatus.active

    tresult = MagicMock()
    tresult.all.return_value = [(task_mock, proj_mock)]
    cresult = MagicMock()
    cresult.scalars.return_value = [cred_mock]

    with patch.object(scheduler_service, "_session_factory") as mock_sf:
        ms = AsyncMock()
        mock_sf.return_value.__aenter__.return_value = ms
        ms.execute = AsyncMock(side_effect=[tresult, cresult])
        items = await scheduler_service.list_schedules()

    by_kind = {it["kind"]: it for it in items}
    t = by_kind["task"]
    assert t["scheduler_job_id"] == f"task-{tid}"
    assert t["cron"] == "0 2 * * *"
    assert t["next_run_time"].startswith("2026-06-29T02:00")
    assert t["registered"] is True
    assert t["context"]["task_name"] == "Task One"
    assert t["context"]["project_name"] == "Proj A"
    k = by_kind["keepalive"]
    assert k["cron"] == "0 3 * * *"
    assert k["registered"] is True
    assert k["context"]["credential_name"] == "Cred One"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/pytest tests/services/test_scheduler_service.py::test_list_schedules_returns_task_and_keepalive -xvs`
Expected: FAIL（`AttributeError: 'SchedulerService' object has no attribute 'list_schedules'`）

- [ ] **Step 3: 加 import 并实现 helper + 方法**

在 `docupipe_manager/services/scheduler_service.py` 顶部 import 区加（`Task` import 之后）：

```python
from docupipe_manager.models.project import Project
```

在文件末尾（类定义之后）加模块级 helper：

```python
def _cron_from_trigger(trigger) -> str:
    """从 CronTrigger 重建标准 5 字段 crontab 字符串（minute hour day month day_of_week）。"""
    fields = {f.name: str(f) for f in trigger.fields}
    return f"{fields['minute']} {fields['hour']} {fields['day']} {fields['month']} {fields['day_of_week']}"
```

在 `SchedulerService` 类内（`_scheduled_run` 方法之后）加：

```python
    async def list_schedules(self) -> list[dict]:
        """读取 APScheduler 运行时状态 + DB enrich，返回统一 schedule 条目（未排序）。

        admin-only；权限守卫在 API 层。task 调度从 DB 配置枚举，
        与 APScheduler 交叉对账（registered），暴露配置了但未注册的漂移。
        """
        jobs = self._scheduler.get_jobs()
        task_jobs: dict[uuid.UUID, object] = {}
        keepalive_jobs: dict[uuid.UUID, object] = {}
        for job in jobs:
            if job.id.startswith("task-"):
                try:
                    task_jobs[uuid.UUID(job.id[len("task-"):])] = job
                except (ValueError, IndexError):
                    continue
            elif job.id.startswith("keepalive-"):
                try:
                    keepalive_jobs[uuid.UUID(job.id[len("keepalive-"):])] = job
                except (ValueError, IndexError):
                    continue

        items: list[dict] = []
        async with self._session_factory() as session:
            tresult = await session.execute(
                select(Task, Project)
                .join(Project, Task.project_id == Project.id)
                .where(
                    Task.status == TaskStatus.active,
                    Task.schedule_enabled.is_(True),
                    Task.schedule_cron.isnot(None),
                )
            )
            for task, project in tresult.all():
                job = task_jobs.get(task.id)
                items.append({
                    "kind": "task",
                    "scheduler_job_id": job.id if job else None,
                    "name": f"task-{task.slug}",
                    "cron": _cron_from_trigger(job.trigger) if job else task.schedule_cron,
                    "next_run_time": (job.next_run_time.isoformat()
                                      if job and job.next_run_time else None),
                    "context": {
                        "task_id": str(task.id),
                        "task_name": task.name,
                        "project_id": str(project.id),
                        "project_name": project.name,
                    },
                    "config_enabled": bool(task.schedule_enabled),
                    "registered": task.id in task_jobs,
                })

            if self._settings.credential_keepalive_enabled:
                cresult = await session.execute(
                    select(DwsCredential).where(DwsCredential.status == CredentialStatus.active)
                )
                for cred in cresult.scalars():
                    job = keepalive_jobs.get(cred.id)
                    status_val = cred.status.value if hasattr(cred.status, "value") else str(cred.status)
                    items.append({
                        "kind": "keepalive",
                        "scheduler_job_id": job.id if job else None,
                        "name": f"keepalive-{cred.name}",
                        "cron": _cron_from_trigger(job.trigger) if job
                                else self._settings.credential_keepalive_cron,
                        "next_run_time": (job.next_run_time.isoformat()
                                          if job and job.next_run_time else None),
                        "context": {
                            "credential_id": str(cred.id),
                            "credential_name": cred.name,
                            "credential_status": status_val,
                        },
                        "config_enabled": True,
                        "registered": cred.id in keepalive_jobs,
                    })
        return items
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/pytest tests/services/test_scheduler_service.py::test_list_schedules_returns_task_and_keepalive -xvs`
Expected: PASS

- [ ] **Step 5: 写边界测试（已暂停 + 未注册漂移）**

追加到 `tests/services/test_scheduler_service.py`：

```python
@pytest.mark.asyncio
async def test_list_schedules_paused_job_next_run_none(scheduler_service):
    """已暂停的 keepalive job（next_run_time=None）仍列出，next_run_time=None。"""
    from apscheduler.triggers.cron import CronTrigger
    from docupipe_manager.models.dws_credential import DwsCredential, CredentialStatus

    cid = uuid.uuid4()
    ka_job = MagicMock()
    ka_job.id = f"keepalive-{cid}"
    ka_job.trigger = CronTrigger.from_crontab("0 3 * * *")
    ka_job.next_run_time = None  # 暂停
    scheduler_service._scheduler.get_jobs = MagicMock(return_value=[ka_job])

    cred_mock = MagicMock(spec=DwsCredential)
    cred_mock.id = cid
    cred_mock.name = "Paused Cred"
    cred_mock.status = CredentialStatus.active

    tresult = MagicMock(); tresult.all.return_value = []   # 无 task
    cresult = MagicMock(); cresult.scalars.return_value = [cred_mock]

    with patch.object(scheduler_service, "_session_factory") as mock_sf:
        ms = AsyncMock()
        mock_sf.return_value.__aenter__.return_value = ms
        ms.execute = AsyncMock(side_effect=[tresult, cresult])
        items = await scheduler_service.list_schedules()

    assert len(items) == 1
    assert items[0]["kind"] == "keepalive"
    assert items[0]["registered"] is True
    assert items[0]["next_run_time"] is None
    assert items[0]["cron"] == "0 3 * * *"


@pytest.mark.asyncio
async def test_list_schedules_task_configured_but_not_registered(scheduler_service):
    """DB 里 schedule_enabled=True 但 APScheduler 无对应 job → registered=False（漂移检测）。"""
    from docupipe_manager.models.project import Project

    scheduler_service._scheduler.get_jobs = MagicMock(return_value=[])  # APScheduler 空

    tid = uuid.uuid4()
    task_mock = MagicMock(spec=Task)
    task_mock.id = tid
    task_mock.slug = "drift"
    task_mock.name = "Drift Task"
    task_mock.schedule_enabled = True
    task_mock.schedule_cron = "0 5 * * *"
    proj_mock = MagicMock(spec=Project)
    proj_mock.id = uuid.uuid4()
    proj_mock.name = "Proj B"

    tresult = MagicMock(); tresult.all.return_value = [(task_mock, proj_mock)]
    cresult = MagicMock(); cresult.scalars.return_value = []  # 无 active cred

    with patch.object(scheduler_service, "_session_factory") as mock_sf:
        ms = AsyncMock()
        mock_sf.return_value.__aenter__.return_value = ms
        ms.execute = AsyncMock(side_effect=[tresult, cresult])
        items = await scheduler_service.list_schedules()

    assert len(items) == 1
    t = items[0]
    assert t["registered"] is False
    assert t["scheduler_job_id"] is None
    assert t["next_run_time"] is None
    assert t["cron"] == "0 5 * * *"  # 取自 DB
    assert t["config_enabled"] is True


@pytest.mark.asyncio
async def test_list_schedules_keepalive_disabled_skips_credentials(scheduler_service):
    """credential_keepalive_enabled=False 时不查/不列 keepalive。"""
    scheduler_service._settings.credential_keepalive_enabled = False
    scheduler_service._scheduler.get_jobs = MagicMock(return_value=[])

    tresult = MagicMock(); tresult.all.return_value = []
    with patch.object(scheduler_service, "_session_factory") as mock_sf:
        ms = AsyncMock()
        mock_sf.return_value.__aenter__.return_value = ms
        ms.execute = AsyncMock(side_effect=[tresult])  # 只调一次（无 credential 查询）
        items = await scheduler_service.list_schedules()

    assert items == []
```

- [ ] **Step 6: 运行全部 scheduler 测试确认通过**

Run: `.venv/bin/pytest tests/services/test_scheduler_service.py -xvs`
Expected: 全部 PASS（含原有 + 新增）

- [ ] **Step 7: 提交**

```bash
git add docupipe_manager/services/scheduler_service.py tests/services/test_scheduler_service.py
git commit -m "feat: SchedulerService.list_schedules reads APScheduler + DB for unified schedule view"
```

---

### Task 2: GET /api/schedules 端点（admin-only）

**Files:**
- Create: `docupipe_manager/api/schedules.py`
- Modify: `docupipe_manager/main.py`（include router）
- Test: `tests/api/test_schedules.py`（新建）

**Interfaces:**
- Consumes: `deps.get_scheduler().list_schedules() -> list[dict]`（Task 1 产出）、`deps.get_settings()`（取 `credential_keepalive_cron` / `credential_keepalive_enabled`）
- Produces: `GET /docupipe/api/schedules` → `{"schedules": [...], "count": N, "keepalive_cron": str, "keepalive_enabled": bool}`，按 `next_run_time` 升序（None 末尾）

- [ ] **Step 1: 写 admin 返回 + 排序测试**

新建 `tests/api/test_schedules.py`：

```python
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import override_get_current_user, clear_overrides


@pytest.mark.asyncio
async def test_list_schedules_admin_returns_sorted(async_client):
    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    # service 返回未排序；next_run_time 故意倒序
    items = [
        {"kind": "keepalive", "scheduler_job_id": "k1", "name": "keepalive-b",
         "cron": "0 3 * * *", "next_run_time": "2026-06-29T03:00:00+08:00",
         "context": {}, "config_enabled": True, "registered": True},
        {"kind": "task", "scheduler_job_id": "t1", "name": "task-a",
         "cron": "0 2 * * *", "next_run_time": "2026-06-29T02:00:00+08:00",
         "context": {}, "config_enabled": True, "registered": True},
        {"kind": "task", "scheduler_job_id": None, "name": "task-drift",
         "cron": "0 5 * * *", "next_run_time": None,
         "context": {}, "config_enabled": True, "registered": False},
    ]
    with patch("docupipe_manager.deps.get_scheduler") as mock_get_scheduler, \
         patch("docupipe_manager.deps.get_settings") as mock_get_settings:
        mock_sched = MagicMock()
        mock_sched.list_schedules = AsyncMock(return_value=items)
        mock_get_scheduler.return_value = mock_sched
        mock_settings = MagicMock()
        mock_settings.credential_keepalive_cron = "0 3 * * *"
        mock_settings.credential_keepalive_enabled = True
        mock_get_settings.return_value = mock_settings

        r = await async_client.get("/docupipe/api/schedules")

    assert r.status_code == 200
    data = r.json()
    names = [s["name"] for s in data["schedules"]]
    # next_run_time 升序，None 末尾 → task-a(02:00), keepalive-b(03:00), task-drift(None)
    assert names == ["task-a", "keepalive-b", "task-drift"]
    assert data["count"] == 3
    assert data["keepalive_cron"] == "0 3 * * *"
    assert data["keepalive_enabled"] is True
    clear_overrides()


@pytest.mark.asyncio
async def test_list_schedules_non_admin_forbidden(async_client):
    override_get_current_user({"id": str(uuid.uuid4()), "role": "user"})
    r = await async_client.get("/docupipe/api/schedules")
    assert r.status_code == 403
    clear_overrides()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/pytest tests/api/test_schedules.py -xvs`
Expected: FAIL（404，路由不存在）

- [ ] **Step 3: 创建 api/schedules.py**

新建 `docupipe_manager/api/schedules.py`：

```python
from fastapi import APIRouter, Depends

from docupipe_manager import deps
from docupipe_manager.auth.dependencies import require_admin

router = APIRouter(prefix="/api/schedules", tags=["schedules"])


@router.get("")
async def list_schedules(user: dict = Depends(require_admin)):
    scheduler = deps.get_scheduler()
    settings = deps.get_settings()
    items = await scheduler.list_schedules()
    items.sort(key=lambda x: (x["next_run_time"] is None, x["next_run_time"] or ""))
    return {
        "schedules": items,
        "count": len(items),
        "keepalive_cron": settings.credential_keepalive_cron,
        "keepalive_enabled": bool(settings.credential_keepalive_enabled),
    }
```

- [ ] **Step 4: 注册 router**

在 `docupipe_manager/main.py` 的 router import 区（`env_vars` import 之后）加：

```python
from docupipe_manager.api.schedules import router as schedules_router
```

在 `app.include_router(env_vars_router, prefix="/docupipe")` 之后加：

```python
app.include_router(schedules_router, prefix="/docupipe")
```

- [ ] **Step 5: 运行测试确认通过**

Run: `.venv/bin/pytest tests/api/test_schedules.py -xvs`
Expected: PASS（2 个测试）

- [ ] **Step 6: 回归 runs API（确认未破坏）**

Run: `.venv/bin/pytest tests/api/test_runs.py -xvs`
Expected: 全部 PASS

- [ ] **Step 7: 提交**

```bash
git add docupipe_manager/api/schedules.py docupipe_manager/main.py tests/api/test_schedules.py
git commit -m "feat: add GET /api/schedules endpoint (admin-only, sorted by next_run_time)"
```

---

### Task 3: 调度页面 + 导航入口

**Files:**
- Modify: `docupipe_manager/api/pages.py`（加页面路由）
- Modify: `docupipe_manager/main.py`（nav_menu 加入口）
- Create: `docupipe_manager/templates/docupipe/schedules.html`
- Test: `tests/api/test_pages_schedules.py`（新建）

**Interfaces:**
- Consumes: `GET /docupipe/api/schedules`（Task 2 产出），共享 `_render` / `_ui_vars`（pages.py 内）
- Produces: `GET /docupipe/schedules` 页面（函数名 `schedules_list` → 路由 name `schedules_list`）

- [ ] **Step 1: 写页面路由 + 模板存在性测试**

新建 `tests/api/test_pages_schedules.py`：

```python
from pathlib import Path


def test_schedules_page_route_and_template():
    from docupipe_manager.main import app

    url = app.url_path_for("schedules_list")
    assert url == "/docupipe/schedules"

    template = (Path(__file__).resolve().parents[2]
                / "docupipe_manager" / "templates" / "docupipe" / "schedules.html")
    assert template.is_file(), f"missing template: {template}"
    assert '{% extends "base.html" %}' in template.read_text(encoding="utf-8")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/pytest tests/api/test_pages_schedules.py -xvs`
Expected: FAIL（`url_path_for` 找不到 `schedules_list`）

- [ ] **Step 3: 加页面路由**

在 `docupipe_manager/api/pages.py` 的 `runs_list` 路由之后加：

```python
@router.get("/schedules")
async def schedules_list(request: Request, user: dict = Depends(require_admin)):
    return _render(request, "docupipe/schedules.html", {"current_user": user})
```

确认 `require_admin` 已在 pages.py 顶部 import（已存在：`from docupipe_manager.auth.dependencies import get_current_user, require_admin`）。

- [ ] **Step 4: 加导航入口**

在 `docupipe_manager/main.py` 的 `DOCUPIPE_NAV_MENU` 加第三项：

```python
DOCUPIPE_NAV_MENU = [
    {
        "label": "DocuPipe",
        "items": [
            {"id": "projects", "label": "项目", "href": "/docupipe/projects"},
            {"id": "runs",     "label": "运行", "href": "/docupipe/runs"},
            {"id": "schedules","label": "调度", "href": "/docupipe/schedules"},
        ],
    },
]
```

- [ ] **Step 5: 创建模板**

新建 `docupipe_manager/templates/docupipe/schedules.html`：

```html
{% extends "base.html" %}
{% block title %}调度计划{% endblock %}
{% block content %}
<div class="content-header">
    <h2>调度计划</h2>
</div>

<div id="sched-summary" class="card-row-meta">加载中...</div>
<div id="schedules-list">
    <div class="empty-state">加载中...</div>
</div>
<script>
function kindTag(k) {
  return k === "keepalive" ? "Keepalive" : "Task";
}
function statusCell(s) {
  if (!s.registered) {
    return '<span class="status-tag is-failed">未注册</span>';
  }
  if (s.next_run_time === null) {
    return '<span class="status-tag is-running">已暂停</span>';
  }
  return '<span class="status-tag is-success">启用</span>';
}
function relTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  const diff = d - new Date();
  const h = Math.round(diff / 3600000);
  if (h <= 0) return "即将执行";
  if (h < 24) return h + " 小时后";
  return Math.round(h / 24) + " 天后";
}
function contextCell(s) {
  if (s.kind === "task") {
    const p = s.context.project_name
      ? `<a href="/docupipe/projects/${s.context.project_id}">${s.context.project_name}</a>` : "—";
    return `${p} / ${s.context.task_name || "—"}`;
  }
  return `凭证：${s.context.credential_name || "—"}（${s.context.credential_status}）`;
}

async function loadSchedules() {
  const box = document.getElementById("schedules-list");
  const sum = document.getElementById("sched-summary");
  const r = await fetch(`${API_PREFIX}/api/schedules`);
  if (!r.ok) {
    if (r.status === 403) { box.innerHTML = '<div class="empty-state">需要管理员权限。</div>'; }
    else { box.innerHTML = '<div class="empty-state">加载失败</div>'; }
    sum.textContent = "";
    return;
  }
  const data = await r.json();
  const taskN = data.schedules.filter(s => s.kind === "task").length;
  const kaN = data.schedules.length - taskN;
  const cronNote = data.keepalive_enabled
    ? `全局 keepalive cron: ${data.keepalive_cron}`
    : "keepalive 未启用";
  sum.textContent = `${taskN} 个 Task 调度 · ${kaN} 个 Keepalive 调度 · ${cronNote}`;

  if (!data.schedules.length) {
    box.innerHTML = '<div class="empty-state">暂无调度。</div>';
    return;
  }
  let html = '<table class="data-table"><thead><tr>'
    + '<th>类型</th><th>名称</th><th>所属</th><th>Cron</th>'
    + '<th>下次执行</th><th>状态</th></tr></thead><tbody>';
  for (const s of data.schedules) {
    html += `<tr>
      <td>${kindTag(s.kind)}</td>
      <td>${s.name}</td>
      <td>${contextCell(s)}</td>
      <td><code>${s.cron}</code></td>
      <td>${s.next_run_time ? new Date(s.next_run_time).toLocaleString() + '<br><span class="card-row-meta-inline">' + relTime(s.next_run_time) + '</span>' : "—"}</td>
      <td>${statusCell(s)}</td>
    </tr>`;
  }
  html += '</tbody></table>';
  box.innerHTML = html;
}

loadSchedules();
</script>
{% endblock %}
```

- [ ] **Step 6: 运行页面路由测试确认通过**

Run: `.venv/bin/pytest tests/api/test_pages_schedules.py -xvs`
Expected: PASS

- [ ] **Step 7: 全量回归**

Run: `.venv/bin/pytest tests/ -q`
Expected: 全部 PASS

- [ ] **Step 8: lint / typecheck**

Run: `.venv/bin/ruff check docupipe_manager/api/schedules.py docupipe_manager/services/scheduler_service.py docupipe_manager/api/pages.py docupipe_manager/main.py`
Expected: 无错误

- [ ] **Step 9: 提交**

```bash
git add docupipe_manager/api/pages.py docupipe_manager/main.py \
        docupipe_manager/templates/docupipe/schedules.html \
        tests/api/test_pages_schedules.py
git commit -m "feat: add /docupipe/schedules read-only page with nav entry"
```

---

## Self-Review

**1. Spec 覆盖：**
- 统一只读计划视图（task + keepalive）→ Task 1 `list_schedules` + Task 3 页面 ✓
- keepalive 可见（cron/下次执行/所属凭证）→ Task 1 keepalive 分支 + Task 3 模板 ✓
- 准确性（APScheduler 运行时状态、含 jitter 的 next_run_time）→ Task 1 读 `get_jobs()` 的 `next_run_time` ✓
- 暴露 keepalive 全局 cron 来源（顶部摘要）→ Task 2 响应 `keepalive_cron` + Task 3 摘要 ✓
- admin-only（页面 + API `require_admin`）→ Task 2 `Depends(require_admin)` + Task 3 页面 `require_admin` + 测试覆盖非 admin 403 ✓
- 漂移检测（registered=false）→ Task 1 DB-first 枚举 + `registered` 字段 + 测试 ✓
- 排序（next_run_time 升序 None 末尾）→ Task 2 排序 + 测试 ✓
- 导航入口 → Task 3 nav_menu ✓
- 非目标（执行历史/操作/轮询/per-credential cron）均未触碰 ✓

**2. 占位符扫描：** 无 TBD/TODO；每步含完整代码与确切命令。

**3. 类型一致性：**
- `list_schedules(self) -> list[dict]`：Task 1 定义、Task 2 调用一致。
- 条目字段 `kind/scheduler_job_id/name/cron/next_run_time/context/config_enabled/registered`：Task 1 产出、Task 2 测试构造、Task 3 模板读取三处一致。
- `_cron_from_trigger`：Task 1 定义并在同文件方法内使用。
- 路由 name `schedules_list`：Task 3 路由函数名与测试 `url_path_for` 一致。
- 排序键 `(x["next_run_time"] is None, x["next_run_time"] or "")`：Task 2 实现与测试期望顺序一致（task-a 02:00 → keepalive-b 03:00 → task-drift None）。
