# dws 状态隔离与取消全局锁设计

> 日期：2026-06-28
> 状态：已确认，待写实施计划

## 背景与动机

DocuPipe Manager 所有触碰 `dws` 认证状态的子进程（`auth import`/`status`/`export`/`wiki space list`、以及 runner 启动的 `docupipe` 主进程）当前都跑在**真实 `$HOME`** 下，共享同一个 `~/.dws`。为避免互相 clobber，`CredentialService` 内部用了一把 `asyncio.Lock()`（`_dws_lock`）串行化 `_probe_auth_blob` 与 `refresh_credential`。

这套做法有三个已知问题：

1. **RunnerService 完全不加锁**（`runner_service.py:214` 的 `_import_credential` 同样 logout+import 真实 HOME）。keepalive 设计文档（`2026-06-27-credential-keepalive-design.md:17,28`）已显式记录此风险，作为"已知并接受的 interim"，把进程级锁推迟到"下次再做"。
2. **`_ensure_dws_state` 在锁外跑 `_run_dws(["wiki","space","list"])`**（`credential_service.py:358`），仍可能与锁内 logout 撞车。
3. **`asyncio.Lock` 只在单进程内有效**。`Dockerfile` 当前是单 uvicorn worker，但一旦多 worker 部署即失效。

更深一层：上述串行需求的前提是"**真实 HOME 不可隔离**"。keepalive 设计把这条当作硬约束，理由是 macOS 钥匙串。本次 brainstorming 期间通过实验**证伪了该前提**（见下"事实核查"）。前提既不成立，全局锁本身就是多余且有害的（牺牲并发、不修移植性）。

### 事实核查（已验证）

对 `dws v1.0.39` 做的只读 + 隔离实验得出：

- `dws` 二进制支持隐藏 env：`DWS_DISABLE_KEYCHAIN`、`DWS_KEYCHAIN_DIR`、`DWS_CONFIG_DIR`、`DWS_CACHE_DIR`。
- 默认（macOS）将 DEK 存**系统 Keychain**。`dws auth export` 在此情形下直接拒绝导出并报错：*"macOS 默认将 DEK 存在系统 Keychain，导出的包无法在其它机器解密；请设置 `DWS_DISABLE_KEYCHAIN=1` 后重新登录再导出"*。
- 设 `DWS_DISABLE_KEYCHAIN=1` 后，DEK 走**文件后端**（写到 `$HOME/.../dws-cli/dek`），不再触碰 OS keychain。
- 实测 `HOME=$ROOT` + `DWS_CONFIG_DIR` + `DWS_CACHE_DIR` + `DWS_DISABLE_KEYCHAIN=1` 跑 `dws auth status`：写入全部落到 `$ROOT` 子目录，**真实 `~/.dws` 纹丝不动**（`diff` 前后 mtime 为空）。

结论：**每个 dws 操作分配独立临时目录 + 该 flag，即可完全并发且互不干扰，同时让 `export` 产出的 blob 真正可移植**。生产部署目标是 `python:3.12-slim` Linux 容器（无 Keychain），本就适合文件 DEK；macOS 开发环境靠 flag 同样可用。

## 目标

1. **取消 `_dws_lock` 与 `_ensure_dws_state`**，所有 dws 操作改为隔离临时目录执行。
2. **统一隔离入口**：新增共享 helper，`CredentialService` 与 `RunnerService` 共用。
3. **覆盖 runner 漏锁的根因**：runner 的 import + docupipe 主进程共享同一隔离会话。
4. **顺带修移植性**：device-flow 与所有 export 路径产出可跨机解密的 blob。
5. **真并发**：多个 run、keepalive、凭证创建/测试互不阻塞。

## 非目标

- **不做进程级文件锁**（已知隔离可行，锁是严格更差的解）。
- **不为存量 dev Keychain-bound blob 写兼容代码**（见"边界与风险"，dev 重登即可）。
- **不改 run 的回写语义**：run 期间的 token 刷新随隔离目录清理丢弃，维持 keepalive 设计的"run 不回写"决策。
- **不做保活历史 / per-credential cron / UI 变更**（与本次无关）。
- **不做启动期 stale `/tmp/dws-env-*` 清理**（YAGNI；崩溃残留由 `rmtree(ignore_errors=True)` 兜底）。
- **不改数据模型 / 迁移**（纯代码改动）。

## 关键决策摘要

