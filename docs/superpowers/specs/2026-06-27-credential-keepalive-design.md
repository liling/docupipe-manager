# 凭证保活与 Job 下沉设计

> 日期：2026-06-27
> 状态：已确认，待写实施计划

## 背景与动机

DWS 凭证使用带 refresh_token 的 OAuth2，且 **refresh_token 非轮换（可复用）**——刷新只换发新的 access_token，refresh_token 本身直到自身过期前始终有效。Token 只在**真实调用业务 API** 时才会被刷新（`dws` 无独立 refresh 子命令，`dws auth status` 也不触发刷新）。

当前任务执行流程（`runner_service.py`）是 `import → 跑 docupipe → logout`：run 期间发生的 token 刷新被 `dws auth logout` 丢弃。因为 refresh_token 非轮换，DB 里存的 `auth_blob` 跨任意多次刷新始终有效，**但 access_token 会逐渐过期**；若某凭证长期没有触发刷新动作，最终 access_token 过期、refresh_token 也会在 7 天（`refresh_token_ttl_days`）后过期，凭证死亡。

因此需要一套**独立的定时保活机制**：周期性地对每个 active 凭证调一次轻量业务 API 触发刷新，并把刷新后的 blob 回写 DB。run 本身不负责回写（保持简单）。

顺带暴露出两个已有问题，本设计一并处理：

1. **执行记录耦合**：`PipelineRun` 把"通用命令执行 + 输出捕获"（status/pid/exit_code/log_path/command_text 等）和"docupipe 专属语义"（task_id/mode/pipeline_name）焊在同一张表。保活作为一种命令执行却无处落脚。
2. **macOS 钥匙串串行**：`dws` 的 import/status/export 必须用**真实 HOME**（钥匙串绑用户会话，见 `credential_service.py:203-205` 注释）。真实 `~/.dws` 是单写者资源，而 `CredentialService._dws_lock` 只在内部保护 `_probe_auth_blob`，runner 的 import/logout 完全不加锁——check_status 与 cred-using run 并发会互相破坏。**进程级锁本次不做**（见非目标），仅以复用 `_dws_lock` 做 interim 缓解。

## 目标

1. **抽出 `Job` 实体**：通用"命令执行 + 输出捕获"记录。`PipelineRun` 引用 Job（1:1），瘦身为只保留 docupipe 专属字段；身份与用途不变。
2. **凭证保活**：作为 `Job` 的一种 kind（`credential_keepalive`），通过现有 APScheduler 周期触发，调 `dws wiki space list` 触发刷新，回写 DB。
3. **统一可观测性**：任何 Job 都有 log_path / status / 命令输出，保活天然获得执行记录。
4. **调度复用**：不引入新调度框架，`SchedulerService` 在同一 `AsyncIOScheduler` 上管理 task-run job 与 keepalive job 两类。

## 非目标

- **不做进程级 dws-state 锁**：本次 keep-alive 仅复用现有 `_dws_lock`（串行 CredentialService 内操作），与正在跑的 cred-using run 的并发风险**已知且本次接受**（缓解：默认 3am cron 低并发窗口）。进程级锁下次单独做。
- **run 不回写**：run 维持 import→用→logout，不做 blob 回写。
- **不做保活历史 UI / 列表 API**：数据落入 `jobs` 表，需要时再加 `GET .../credentials/{cid}/jobs` 与对应 UI。
- **不做 per-credential cron**：保活节奏用全局配置（YAGNI）。
- **不重命名对外"运行"文案/路径**：`PipelineRun` 表名、`/runs` 路径、前端"运行记录"均保留；仅内部模型与表结构归一。
- **不抽 runner 的 broadcast/log 公共 helper**：保活自写日志循环（无 broadcast）；runner 那套保持现状。

## 关键决策摘要

