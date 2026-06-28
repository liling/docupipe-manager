# 调度计划视图设计

> 日期：2026-06-28
> 状态：已确认，待写实施计划

## 背景与动机

系统中有两类「被配置为会自动运行」的调度，由 `SchedulerService` 在同一个 `AsyncIOScheduler` 上管理：

1. **Task 调度**（job_id `task-{task_id}`）：源自 `Task` 表的 `schedule_cron` / `schedule_enabled` / `schedule_pipeline` / `schedule_mode` 字段，随 task 创建/编辑而注册。
2. **Keepalive 调度**（job_id `keepalive-{credential_id}`）：**隐式**派生自全局设置 `credential_keepalive_cron` × 每个 `status=active` 的凭证，启动时由 `_reload_all` 批量注册，凭证 import/finalize/revoke 时增量维护。

两者的可见性差距很大：

| 调度类型 | 可见性 |
|---|---|
| Task 调度 | ✅ 分散在 task 表单/列表（每条 task 的 cron 字段） |
| Keepalive 调度 | ❌ 完全不可见——只存在于 APScheduler 进程内存与日志，UI/API 无任何入口 |

此外存在一个易混的概念区分：`Job` 表存的是**执行记录**（每次运行的历史），而「调度计划」是 APScheduler 里的**触发配置**（什么时候会运行）。两者并非同一物。

用户希望有一个统一的地方看到**全部调度计划**（回答「系统接下来会自动做什么」），且必须包含目前完全隐藏的 keepalive 调度。

## 目标

1. **统一调度计划视图**：单个只读页面，合并列出 Task 调度与 Keepalive 调度。
2. **让 keepalive 可见**：暴露每个 active 凭证的 keepalive cron、下次执行时间、所属凭证。
3. **准确性**：展示的就是「系统真正会执行的」，而非从配置重建的近似——含 jitter 后的实际下次执行时间。
4. **暴露 keepalive 节奏来源**：让用户理解「为什么所有 keepalive 时间一样」——展示全局 cron 设置。

## 非目标

- **不做执行历史视图**：Job 表的执行记录（含 keepalive 历史）本次不展示；用户已明确要「计划视图（未来）」。
- **不做操作能力**：页面只读，不提供暂停/启用/编辑 cron 的按钮（这些仍在各自管理页操作；YAGNI）。
- **不做实时刷新**：页面加载拉一次即可，不轮询、不做 SSE（调度计划变化频率低）。
- **不做 per-credential cron**：keepalive 仍用全局配置，不在本视图引入按凭证配置能力。
- **不做分页**：调度总数 = tasks + active credentials，量级小。

## 关键决策摘要

| 维度 | 决策 |
|---|---|
| 视图性质 | 只读「计划视图（未来）」，列出所有被配置为自动运行的调度 |
| 数据来源 | **读 APScheduler 运行时状态**（`get_jobs()`，事实来源），用 DB 补充可读上下文 |
| next_run_time | 取自 APScheduler，含 jitter 后实际时间（不自行用 croniter 计算） |
| 条目形状 | Task / Keepalive 两种调度合并为统一 schedule 条目，前端只渲染一种列表 |
| 排序 | 按 `next_run_time` 升序（最快要执行的排最前） |
| 权限 | 沿用现有模型：Task 调度按 project member 可见性过滤；Keepalive 按 credential.project_id 过滤；admin 看全部 |
| 漂移检测 | 方案 A 的额外价值：标记「配置 enabled 但未注册」的异常态 |
| 位置 | 新增独立页面 `/docupipe/schedules` + 导航入口 |
| keepalive cron 来源 | 顶部摘要展示全局 `credential_keepalive_cron` 设置 |

## 两种实现路径（数据来源）

**方案 A：读取 APScheduler 运行时状态（已选）**

直接调 `scheduler.get_jobs()` 拿到真实注册的调度 + 准确的 `next_run_time`，再用 DB 补充可读上下文（task 名、credential 名、project 名）。

- 优点：展示的就是「系统真正会执行的」，`next_run_time` 精确含 jitter；能对账出「配置了但没注册成功」的隐藏问题。
- 缺点：是进程内存状态，重启后短暂为空（但启动时 `_reload_all` 会重建，可接受）。