| 维度 | 决策 |
|---|---|
| 方向 | 隔离 HOME + `DWS_DISABLE_KEYCHAIN=1`，取消锁（方案 A） |
| 隔离粒度 | 每个操作一个临时根目录；runner 单次 run 的 import + docupipe 主进程**共享**同一隔离会话 |
| DEK 后端 | 文件型（`DWS_DISABLE_KEYCHAIN=1` 强制），可移植 |
| 共享 helper | 新增 `services/dws_env.py::isolated_dws_env()` 同步上下文管理器 |
| `_run_dws` | 增加 `env` 参数（默认 `None` 保持继承 `os.environ` 旧行为） |
| 删除 | `_dws_lock` 字段、`_ensure_dws_state` 方法、所有"操作前 logout 清场" |
| 移植性 | device-flow 改用隔离 env 后 export 产出可移植 blob |
| 并发 | runner `Semaphore` 限流照常；dws 操作间无锁、可真并发 |
| 存量 dev blob | Keychain-bound 的 dev blob 需重登；prod 无影响，不写兼容 |

## 架构：共享隔离 env helper

新增 `docupipe_manager/services/dws_env.py`：

```python
import os
import shutil
from contextlib import contextmanager
from tempfile import mkdtemp
from typing import Iterator


@contextmanager
def isolated_dws_env() -> Iterator[dict[str, str]]:
    """分配一次性、自包含的 dws 状态目录，yield 要传给 dws/docupipe 子进程的 env。

    - DWS_DISABLE_KEYCHAIN=1 → 文件型 DEK 后端（可移植、不碰 OS keychain）。
    - HOME / DWS_CONFIG_DIR / DWS_CACHE_DIR → 同一个临时根目录。
    退出时 best-effort rmtree。
    """
    root = mkdtemp(prefix="dws-env-")
    env = {
        **os.environ,
        "HOME": root,
        "DWS_CONFIG_DIR": os.path.join(root, "dws-config"),
        "DWS_CACHE_DIR": os.path.join(root, "dws-cache"),
        "DWS_DISABLE_KEYCHAIN": "1",
    }
    try:
        yield env
    finally:
        shutil.rmtree(root, ignore_errors=True)
```

说明：

- `mkdtemp`/`rmtree` 是同步且极快，在 async 方法里直接 `with` 使用即可，无需 async 封装。
- 同时设 `HOME` 与 `DWS_CONFIG_DIR`/`DWS_CACHE_DIR`：实验显示 DEK 文件落在 `$HOME/Library/Application Support/dws-cli/dek`，identity/logs 走 `DWS_CONFIG_DIR`，缓存走 `DWS_CACHE_DIR`。三者都指向同一 `root` 子树，退出统一清理。
- **关键不变量**：任何触碰 dws 状态的子进程都必须拿到此 helper 产出的 `env`。

## 后端服务变更

### `CredentialService`（`services/credential_service.py`）

- **`__init__`**：删除 `self._dws_lock = asyncio.Lock()`。
- **`_run_dws`**：签名加 `env: dict[str, str] | None = None`，透传给 `create_subprocess_exec(..., env=env)`。默认 `None` 保持旧行为（继承 `os.environ`），向后兼容现有调用与测试。
- **`start_device_login`**：包进 `with isolated_dws_env() as env:`，login 子进程用该 env；删除 `os.makedirs(.../Library/Keychains)`（文件 DEK 不需要）。`_active_sessions[session_key]` 改为存**完整 env**（而非仅 `home_dir`），并把临时根目录记入 session 以便后续清理。
- **`finalize_login`**：status / export 子进程改用 session 里存的隔离 env。**由此 export 产出可移植 blob**（修移植性 bug）。
- **`_probe_auth_blob`**：`with isolated_dws_env() as env:` 包住整段；删除 `async with self._dws_lock`；删除开头的 `auth logout`（全新目录无需清场）。import/status 子进程用 `env`。
- **`refresh_credential`**：`with isolated_dws_env() as env:` 包住 dws 操作段；删除 `async with self._dws_lock`、删除 `await self._ensure_dws_state()`、删除首尾的 `auth logout`。所有 `_run_dws(...)` 调用传 `env=env`。
- **`_ensure_dws_state`**：**整段删除**。临时目录在 import 时自动 bootstrap，无需预热真实 `~/.dws`。
- **`_cleanup_session`**：随 session 结构调整，rmtree 临时根目录（原 `home_dir` 字段语义改为 root）。

### `RunnerService`（`services/runner_service.py`）

- **`_import_credential(ctx, env)`**：签名增加 `env` 参数；blob 临时文件写入该 env 的 root 子目录（随 rmtree 清理，不再单独 `os.unlink`）；import 子进程用 `env`；删除前置 `auth logout`。
- **`_do_execute`**：用**一个** `with isolated_dws_env() as dws_env:` 包住 "import + 跑 docupipe" 整段；`run_env = {**dws_env, **ctx.project_env}`（project_env 优先）传给 `_stream_subprocess`；退出 `with` 自动 rmtree 隔离目录。删除 `finally` 里的 `auth logout` 与 `os.unlink(auth_temp_path)`。
- **`_stream_subprocess`**：签名增加 `env: dict[str, str]` 参数；`create_subprocess_exec(..., env=env, cwd=project_dir)`。