| 维度 | 决策 |
|---|---|
| 架构 | 独立定时保活任务，与 run 解耦；run 不回写 |
| 刷新触发 | `dws wiki space list`（轻量业务 API，触发 token 刷新） |
| Token 语义 | refresh_token **非轮换**，可复用 → DB blob 跨多次刷新始终有效 |
| macOS 约束 | 保活走**真实 HOME**（隔离 HOME 在 macOS 不可用） |
| 串行 | 复用现有 `_dws_lock` 做 interim；**进程级锁下次再做** |
| Job 抽取 | 切法 A：Job 拿走全部通用执行字段；PipelineRun 引用 Job |
| Job↔Run 关系 | 1:1，**共享 id**（`jobs.id = pipeline_runs.id`），迁移零映射成本 |
| 调度 | 复用现有 `AsyncIOScheduler`，新增 keepalive job 类型，不做多态派发表 |
| 失败语义 | 保活失败不改 `cred.status`（靠现有"测试"按钮做权威判定），仅记 Job(failed) + 审计 |
| 保活记录 | 落 `jobs` 表（kind=credential_keepalive），写 log_path 文件，无实时流 |
| 节奏 | 全局 cron，默认 `0 3 * * *`（3am Asia/Shanghai），对 7 天 TTL 有 7 倍裕度 |

## 数据模型

所有表位于 `docupipe_manager` schema。枚举值与旧 `run_status` / `run_trigger_type` 一致，仅改名。

### 新增 `Job`（`models/job.py`）

```python
class JobKind(str, enum.Enum):
    docupipe_run = "docupipe_run"
    credential_keepalive = "credential_keepalive"

class JobStatus(str, enum.Enum):       # 值同旧 RunStatus
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"

class JobTriggerType(str, enum.Enum):  # 值同旧 RunTriggerType
    manual = "manual"
    scheduled = "scheduled"

class Job(Base):
    __tablename__ = "jobs"
    id: UUID PK (default uuid4)
    kind: JobKind NOT NULL
    status: JobStatus NOT NULL default pending
    pid: int | None
    exit_code: int | None
    started_at: datetime | None
    completed_at: datetime | None
    log_path: str(512) | None
    command_text: str(1024) | None
    error_message: Text | None
    trigger_type: JobTriggerType NOT NULL
    triggered_by: UUID | None
    credential_id: UUID | None  FK→dws_credentials ON DELETE SET NULL
    created_at: datetime server_default now
```

### `PipelineRun` 瘦身（`models/pipeline_run.py`）

身份不变——仍是"绑 task 的 docupipe 运行"，仅不再重复存通用执行字段：

- **失去**：status / pid / exit_code / started_at / completed_at / log_path / command_text / error_message / trigger_type / triggered_by / created_at
- **保留**：id(PK)、task_id(NOT NULL FK→tasks)、pipeline_name、mode
- **新增**：`job_id`(FK→jobs, NOT NULL, UNIQUE)

> 关系：1 个 PipelineRun 恰好对应 1 个 Job（kind=docupipe_run）。保活不建 PipelineRun，直接建 Job（kind=credential_keepalive + credential_id）。

### 迁移 `0007_create_jobs_and_backfill.py`

`revision = "0007"`，`down_revision = "0006"`（`0006_owner_as_member` 已存在于 master；新分支顺延为 0007/0008）。手写幂等 raw SQL（项目惯例，不依赖 autogenerate）：

1. 建枚举类型 `docupipe_manager.job_kind`(`docupipe_run`,`credential_keepalive`)、`job_status`(同 run_status 值)、`job_trigger_type`(同 run_trigger_type 值)。
2. 建 `docupipe_manager.jobs` 表（含 `credential_id` FK `ON DELETE SET NULL`）。
3. 回填：`INSERT INTO docupipe_manager.jobs (id, kind, status, pid, exit_code, started_at, completed_at, log_path, command_text, error_message, trigger_type, triggered_by, created_at) SELECT id, 'docupipe_run', status, pid, exit_code, started_at, completed_at, log_path, command_text, error_message, trigger_type, triggered_by, created_at FROM docupipe_manager.pipeline_runs`。
4. `ALTER TABLE docupipe_manager.pipeline_runs ADD COLUMN job_id UUID`。
5. `UPDATE docupipe_manager.pipeline_runs SET job_id = id`（**共享 id**）。
6. 加 FK `pipeline_runs.job_id → jobs.id` + `UNIQUE(job_id)`。
7. `ALTER TABLE docupipe_manager.pipeline_runs DROP COLUMN` 上列被搬走的列。
8. downgrade 反向（重建列、回填、删 jobs 表与枚举）。

存量行由步骤 3-5 自动迁移。`jobs.credential_id` 对存量行为 NULL（仅 keepalive 用）。

### `DwsCredential` 不变

`last_refreshed_at` 列早已存在（之前无写入方），本次首次被 `refresh_credential` 写入。