**方案 B：从 DB + 配置重建（未选）**

Task 调度查 Task 表；keepalive 用 `credential_keepalive_cron × active credentials` 计算；用 croniter 算 next_run_time。

- 优点：无状态、可复现。
- 缺点：可能与 APScheduler 实际注册状态漂移（注册失败、settings 改了未 reload）；`next_run_time` 不含 jitter。

**选择方案 A**：准确性对「计划视图」最重要；APScheduler 是事实来源，DB 只负责补充可读信息。

## 架构与数据流

```
APScheduler.get_jobs()  ──┐
  (id, trigger,           │  按 job id 前缀分流:
   next_run_time, name)   ├─ "task-{id}"     → 查 Task + Project 取名字/状态
                          └─ "keepalive-{id}" → 查 DwsCredential 取名字
DB (Task/Project/         →  合并 → 统一 schedule 条目
    DwsCredential)            {kind, name, cron, next_run_time,
                              context, config_enabled, registered}
```

`SchedulerService` 新增 `list_schedules() -> list[dict]` 方法：内部读取 `self._scheduler.get_jobs()`，按 job id 前缀（`task-` / `keepalive-`）分流，批量查 DB enrich，合并返回。API 层只做权限检查 + 调用。

## 数据模型（统一 schedule 条目）

两种调度合并成同一形状，前端只渲染一种列表：

```python
{
  "kind": "task" | "keepalive",
  "scheduler_job_id": "task-<uuid>" | "keepalive-<uuid>",
  "name": "task-{slug}" | "keepalive-{credential_name}",
  "cron": "0 2 * * *",                        # 从 CronTrigger 提取的标准 crontab 字符串
  "next_run_time": "2026-06-29T02:00:00+08:00",  # APScheduler 给的，含 jitter；None=已暂停
  "context": {                               # 可读上下文（从 DB enrich）
    # task:
    "task_id": "...", "task_name": "...", "project_id": "...", "project_name": "..."
    # 或 keepalive:
    "credential_id": "...", "credential_name": "...", "credential_status": "active"
  },
  "config_enabled": true,   # DB 侧配置开关（task.schedule_enabled / keepalive 全局开关）
  "registered": true        # 是否真在 APScheduler 里（false=异常漂移）
}
```

> `next_run_time` 为 None 表示该 job 在 APScheduler 里被暂停（`pause_job`）——仍列出，前端标记「已暂停」。

## API

单个端点，只读：

```
GET /api/schedules
```

- 返回 `{ "schedules": [ ... ], "count": N }`
- 排序：按 `next_run_time` 升序（None 排末尾）——「计划视图」最自然的排序。
- 无分页。

**权限过滤**：
- Task 调度：非 admin 只看到自己有 member 权限的 project 下的 task 调度（与现有 runs/tasks 可见性规则一致）。
- Keepalive 调度：admin 看全部；非 admin 只看到自己所属 project 下的 credential 的 keepalive（经 `credential.project_id` 过滤）。
- 不引入新的越权面。

## 后端服务变更

### `SchedulerService.list_schedules`（`services/scheduler_service.py`）

```python
async def list_schedules(
    self,
    visible_task_ids: set[uuid.UUID] | None = None,        # None = 不过滤(全部)
    visible_credential_ids: set[uuid.UUID] | None = None,   # None = 不过滤(全部)
) -> list[dict]:
    """读取 APScheduler 运行时状态 + DB enrich，返回统一 schedule 条目。
    权限过滤由 API 层算好可见 id 集合后传入，避免本层耦合 auth。"""
    # 1. self._scheduler.get_jobs() 拿全部已注册 job
    # 2. 按 id 前缀分两组：task- / keepalive-
    # 3. 批量查 DB（仅查可见 id 集合内的）：
    #    - task- → JOIN Task + Project 取 name/slug/project_name/schedule_enabled
    #    - keepalive- → 查 DwsCredential 取 name/status
    # 4. 从 CronTrigger 重建标准 crontab 字符串（trigger.fields）
    # 5. 组装统一条目（next_run_time 来自 APScheduler job）
    # 6. 漂移检测：可见且 schedule_enabled=true 的 task 但 APScheduler 无对应 job → registered=false
    # 7. 返回（排序交给 API 层）
```