### 逐调用点改动总表

| 文件:方法 | 现在 | 改成 |
|---|---|---|
| `credential_service.py:start_device_login` | `HOME=home_dir` + 建 `Library/Keychains`，无 flag | `isolated_dws_env()`，删 Keychains 目录；session 存完整 env |
| `credential_service.py:finalize_login` | session home_dir，无 flag | session 隔离 env → export 可移植 |
| `credential_service.py:_probe_auth_blob` | 真实 HOME + `_dws_lock` + 前置 logout | `isolated_dws_env()`，去锁、去 logout |
| `credential_service.py:refresh_credential` | 真实 HOME + `_dws_lock` + `_ensure_dws_state` + 首尾 logout | `isolated_dws_env()`，去锁、去 ensure、去 logout |
| `credential_service.py:_run_dws` | 无 env 参数 | 加 `env=None` 透传 |
| `credential_service.py:_ensure_dws_state` | 存在（仅 refresh 调用） | **删除** |
| `runner_service.py:_import_credential` | 真实 HOME，无锁，前置 logout | 接收 `env`，import 进隔离目录；去 logout |
| `runner_service.py:_do_execute` | import→跑→logout，env 各自 `os.environ` | **一个** `with isolated_dws_env()` 包住 import+docupipe；`run_env={**dws_env,**project_env}`；去 finally logout |
| `runner_service.py:_stream_subprocess` | `env={**os.environ,**project_env}` | `env` 由调用方传入 |

## 删除项汇总

- `CredentialService._dws_lock`（字段 + `__init__` 初始化）。
- `CredentialService._ensure_dws_state`（整个方法）。
- 所有"操作前 `auth logout` 清场"调用（credential_service 2 处、runner 2 处）。
- runner `_do_execute` finally 里的 `os.unlink(auth_temp_path)` 与 `auth logout`（临时目录随 rmtree 消失）。

## 边界与风险

1. **存量 dev blob（Keychain-bound）**：若 dev 曾在**未设 flag** 的 macOS 上用 device-flow 创建凭证，该 blob 是 Keychain-bound，在新隔离 env 里 `import` 会失败。**仅影响 dev**——生产是 Linux 无 Keychain，存量 blob 本就是文件 DEK、可移植。dev 重建一次凭证即可，**不写兼容代码**（YAGNI）。
2. **docupipe 子进程吃隔离 env**：它继承传入的 env，天然读到隔离目录的 dws 状态（这正是 runner "先 import 再跑"的原因）。run 期间 token 刷新随目录清理丢弃，符合"run 不回写"。
3. **临时目录残留**：进程崩溃时 `rmtree(ignore_errors=True)` 兜底；不做启动期 stale `/tmp/dws-env-*` 清理。
4. **并发安全**：隔离后 dws 操作间无共享状态，可真并发。runner `Semaphore(max_concurrent_runs)` 仍限流 docupipe 进程数；keepalive 与 run 可并发。

## 测试策略

沿用现有目录（`tests/services`、`tests/api`）与 `conftest.py` fixture。dws 子进程用 mock。

| 层 | 文件 | 覆盖点 |
|---|---|---|
| helper | `tests/services/test_dws_env.py`（新建） | `isolated_dws_env` 创建三个目录 + flag、yield 的 env 含正确键值、退出后目录被清理 |
| credential | `tests/services/test_credential_service.py`（追加+回归） | 传给 `create_subprocess_exec` 的 env 含 `DWS_DISABLE_KEYCHAIN=1` + 三目录；`_dws_lock` / `_ensure_dws_state` 不再存在；`_probe_auth_blob`/`refresh_credential` 不再调用 logout；`_run_dws(env=...)` 透传 |
| runner | `tests/services/test_runner_service.py`（追加+回归） | `_import_credential` 与 `_stream_subprocess` 共享同一 env；docupipe 子进程 env 含隔离键 + project_env 优先；finally 不再 logout |
| 集成 | `@pytest.mark.integration`（默认跳过） | **补本次未跑成的全循环**：用 flag 重建可移植 blob，在隔离 env 跑 `import→status→wiki space list→export`，断言真实 `~/.dws` 全程未被触碰、iso 导出 blob 可二次 import |

## 实施顺序建议

1. 新增 `services/dws_env.py` + 单测。
2. 改 `_run_dws` 加 `env` 参数（不改行为，先过现有测试）。
3. 改 `CredentialService`（device-flow / `_probe_auth_blob` / `refresh_credential`），删 `_dws_lock` / `_ensure_dws_state` / logout 清场；更新单测。
4. 改 `RunnerService`（`_import_credential` / `_do_execute` / `_stream_subprocess`）；更新单测。
5. 补集成测试；本地用 flag 重建一个 dev 凭证做端到端冒烟。