## 后端服务变更

### `SchedulerService` 泛化（`services/scheduler_service.py`）

同一 `AsyncIOScheduler` 管理两类 job，**不做多态派发表**：

- `schedule_task(task_id)` / `unschedule_task(task_id)` / `_scheduled_run → runner.start_run`：**不变**。
- **新增** `schedule_keepalive(credential_id: uuid.UUID) -> None`：
  - job_id = `f"keepalive-{credential_id}"`
  - 仅当 `settings.credential_keepalive_enabled` 且凭证 active 时注册
  - trigger = `CronTrigger.from_crontab(settings.credential_keepalive_cron)`
  - 回调 `self._scheduled_keepalive`，args=[credential_id]
- **新增** `unschedule_keepalive(credential_id: uuid.UUID) -> None`：`remove_job(job_id)`（不存在则忽略）。
- **新增** `_scheduled_keepalive(credential_id)`：守卫（凭证仍 active）→ `await credential_service.refresh_credential(credential_id)`。构造时需注入 `credential_service`。
- `_reload_all` 扩展：加载完 task job 后，若 keepalive enabled，遍历所有 `status=active` 的凭证调 `schedule_keepalive`。

构造函数签名扩展：`__init__(self, runner, credential_service, engine, settings)`。`main.py` 装配处与 `deps.py` 同步。

### `CredentialService.refresh_credential`（`services/credential_service.py`）

```python
async def refresh_credential(self, credential_id: uuid.UUID) -> None:
    """定时保活：真实 HOME 调一次业务 API 触发刷新，回写 DB。
    失败仅记 Job(failed)+审计，不改 cred.status。"""
    # 1. 读 cred；status != active → 直接返回；decrypt_sm4(auth_blob)
    # 2. 建 Job(kind=credential_keepalive, status=pending, credential_id,
    #          trigger_type=scheduled, command_text="dws wiki space list",
    #          pid=NULL)   # 保活是多个短子进程序列，无单一长进程，pid 恒空、不支持 cancel
    # 3. log_path = {data_dir}/credentials/{cred_id}/jobs/{job_id}.log；makedirs
    # 4. async with self._dws_lock:        # interim 串行；进程级锁下次再做
    #      a. blob 写 tmp.b64
    #      b. dws auth logout (真实 HOME)  # 幂等清场
    #      c. dws auth import --base64 -i tmp
    #      d. UPDATE job(status=running, started_at)   # 不写 pid
    #      e. dws wiki space list            # 触发刷新；stdout/stderr 追加写 log_path
    #      f. dws auth status --format json  # 取 expires_at / refresh_expires_at
    #      g. dws auth export --base64 -o tmp2
    #      h. encrypt_sm4(new_blob) → UPDATE cred(auth_blob,
    #         token_expires_at, refresh_token_expires_at, last_refreshed_at=now)
    #      i. UPDATE job(status=succeeded, exit_code, completed_at, log_path)
    #      j. push_audit("docupipe.credential.refresh.success", credential_id, job_id)
    #    except Exception as e:
    #      UPDATE job(status=failed, error_message=str(e)[:2048], completed_at, log_path)
    #      push_audit("docupipe.credential.refresh.fail", credential_id, error=str(e))
    #      logger.warning(...); 不改 cred.status
    #    finally:
    #      dws auth logout; os.unlink(tmp); （无 rmtree，未建隔离 HOME）
```

**日志写法**：保活自写一个简化的"逐行读 stdout/stderr → 追加写 log_path"循环（仿 `runner_service._stream_subprocess` 的写文件部分），**不做 broadcast、不更新 semaphore**。子进程一律真实 HOME（macOS 钥匙串要求）。

**子进程封装**：可顺手在 `CredentialService` 内抽 `_run_dws(args, home=None) -> (exit_code, stdout, stderr)` 私有辅助，统一 import/status/export/wiki/logout 的子进程调用与超时。仅服务于保活与现有 `_probe_auth_blob`，不强行重构 runner。

### `RunnerService` 适配 Job（`services/runner_service.py`）