> `visible_*` 为 `None` 时不过滤（admin 全量）；非 None 时仅返回集合内的，service 层不查 auth。

### `api/schedules.py`（新建）

```python
router = APIRouter(prefix="/api/schedules", tags=["schedules"])

@router.get("")
async def list_schedules(user: dict = Depends(get_current_user)):
    # 1. 计算当前用户可见的 task_id 集合 / credential_id 集合（admin=全部）
    # 2. scheduler = deps.get_scheduler()
    # 3. items = await scheduler.list_schedules(visible_task_ids, visible_credential_ids)
    # 4. 按 next_run_time 升序排序（None 末尾）
    # 5. return {"schedules": items, "count": len(items)}
```

### 装配

- `main.py` / `deps.py`：注册 `api/schedules.py` router；无需新依赖（复用 `get_scheduler`、`get_engine`）。

## 前端

### 页面（`templates/docupipe/schedules.html` + `api/pages.py` 路由）

- 路由 `GET /docupipe/schedules`（`pages.py` 加一行）。
- 导航菜单新增「调度 / Schedules」入口（与现有 nav_menu 装配一致）。
- 单列表，每行一个 schedule，列：
  - 类型徽标（Task / Keepalive）
  - 名称
  - 所属（project 名 / credential 名）
  - cron 表达式
  - 下次执行时间（本地时区 Asia/Shanghai + 相对时间如「12 小时后」）
  - 状态（启用 / 已暂停；`registered=false` 用警告标记）
- **顶部摘要**：`N 个 Task 调度 · M 个 Keepalive 调度 · 全局 keepalive cron: 0 3 * * *`（暴露 keepalive 全局 cron 来源，解释「为什么所有 keepalive 时间一样」）。
- 只读：无暂停/编辑按钮。
- 加载时拉一次 `GET /api/schedules`，不轮询。

## 边界情况

- **APScheduler 暂停的 job**：`next_run_time` 为 None —— 列表仍列出该行，标记「已暂停」。
- **配置 enabled 但未注册**（异常漂移）：`registered: false`，用警告标记标出，让用户发现「task.schedule_enabled=true 但 APScheduler 里没有」这类问题（方案 A 相对方案 B 的额外价值）。
- **启动瞬间**：scheduler 尚未 reload 完，列表可能短暂为空 —— 可接受，不做特殊处理。
- **cron 展示**：从 `CronTrigger` 的 `trigger.fields` 重建标准 crontab 字符串，非 APScheduler 内部表达。
- **时区**：`next_run_time` 返回带时区 ISO；前端按 Asia/Shanghai 本地展示 + 相对时间。

## 测试策略

沿用现有目录（`tests/api`、`tests/services`）与 `conftest.py` fixture。

| 层 | 文件 | 覆盖点 |
|---|---|---|
| service | `tests/services/test_scheduler_service.py`（追加） | mock `get_jobs()` 返回 task-/keepalive- 两类 job，验证 enrich 后条目形状、`registered` 标记、cron 提取正确；权限过滤参数生效 |
| 边界 | 同上 | `next_run_time=None`（已暂停）仍列出并标记；DB 有 task 但 APScheduler 无对应 job → `registered=false` |
| API | `tests/api/test_schedules.py`（新建） | admin 看全部；非 admin 只看到授权 project 的 task 调度 + 自己 project 的 credential keepalive；排序按 next_run_time 升序（None 末尾） |

## 风险与权衡

1. **运行时状态 vs 配置漂移**：方案 A 展示的是 APScheduler 实际状态，若与 DB 配置不一致会直接暴露为 `registered=false`。这是特性而非缺陷——帮助发现隐藏问题。
2. **进程内存状态**：重启后短暂为空。缓解：`_reload_all` 启动时重建；页面在 scheduler 未就绪时显示空态即可。
3. **权限模型复杂度**：keepalive 按 `credential.project_id` 过滤是新增逻辑，但与 Task 调度的 project member 模型对称，不引入新模式。
