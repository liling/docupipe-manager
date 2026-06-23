# 运行控制台（虚拟控制台）设计

- **日期**：2026-06-23
- **状态**：已确认，待制定实施计划
- **相关文件**：
  - `docupipe_manager/services/runner_service.py`
  - `docupipe_manager/api/runs.py`
  - `docupipe_manager/api/pages.py`
  - `docupipe_manager/models/pipeline_run.py`
  - `docupipe_manager/templates/docupipe/runs/detail.html`（新增）
  - `docupipe_manager/static/js/run_detail.js`（新增）
  - `docupipe_manager/static/js/project_detail.js`
  - `docupipe_manager/migrations/versions/0002_add_run_command_text.py`（新增）

## 1. 背景与问题

任务被触发后，用户完全看不到任务的执行过程：

1. **触发无后续**：`project_detail.js` 的"触发"按钮只 `alert("已触发")`，没有进入运行详情的入口。
2. **运行历史无日志**：`loadRuns()` 只展示状态、任务名、时间，从不调用已存在的 `GET /api/runs/{id}/log`。
3. **无运行详情页**：点击运行记录没有任何详情页可看。
4. **后端 flush bug**：`runner_service.py:180` 用 `open(log_path, "w")` 写日志但**从不 flush**，Python 默认缓冲导致任务运行期间日志文件几乎为空——即使前端拉取也拿不到内容。
5. **看不到启动命令**：实际执行的 `python -m docupipe run ...` 命令行从未持久化，也无任何界面展示。

## 2. 目标与非目标

### 目标
- 触发任务后自动跳转到一个"虚拟控制台"页面，能看到：
  - 实际执行的启动命令行
  - 任务在 stdout/stderr 上的**逐行实时输出**（SSE 推送，类似 `docker logs -f`）
  - 状态、退出码、起止时间
- 运行中刷新页面、或事后打开已完成的运行，都能看到完整日志。
- 修复后端 flush bug，保证运行期间日志文件即时可读。

### 非目标（YAGNI，本期不做）
- 不做多副本部署的跨进程日志聚合（当前单进程）。
- 不做日志搜索、过滤、ANSI 颜色渲染。
- 不做 WebSocket（SSE 单向推送已满足只读控制台需求）。
- 不做前端 JS 单元测试（项目无 JS 测试栈）。

## 3. 方案选择

针对"SSE 如何接入现有 fire-and-forget 子进程架构"，对比两方案后采用 **A**：

| 方案 | 描述 | 取舍 |
|------|------|------|
| **A. 内存日志总线（采用）** | RunnerService 维护 per-run 的 `deque` 缓冲 + 订阅者 Queue 集合；readline 循环里写文件+flush 后 broadcast | 实时性最好（一行一出）；与现有循环零侵入契合；运行中重启丢内存缓冲（回退读文件） |
| B. 纯文件轮询 | SSE 端点轮询文件指针推进位置推送 | 无内存状态但延迟大、需感知文件变更、与写入循环争抢句柄，更复杂 |

## 4. 后端架构设计

### 4.1 修复 flush bug
`_do_execute` 写日志处改为每次 write 后 `log_file.flush()`（或以 `buffering=1` 行缓冲打开），确保运行期间内容即时落盘。这样现有的 `GET /api/runs/{id}/log` 与下载接口在运行中也即时可用。

### 4.2 内存日志总线（`RunnerService` 新增成员）
```python
self._log_buffers: dict[uuid.UUID, deque[str]] = {}     # maxlen=2000
self._subscribers: dict[uuid.UUID, set[asyncio.Queue]] = {}
```

**广播点**：`_do_execute` readline 循环内，每读到一行：
1. `log_file.write(line_decoded); log_file.flush()`
2. `buffer.append(line_decoded)`（超容量自动丢弃旧行）
3. 对该 run 的所有订阅者 `queue.put_nowait(line_decoded)`