- `start_run`：建 `PipelineRun` 的同时建对应 `Job`（kind=docupipe_run，共享同一 id），`PipelineRun.job_id` 指向它。原来写在 PipelineRun 上的 status/pid/exit_code/时间戳/log_path/command_text/error_message/trigger 字段，全部改写到 **Job**。
- `_stream_subprocess` / `_finalize_run` / `_mark_run_failed` / `cancel_run`：操作对象由 PipelineRun 改为 Job（status、pid 等）。`subscribe`/`_broadcast` 按 **job_id**（= run_id，共享 id）索引，订阅 API 不变。
- 其余（import/logout/凭证加载/环境变量）不变。

> 因共享 id，所有 `run_id` 参数语义不变（既是 PipelineRun.id 也是 Job.id）。

## 配置（`config.py`）

```python
credential_keepalive_enabled: bool = True
credential_keepalive_cron: str = "0 3 * * *"   # 每天 3am，scheduler 时区 Asia/Shanghai
```

复用：`refresh_token_ttl_days`(=7，节奏参考)、`dws_cli_path`、`encryption_key`、`data_dir`。

## API & UI 影响

- **run 相关 API**（`api/tasks.py` 运行列表、跨项目运行页）：PipelineRun 失去多列，查询需 `JOIN jobs`；**响应 shape 保持不变**（把 job 字段拍平回原 run 响应字段名），前端零改动。
- **凭证 API**（`api/credentials.py`）：`create_from_import` / `finalize_login` 成功后调 `scheduler.schedule_keepalive(cred.id)`；`revoke` 调 `unschedule_keepalive`。响应不变。
- **保活历史 API / UI**：本次**非目标**（数据已在 jobs 表）。

## 钩子装配

- `deps.py`：`SchedulerService` 构造增加 `credential_service` 参数；`get_scheduler` 不变。
- `main.py` lifespan：`scheduler.start()` 内部 `_reload_all` 自动加载 keepalive job；无需额外启动逻辑。
- `api/credentials.py`：import 上述两个钩子（对称于 `api/tasks.py` 对 `schedule_task` 的处理）。

## 测试策略

沿用现有目录（`tests/api`、`tests/services`、`tests/unit`）与 `conftest.py` fixture。dws 子进程用 mock；真实 CLI 调用留给 `@pytest.mark.integration`。

| 层 | 文件 | 覆盖点 |
|---|---|---|
| 迁移 | `tests/migrations/`（新建或追加） | jobs 从 pipeline_runs 回填；`job_id = run.id`；列正确搬迁；downgrade 可逆 |
| 模型 | `tests/unit/test_models.py`（追加） | Job/PipelineRun 1:1 关系；枚举值；credential_id FK |
| runner | `tests/services/test_runner_service.py`（追加+回归） | 现在 write Job+PipelineRun；status/pid/log_path 落 Job；现有用例回归（共享 id 保语义） |
| credential_service | `tests/services/test_credential_service.py`（追加） | refresh 成功回写 blob/expires/last_refreshed_at + Job(succeeded)；`wiki space list` 失败 → Job(failed)、cred 不动；revoked/non-active 跳过；`_dws_lock` 串行 |
| scheduler | `tests/services/test_scheduler_service.py`（新建或追加） | schedule/unschedule_keepalive 注册/移除 job；`_reload_all` 加载 keepalive；`_scheduled_keepalive` 调 refresh_credential；enabled=False 不注册 |
| API | `tests/api/test_credentials.py`（追加） | 建凭证(import/finalize) → schedule_keepalive 被调；revoke → unschedule；run 列表经 join 字段不丢 |

## 风险与权衡

1. **macOS 串行（已知、本次接受）**：keep-alive 真实 HOME，仅复用 `_dws_lock`（只串行 CredentialService 内操作）。与正在跑的 cred-using run 仍可能互相 clobber。缓解：默认 3am cron 低并发窗口。进程级锁按决定下次单独做。
2. **迁移风险**：列搬迁 + 回填 jobs。缓解：共享 id（零映射）、幂等 SQL、上线前在 dump 上验证、保留 downgrade。
3. **`dws wiki space list` 刷新假设**：本设计假定该调用触发 token 刷新且轻量。实现/集成阶段需验证；若不触发，保活无法续命（会暴露为 Job failed，可观测、可发现）。
4. **保活无实时流**：by design（后台维护任务），事后靠 Job/log 文件可查。
5. **Job 抽取改动面**：触及 runner 全部状态写回路径与 run 列表 API 的 join。共享 id 显著降低语义风险；现有 runner 测试回归作为安全网。