**新方法**：
- `subscribe(run_id) -> tuple[list[str], asyncio.Queue]`：返回当前缓冲的历史行副本 + 新建 Queue 加入订阅者集合。
- `unsubscribe(run_id, queue)`：从订阅者集合移除，丢弃 Queue。
- 运行结束/失败/取消时：向所有订阅者 Queue 投递哨兵 `None` 表示流结束，并从 `_subscribers`/`_log_buffers` 中清理该 run（保留 buffer 直到结束广播完成后清理）。

**容量保护**：`deque(maxlen=2000)` 防止超长日志撑爆内存；完整日志仍以文件为准（10MB 上限 `run_log_max_bytes`）。

### 4.3 启动命令持久化
`_do_execute` 拼好 `cmd` 列表后，在标记 `running` 的同一 UPDATE 语句里写入 `command_text = " ".join(shlex.quote(c) for c in cmd)`（仅展示用，不再执行，加 quote 防可读性问题）。

### 4.4 SSE 端点（`api/runs.py` 新增）
- 路由：`GET /api/runs/{run_id}/stream` → `text/event-stream`
- 鉴权：复用 `_verify_run_access`。
- 数据源选择：
  - 运行**在本进程且 active**（pending/running）：`runner.subscribe(run_id)` 拿历史行 + Queue。
  - 否则（已完成 / 进程重启后）：直接读 `log_path` 文件全部行作为历史；不订阅。
- 事件序列（SSE 格式）：
  1. `event: meta`，data 为 JSON：`{task_id, status, command_text, exit_code, started_at, completed_at}`
  2. 每个历史行 + 每个 Queue 新行 → `event: log`，`data:` 单行文本（行内换行按 SSE 规范处理）
  3. 运行结束 → `event: end`，data 为 JSON：`{status, exit_code}`，随后关闭流
- 心跳：每 15s 发一条 SSE 注释 `: keepalive\n\n` 防代理断连。
- 实现：`StreamingResponse(generator, media_type="text/event-stream")`，generator 为 async function。
- 订阅者退出（客户端断开）：捕获 `GeneratorExit`/`asyncio.CancelledError` 调用 `unsubscribe`，避免泄漏 Queue。

### 4.5 现有端点
- `GET /api/runs/{id}/log`、`GET /api/runs/{id}/download-log` 保留（flush 修复后运行中也可用）。
- `GET /api/runs/{id}` 补充返回 `command_text` 字段。

## 5. 前端页面设计

### 5.1 新路由（`api/pages.py`）
- `GET /docupipe/runs/{run_id}` → 渲染 `docupipe/runs/detail.html`，传入 `run_id`。

### 5.2 页面结构（`runs/detail.html` + `static/js/run_detail.js`）
- **头部卡片**：
  - 任务名：先取 `/api/runs/{id}` 拿 `task_id`，再取 `/api/projects/.../tasks` 中对应任务名（或复用 task detail 接口）
  - 状态 tag、退出码、起止时间
  - 启动命令：等宽 `<code>` 展示 `command_text`
  - "下载日志"按钮 → `/api/runs/{id}/download-log`
  - "取消运行"按钮（仅 running/pending 时显示）→ `POST /api/runs/{id}/cancel`
- **控制台区**：黑底等宽 `<pre id="console">`，SSE 行追加渲染。
- **滚动控制**：右上角"自动滚动"复选框（默认勾选）；勾选时新行到达自动滚到底部，取消勾选时不跟随以便回看。

### 5.3 SSE 客户端（`run_detail.js`）
```js
const es = new EventSource(`/api/runs/${runId}/stream`);
es.addEventListener("meta", e => renderMeta(JSON.parse(e.data)));
es.addEventListener("log",  e => appendLine(e.data));   // HTML 转义后追加
es.addEventListener("end",  e => { finalize(JSON.parse(e.data)); es.close(); });
es.onerror = () => { /* EventSource 自动重连；UI 显示"重连中" */ };
```
- `appendLine`：对文本做 `textContent` 赋值（自动转义），追加 `<div>` 行节点；若"自动滚动"开启则 `console.scrollTop = console.scrollHeight`。
- `finalize`：更新头部状态 tag、退出码；停止自动滚动；保留全部内容供回看。

### 5.4 触发跳转（改 `project_detail.js`）
"触发"按钮成功后跳转。`trigger` 端点（`api/tasks.py:195`）已返回 `{run_id, status}`，直接用：
```js
const r = await fetch(`/api/projects/${pid}/tasks/${id}/trigger`, {...});
const data = await r.json();
location.href = `/docupipe/runs/${data.run_id}`;
```

### 5.5 运行历史列表（改 `project_detail.js` 的 `loadRuns`）
每行加"查看"链接 → `/docupipe/runs/${run.id}`。

## 6. 数据迁移

新增 `migrations/versions/0002_add_run_command_text.py`，沿用现有 raw SQL + 幂等风格：
```python
revision = "0002"
down_revision = "0001"

def upgrade():
    op.execute("ALTER TABLE docupipe_manager.pipeline_runs "
               "ADD COLUMN IF NOT EXISTS command_text VARCHAR(1024)")

def downgrade():
    op.execute("ALTER TABLE docupipe_manager.pipeline_runs "
               "DROP COLUMN IF EXISTS command_text")
```

Model 同步（`models/pipeline_run.py`）：
```python
command_text: Mapped[str | None] = mapped_column(String(1024), nullable=True)
```

## 7. 测试计划

沿用项目 `pytest` + `unittest.mock` 风格（见 `tests/services/test_runner_service.py`）。

**`tests/services/test_runner_service.py` 新增：**
- `test_subscribe_returns_buffer_then_live`：手动塞 buffer 历史，再 broadcast 新行，断言订阅者先收历史、后收新行。
- `test_broadcast_flushes_each_line`：mock open，断言每行 write 后调用 flush。
- `test_unsubscribe_stops_broadcast`：取消订阅后再 broadcast，Queue 不再收到。
- `test_run_end_sends_sentinel_and_cleans`：运行结束时订阅者收到 `None` 哨兵，buffer/subscribers 被清理。

**`tests/api/test_runs.py` 新增（若无则建）：**
- `test_stream_active_run_emits_meta_history_end`：mock runner.subscribe 返回固定历史 + 一次性 Queue，断言 SSE 输出含 meta、逐行 log、end。
- `test_stream_completed_run_reads_file`：run 状态 succeeded 且无内存 buffer，mock 文件内容，断言从文件读取并发出。

**不新增：** 前端 JS 测试。

## 8. 验收标准

1. 触发任务 → 自动跳转 `/docupipe/runs/{id}` → 头部显示启动命令，控制台逐行实时刷出 stdout/stderr。
2. 运行中刷新详情页 → 历史行重放 + 继续实时追加，无重复无丢失。
3. 打开已完成的运行 → 看到完整日志 + 最终状态/退出码，SSE 主动关闭。
4. 下载日志按钮可用；取消运行按钮在运行中可用。
5. 后端 flush 修复后，运行期间 `GET /api/runs/{id}/log` 即时返回非空内容。
6. 现有 `tests/services/test_runner_service.py`、`test_scheduler_service.py` 及新增测试全绿。

## 9. 风险与权衡

- **内存缓冲上限**：`deque(maxlen=2000)`，单行假设 1KB 则上限约 2MB/运行；完整真相以文件为准，超长日志靠文件兜底，SSE 仅保最近窗口。
- **进程重启**：运行中若服务重启，内存 buffer 与子进程句柄丢失；该 run 状态需人工/超时兜底标记为 failed（属现有运维范畴，本期不引入）。重启后访问历史 run 走文件读取分支。
- **SSE 与代理**：15s 心跳注释防中间代理断连；前端 `EventSource` 自带重连，断线后通过 meta+历史重放保证一致性。
- **命令注入展示**：`command_text` 仅用于展示，用 `shlex.quote` 拼装避免空格/特殊字符造成的可读性问题；前端 `textContent` 赋值防 XSS。
