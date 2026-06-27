# dws 状态隔离与取消全局锁 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用隔离的临时 `HOME`/`DWS_CONFIG_DIR`/`DWS_CACHE_DIR` + `DWS_DISABLE_KEYCHAIN=1` 取代 `_dws_lock`，让所有 dws 子进程互不干扰、可真并发，并修好 blob 移植性。

**Architecture:** 新增共享 helper `services/dws_env.py`（`make_dws_env` + `isolated_dws_env`）。`CredentialService` 与 `RunnerService` 所有触碰 dws 状态的子进程都改用隔离 env；runner 的 import 与 docupipe 主进程共享同一隔离会话。删除 `_dws_lock`、`_ensure_dws_state`、所有"操作前 logout 清场"。

**Tech Stack:** Python 3.12, asyncio, FastAPI, SQLAlchemy[asyncio], pytest + pytest-asyncio (auto mode)。

**Spec:** `docs/superpowers/specs/2026-06-28-dws-state-isolation-design.md`

## Global Constraints

- 生产部署：`python:3.12-slim` Linux 容器，单 uvicorn worker（`Dockerfile` CMD）。
- dws CLI：`v1.0.39`，隐藏 env `DWS_DISABLE_KEYCHAIN`/`DWS_CONFIG_DIR`/`DWS_CACHE_DIR` 已验证生效。
- 测试命令：`pytest`（项目根 `.venv` 可用；等价 `uv run pytest`）。`asyncio_mode = "auto"`；`integration` marker 默认跳过（`addopts = "-m 'not integration'"`）。
- 加密 key：`settings.encryption_key`（32 hex 字符），SM4（`docupipe_manager.crypto`）。
- 现有约定：mock 子进程用 `unittest.mock.AsyncMock`/`patch`；session 用 `_session_factory`（`async_sessionmaker`）。
- 所有 dws 子进程现在都**不**碰真实 `~/.dws`；每个操作自包含。
- 不改数据模型 / 迁移；不为存量 dev Keychain-bound blob 写兼容代码。

## File Structure

| 文件 | 责任 | 本计划动作 |
|---|---|---|
| `docupipe_manager/services/dws_env.py` | **新建**：`make_dws_env(root)` 构造隔离 env dict；`isolated_dws_env()` 上下文管理器（mkdtemp+rmtree） | Task 1 |
| `docupipe_manager/services/credential_service.py` | 凭证生命周期；`_run_dws`/`_probe_auth_blob`/`refresh_credential`/`start_device_login`/`finalize_login`/`_cleanup_session` | Task 2–5 |
| `docupipe_manager/services/runner_service.py` | docupipe 执行；`_import_credential`/`_do_execute`/`_stream_subprocess` | Task 6 |
| `tests/services/test_dws_env.py` | **新建**：helper 单测 | Task 1 |
| `tests/services/test_credential_service.py` | 追加 + 回归 | Task 2–5 |
| `tests/services/test_runner_service.py` | 追加 + 回归 | Task 6 |
| `tests/services/test_dws_isolation_integration.py` | **新建**：`@pytest.mark.integration` 全循环 | Task 7 |

---

### Task 1: 新增 `dws_env` helper

**Files:**
- Create: `docupipe_manager/services/dws_env.py`
- Test: `tests/services/test_dws_env.py`

**Interfaces:**
- Produces: `make_dws_env(root: str) -> dict[str, str]`（构造隔离 env，不分配目录）；`isolated_dws_env()`（`@contextmanager`，yield env dict，退出 rmtree）。

- [ ] **Step 1: 写失败测试**

```python
# tests/services/test_dws_env.py
import os

from docupipe_manager.services.dws_env import isolated_dws_env, make_dws_env


def test_make_dws_env_has_required_keys():
    env = make_dws_env("/tmp/fake-root")
    assert env["HOME"] == "/tmp/fake-root"
    assert env["DWS_CONFIG_DIR"] == "/tmp/fake-root/dws-config"
    assert env["DWS_CACHE_DIR"] == "/tmp/fake-root/dws-cache"
    assert env["DWS_DISABLE_KEYCHAIN"] == "1"
    # 继承当前进程 env（PATH 等）
    assert "PATH" in env


def test_isolated_dws_env_creates_and_cleans_up():
    created = {}
    with isolated_dws_env() as env:
        root = env["HOME"]
        created["root"] = root
        created["exists_during"] = os.path.isdir(root)
        created["env"] = env
    # 退出后目录被清理
    assert created["exists_during"] is True
    assert not os.path.exists(created["root"])
    assert created["env"]["DWS_DISABLE_KEYCHAIN"] == "1"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/services/test_dws_env.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'docupipe_manager.services.dws_env'`

- [ ] **Step 3: 实现 helper**

```python
# docupipe_manager/services/dws_env.py
"""Isolated dws state environments.

Every dws-touching subprocess runs inside an isolated temp HOME/config/cache
with DWS_DISABLE_KEYCHAIN=1, so operations never share ~/.dws and can run
concurrently. The file-based DEK backend (forced by the flag) also makes
auth export/import blobs portable across machines.
"""
import os
import shutil
from contextlib import contextmanager
from tempfile import mkdtemp
from typing import Iterator


def make_dws_env(root: str) -> dict[str, str]:
    """Build an isolated dws env dict pointing all state under ``root``."""
    return {
        **os.environ,
        "HOME": root,
        "DWS_CONFIG_DIR": os.path.join(root, "dws-config"),
        "DWS_CACHE_DIR": os.path.join(root, "dws-cache"),
        "DWS_DISABLE_KEYCHAIN": "1",
    }


@contextmanager
def isolated_dws_env() -> Iterator[dict[str, str]]:
    """Allocate a one-shot isolated dws env; rmtree the root on exit.

    Use for short-lived operations (probe / refresh / single run). For
    long-lived sessions (device flow) call ``make_dws_env`` directly on a
    mkdtemp root and clean up manually.
    """
    root = mkdtemp(prefix="dws-env-")
    try:
        yield make_dws_env(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/services/test_dws_env.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: 提交**

```bash
git add docupipe_manager/services/dws_env.py tests/services/test_dws_env.py
git commit -m "feat: add isolated_dws_env helper for per-op dws state isolation"
```

---

### Task 2: `_run_dws` 增加 `env` 参数（向后兼容）

**Files:**
- Modify: `docupipe_manager/services/credential_service.py`（`_run_dws` 方法，约 258-276 行）
- Test: `tests/services/test_credential_service.py`（追加）

**Interfaces:**
- Produces: `_run_dws(args, env=None, log_path=None, timeout=120.0) -> (rc, stdout, stderr)`。`env=None` 时不传 `env` kwarg（保持继承 `os.environ` 旧行为，现有测试 `test_run_dws_command_construction` 不动）。

- [ ] **Step 1: 写失败测试（断言 env 透传）**

在 `tests/services/test_credential_service.py` 末尾追加：

```python
@pytest.mark.asyncio
async def test_run_dws_passes_env_when_given(credential_service):
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(b"{}", b""))
    proc.returncode = 0
    env = {"HOME": "/tmp/x", "DWS_DISABLE_KEYCHAIN": "1", "PATH": "/usr/bin"}
    with patch("docupipe_manager.services.credential_service.asyncio.create_subprocess_exec",
               AsyncMock(return_value=proc)) as mock_exec:
        await credential_service._run_dws(["auth", "status"], env=env)
    assert mock_exec.call_args.kwargs["env"] is env
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/services/test_credential_service.py::test_run_dws_passes_env_when_given -v`
Expected: FAIL — `TypeError: _run_dws() got an unexpected keyword argument 'env'`

- [ ] **Step 3: 改 `_run_dws` 签名与实现**

把 `credential_service.py` 中：

```python
    async def _run_dws(self, args: list[str], log_path: str | None = None,
                       timeout: float = 120.0) -> tuple[int, bytes, bytes]:
        proc = await asyncio.create_subprocess_exec(
            self._settings.dws_cli_path, *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
```

替换为：

```python
    async def _run_dws(self, args: list[str], env: dict[str, str] | None = None,
                       log_path: str | None = None,
                       timeout: float = 120.0) -> tuple[int, bytes, bytes]:
        kwargs: dict = {
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
        }
        if env is not None:
            kwargs["env"] = env
        proc = await asyncio.create_subprocess_exec(
            self._settings.dws_cli_path, *args, **kwargs,
        )
```

（方法其余部分 `try/except`/`log_path` 写文件逻辑不变。）

- [ ] **Step 4: 跑测试确认通过（含回归）**

Run: `pytest tests/services/test_credential_service.py -k "run_dws" -v`
Expected: PASS — 新测试 + 现有 `test_run_dws_command_construction`（断言无 `env` kwarg）+ `test_run_dws_nonzero_exit` 全过。

- [ ] **Step 5: 提交**

```bash
git add docupipe_manager/services/credential_service.py tests/services/test_credential_service.py
git commit -m "feat: add env param to _run_dws (backward compatible)"
```

---

### Task 3: `_probe_auth_blob` 改隔离，去锁去 logout

**Files:**
- Modify: `docupipe_manager/services/credential_service.py`（`_probe_auth_blob` 约 207-256 行；`__init__` 49 行的 `self._dws_lock`）
- Test: `tests/services/test_credential_service.py`（追加；保留现有 `test_probe_auth_blob_*`）

**Interfaces:**
- Consumes: `isolated_dws_env`（Task 1）。
- 仍抛 `ValueError`（base64 非法 / import 失败 / 超时），契约不变，`check_status` / `create_from_import` 调用方不动。

- [ ] **Step 1: 写失败测试（断言 env 透传 + 不再 logout）**

在 `tests/services/test_credential_service.py` 追加：

```python
@pytest.mark.asyncio
async def test_probe_auth_blob_uses_isolated_env(credential_service):
    """import 子进程应收到隔离 env（含 DWS_DISABLE_KEYCHAIN），且不再调 logout。"""
    import_proc = AsyncMock(); import_proc.returncode = 0
    import_proc.communicate = AsyncMock(return_value=(b"", b""))
    status_proc = AsyncMock(); status_proc.returncode = 0
    status_proc.communicate = AsyncMock(return_value=(b'{"corp_id":"c"}', b""))
    calls = []

    async def fake_exec(*args, **kwargs):
        calls.append((args, kwargs))
        if "import" in args:
            return import_proc
        return status_proc

    with patch("docupipe_manager.services.credential_service.asyncio.create_subprocess_exec",
               side_effect=fake_exec):
        meta = await credential_service._probe_auth_blob("YWJjZGVm")  # 合法 base64

    assert meta == {"corp_id": "c"}
    # 没有 logout 子进程
    assert not any("logout" in a[0] for a in calls)
    # import 子进程拿到了隔离 env
    import_call = next(c for a, c in calls if "import" in a)
    assert import_call.kwargs["env"]["DWS_DISABLE_KEYCHAIN"] == "1"
    assert "DWS_CONFIG_DIR" in import_call.kwargs["env"]


def test_credential_service_has_no_dws_lock(credential_service):
    assert not hasattr(credential_service, "_dws_lock")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/services/test_credential_service.py -k "probe_auth_blob_uses_isolated_env or has_no_dws_lock" -v`
Expected: FAIL — `_probe_auth_blob` 未传 env；`_dws_lock` 仍存在。

- [ ] **Step 3: 重写 `_probe_auth_blob`**

把整个 `_probe_auth_blob` 方法替换为：

```python
    async def _probe_auth_blob(self, auth_b64: str) -> dict:
        """import base64 凭证到隔离 env，调 status 返回元数据。
        base64 非法 / import 失败抛 ValueError。"""
        try:
            binascii.a2b_base64(auth_b64.encode("utf-8"))
        except (binascii.Error, ValueError) as e:
            raise ValueError(f"auth_blob 不是合法的 base64: {e}") from e

        with isolated_dws_env() as env:
            import_path = os.path.join(env["HOME"], "auth.b64")
            with open(import_path, "w") as f:
                f.write(auth_b64)

            import_proc = await asyncio.create_subprocess_exec(
                self._settings.dws_cli_path, "auth", "import", "--base64", "-i", import_path,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
            )
            try:
                stdout, stderr = await asyncio.wait_for(import_proc.communicate(), timeout=30)
            except asyncio.TimeoutError:
                import_proc.kill()
                raise ValueError("dws auth import 超时")
            if import_proc.returncode != 0:
                detail = stderr.decode().strip() if stderr else ""
                msg = "dws auth import 失败：凭证无效"
                if detail:
                    msg += f"（{detail}）"
                raise ValueError(msg)

            status_proc = await asyncio.create_subprocess_exec(
                self._settings.dws_cli_path, "auth", "status", "--format", "json",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
            )
            try:
                stdout, _ = await asyncio.wait_for(status_proc.communicate(), timeout=30)
            except asyncio.TimeoutError:
                status_proc.kill()
                raise ValueError("dws auth status 超时")
            return json.loads(stdout.decode()) if stdout else {}
```

并在文件顶部 import 区追加（紧挨现有 `from tempfile import mkdtemp`）：

```python
from docupipe_manager.services.dws_env import isolated_dws_env
```

- [ ] **Step 4: 暂不删 `_dws_lock`（Task 4 一起删），先让 probe 测试过；跑 probe 相关测试**

Run: `pytest tests/services/test_credential_service.py -k "probe" -v`
Expected: `test_probe_auth_blob_uses_isolated_env` PASS；`test_probe_auth_blob_invalid_base64`、`test_probe_auth_blob_import_fails` 仍 PASS（mock 不关心 logout 是否被调）。`test_credential_service_has_no_dws_lock` 仍 FAIL（下个 task 删字段）。

- [ ] **Step 5: 提交**

```bash
git add docupipe_manager/services/credential_service.py tests/services/test_credential_service.py
git commit -m "refactor: _probe_auth_blob uses isolated dws env, drops logout"
```

---

### Task 4: `refresh_credential` 改隔离；删 `_ensure_dws_state` 与 `_dws_lock`

**Files:**
- Modify: `docupipe_manager/services/credential_service.py`（`__init__` 49 行；`_ensure_dws_state` 278-287 行整删；`refresh_credential` 357-401 行的 dws 操作段）
- Test: `tests/services/test_credential_service.py`（删 `_ensure_dws_state` 两个测试；追加隔离断言）

**Interfaces:**
- Consumes: `isolated_dws_env`、`_run_dws(env=...)`。
- `refresh_credential` 对外契约不变（读 active 凭证→建 Job→刷新→回写；失败记 Job failed）。

- [ ] **Step 1: 删 `_ensure_dws_state` 的两个测试**

删除 `tests/services/test_credential_service.py` 中 `test_ensure_dws_state_already_exists`（276-278）与 `test_ensure_dws_state_bootstraps`（281-290）两个函数整体。

- [ ] **Step 2: 写新失败测试（断言 refresh 用隔离 env、不调 logout/ensure）**

在文件末尾追加：

```python
@pytest.mark.asyncio
async def test_refresh_credential_uses_isolated_env(credential_service, tmp_path):
    from docupipe_manager.models.dws_credential import CredentialStatus
    cid = uuid.uuid4()
    cred = MagicMock()
    cred.id = cid; cred.status = CredentialStatus.active
    cred.auth_blob = b"\x00"; cred.token_expires_at = None; cred.refresh_token_expires_at = None

    sessions = []
    for _ in range(5):
        ms = AsyncMock(); ms.__aenter__ = AsyncMock(return_value=ms); ms.__aexit__ = AsyncMock(return_value=None)
        ms.commit = AsyncMock(); ms.execute = AsyncMock(); ms.refresh = AsyncMock()
        sessions.append(ms)
    idx = {"i": 0}
    def factory():
        s = sessions[idx["i"]]; idx["i"] += 1; return s
    credential_service._session_factory = factory
    sessions[0].get = AsyncMock(return_value=cred)
    sessions[1].add = MagicMock()
    sessions[2].get = AsyncMock(return_value=cred)
    sessions[3].get = AsyncMock(return_value=cred)
    credential_service._settings.data_dir = str(tmp_path)

    seen_envs = []

    async def fake_exec(*args, **kwargs):
        seen_envs.append(kwargs.get("env"))
        proc = AsyncMock(); proc.returncode = 0
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

    # 没有 logout 子进程
    assert not hasattr(credential_service, "_dws_lock")
    # 所有子进程拿到同一个隔离 env（同一 HOME）
    homes = {e["HOME"] for e in seen_envs if e}
    assert len(homes) == 1
    assert next(iter(homes)) != os.environ.get("HOME")
```

文件顶部如未 import `os` 则补 `import os`。

- [ ] **Step 3: 跑测试确认失败**

Run: `pytest tests/services/test_credential_service.py -k "refresh_credential or ensure_dws_state or has_no_dws_lock" -v`
Expected: FAIL — `_dws_lock` 仍存在；refresh 仍在真实 HOME 跑。

- [ ] **Step 4: 删 `_dws_lock` 字段**

`credential_service.py` `__init__` 中删除这行：

```python
        self._dws_lock = asyncio.Lock()
```

- [ ] **Step 5: 删 `_ensure_dws_state` 整个方法**（约 278-287 行）。

- [ ] **Step 6: 重写 `refresh_credential` 的 dws 操作段**

把 `refresh_credential` 中：

```python
        try:
            await self._ensure_dws_state()
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
                        raise CredentialError(f"dws auth import failed (exit {rc})")

                    async with self._session_factory() as session:
                        await session.execute(update(Job).where(Job.id == job.id).values(
                            status=JobStatus.running, started_at=started_at, log_path=log_path))
                        await session.commit()

                    rc, _, _ = await self._run_dws(["wiki", "space", "list"], log_path=log_path)
                    if rc != 0:
                        raise CredentialError(f"dws wiki space list failed (exit {rc})")

                    rc, status_out, _ = await self._run_dws(["auth", "status", "--format", "json"],
                                                            log_path=log_path)
                    meta = json.loads(status_out.decode()) if status_out else {}

                    fd2, tmp_export = tempfile.mkstemp(suffix=".b64", prefix="dws-keepalive-export-")
                    os.close(fd2)
                    rc, _, _ = await self._run_dws(["auth", "export", "--base64", "-o", tmp_export],
                                                   log_path=log_path)
                    if rc != 0 or not os.path.exists(tmp_export):
                        raise CredentialError("dws auth export failed")
                    with open(tmp_export, "r") as f:
                        new_blob = f.read().strip()
                    os.unlink(tmp_export)
                finally:
                    try:
                        os.unlink(tmp_import)
                    except OSError:
                        pass
                    try:
                        await self._run_dws(["auth", "logout"])
                    except Exception:
                        pass
```

替换为：

```python
        try:
            with isolated_dws_env() as env:
                import_path = os.path.join(env["HOME"], "auth.b64")
                with open(import_path, "w") as f:
                    f.write(auth_b64)
                rc, _, _ = await self._run_dws(["auth", "import", "--base64", "-i", import_path],
                                               env=env, log_path=log_path)
                if rc != 0:
                    raise CredentialError(f"dws auth import failed (exit {rc})")

                async with self._session_factory() as session:
                    await session.execute(update(Job).where(Job.id == job.id).values(
                        status=JobStatus.running, started_at=started_at, log_path=log_path))
                    await session.commit()

                rc, _, _ = await self._run_dws(["wiki", "space", "list"], env=env, log_path=log_path)
                if rc != 0:
                    raise CredentialError(f"dws wiki space list failed (exit {rc})")

                rc, status_out, _ = await self._run_dws(["auth", "status", "--format", "json"],
                                                        env=env, log_path=log_path)
                meta = json.loads(status_out.decode()) if status_out else {}

                export_path = os.path.join(env["HOME"], "export.b64")
                rc, _, _ = await self._run_dws(["auth", "export", "--base64", "-o", export_path],
                                               env=env, log_path=log_path)
                if rc != 0 or not os.path.exists(export_path):
                    raise CredentialError("dws auth export failed")
                with open(export_path, "r") as f:
                    new_blob = f.read().strip()
```

（后续 `new_blob_hex = encrypt_sm4(...)` 到方法末尾的 DB 回写 + Job succeeded + except 分支不变。）

- [ ] **Step 7: 跑全量 credential 测试**

Run: `pytest tests/services/test_credential_service.py -v`
Expected: PASS — 含新 `test_refresh_credential_uses_isolated_env`、`test_credential_service_has_no_dws_lock`；现有 `test_refresh_credential_success_writes_back` / `_api_failure_marks_job_failed` / `_skips_inactive` / `_skips_none` 回归通过；`_ensure_dws_state` 测试已删。

- [ ] **Step 8: 提交**

```bash
git add docupipe_manager/services/credential_service.py tests/services/test_credential_service.py
git commit -m "refactor: refresh_credential uses isolated dws env; remove _dws_lock and _ensure_dws_state"
```

---

### Task 5: `start_device_login` / `finalize_login` / `_cleanup_session` 改隔离

**Files:**
- Modify: `docupipe_manager/services/credential_service.py`（`start_device_login` 51-81；`finalize_login` 108-171；`_cleanup_session` 464-470）
- Test: `tests/services/test_credential_service.py`（更新 `test_finalize_login_persists_expires`；追加 device-login env 断言）

**Interfaces:**
- Consumes: `make_dws_env`（Task 1，device flow 需长生命周期，不能用自动 rmtree 的 `isolated_dws_env`）。
- `_active_sessions[session_key]` 结构：`{proc, root, env, name, project_id, created_at}`（原 `home_dir` → `root`/`env`）。

- [ ] **Step 1: 更新现有 `test_finalize_login_persists_expires` 的 session 结构**

把 `tests/services/test_credential_service.py` 中：

```python
    session_obj = {"home_dir": "/tmp/fake-home", "name": "n", "project_id": pid}
```

改为：

```python
    session_obj = {"root": "/tmp/fake-home",
                   "env": {"HOME": "/tmp/fake-home", "DWS_DISABLE_KEYCHAIN": "1",
                           "DWS_CONFIG_DIR": "/tmp/fake-home/dws-config",
                           "DWS_CACHE_DIR": "/tmp/fake-home/dws-cache", "PATH": "/usr/bin"},
                   "name": "n", "project_id": pid}
```

- [ ] **Step 2: 写新失败测试（device login 用隔离 env、无 Keychains 目录）**

追加：

```python
@pytest.mark.asyncio
async def test_start_device_login_uses_isolated_env(credential_service):
    pid = uuid.uuid4()
    proc = AsyncMock()
    proc.stdout.readline = AsyncMock(return_value=b'{"verification_url":"http://x","user_code":"ABC"}')
    with patch("docupipe_manager.services.credential_service.asyncio.create_subprocess_exec",
               AsyncMock(return_value=proc)) as mock_exec, \
         patch("docupipe_manager.services.credential_service.mkdtemp",
               return_value="/tmp/dws-device-FAKE"):
        result = await credential_service.start_device_login(pid, "n")
    kw = mock_exec.call_args.kwargs
    assert kw["env"]["DWS_DISABLE_KEYCHAIN"] == "1"
    assert kw["env"]["DWS_CONFIG_DIR"] == "/tmp/dws-device-FAKE/dws-config"
    assert kw["cwd"] == "/tmp/dws-device-FAKE"
    sess = credential_service._active_sessions[result["session_key"]]
    assert sess["root"] == "/tmp/dws-device-FAKE"
    assert sess["env"]["DWS_DISABLE_KEYCHAIN"] == "1"
```

- [ ] **Step 3: 跑测试确认失败**

Run: `pytest tests/services/test_credential_service.py -k "start_device_login_uses_isolated_env or finalize_login_persists_expires" -v`
Expected: FAIL — device login 未设 flag / 未存 env；finalize 读不到 `session["env"]`（KeyError）。

- [ ] **Step 4: 重写 `start_device_login`**

把 `start_device_login`（从 `session_key = ...` 到 `return {...}`）替换为：

```python
        session_key = uuid.uuid4().hex
        root = mkdtemp(prefix="dws-device-")
        env = make_dws_env(root)

        proc = await asyncio.create_subprocess_exec(
            self._settings.dws_cli_path, "auth", "login", "--device",
            "--format", "json",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env=env, cwd=root,
        )

        try:
            first_chunk = await asyncio.wait_for(proc.stdout.readline(), timeout=30)
            info = json.loads(first_chunk)
        except Exception as e:
            proc.kill()
            shutil.rmtree(root, ignore_errors=True)
            raise ValueError(f"Failed to start device login: {e}") from e

        self._active_sessions[session_key] = {
            "proc": proc,
            "root": root,
            "env": env,
            "name": name,
            "project_id": project_id,
            "created_at": time.monotonic(),
        }

        return {"session_key": session_key, **info}
```

（删除原 `os.makedirs(os.path.join(home_dir, "Library", "Keychains"), exist_ok=True)`——文件 DEK 不需要。）

在 import 区把 `from docupipe_manager.services.dws_env import isolated_dws_env` 改为：

```python
from docupipe_manager.services.dws_env import isolated_dws_env, make_dws_env
```

- [ ] **Step 5: 重写 `finalize_login` 用 session env**

把 `finalize_login` 中读 `home_dir` 与构造 status/export 子进程那段：

```python
        session = self._active_sessions.get(session_key)
        if session is None:
            raise ValueError("Session not found or expired")
        home_dir = session["home_dir"]

        try:
            status_proc = await asyncio.create_subprocess_exec(
                self._settings.dws_cli_path, "auth", "status", "--format", "json",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "HOME": home_dir},
            )
            ...
            export_path = os.path.join(home_dir, "dws-export.b64")
            export_proc = await asyncio.create_subprocess_exec(
                self._settings.dws_cli_path, "auth", "export", "--base64", "-o", export_path,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "HOME": home_dir},
            )
```

替换为：

```python
        session = self._active_sessions.get(session_key)
        if session is None:
            raise ValueError("Session not found or expired")
        env = session["env"]
        root = session["root"]

        try:
            status_proc = await asyncio.create_subprocess_exec(
                self._settings.dws_cli_path, "auth", "status", "--format", "json",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout, _ = await status_proc.communicate()
            status_data = json.loads(stdout.decode()) if stdout else {}
            corp_id = status_data.get("corp_id", "")
            token_expires_at_str = status_data.get("expires_at")
            refresh_expires_at_str = status_data.get("refresh_expires_at")

            export_path = os.path.join(root, "dws-export.b64")
            export_proc = await asyncio.create_subprocess_exec(
                self._settings.dws_cli_path, "auth", "export", "--base64", "-o", export_path,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env=env,
            )
```

（后续 `await export_proc.communicate()` 起的逻辑不变。）

- [ ] **Step 6: 改 `_cleanup_session` 用 root**

把 `_cleanup_session` 中：

```python
            shutil.rmtree(session.get("home_dir", ""), ignore_errors=True)
```

改为：

```python
            shutil.rmtree(session.get("root", ""), ignore_errors=True)
```

- [ ] **Step 7: 跑全量 credential 测试**

Run: `pytest tests/services/test_credential_service.py -v`
Expected: PASS（含 device-login 新测试与更新后的 finalize 测试）。

- [ ] **Step 8: 提交**

```bash
git add docupipe_manager/services/credential_service.py tests/services/test_credential_service.py
git commit -m "refactor: device-flow login/finalize use isolated dws env (portable export)"
```

---

### Task 6: `RunnerService` 改共享隔离会话

**Files:**
- Modify: `docupipe_manager/services/runner_service.py`（`_import_credential` 214-234；`_stream_subprocess` 236-244；`_do_execute` 322-360）
- Test: `tests/services/test_runner_service.py`（更新 `test_do_execute_flushes...` / `test_do_execute_truncates...` 的 side_effect；追加隔离断言）

**Interfaces:**
- Consumes: `isolated_dws_env`（Task 1）。
- `_import_credential(ctx, env)`：写 blob 到 `env["HOME"]`，import，无 logout。
- `_stream_subprocess(..., env)`：env 由调用方传入（已含隔离 dws env + project_env）。

- [ ] **Step 1: 写新失败测试（import 与 docupipe 共享同一隔离 HOME；无 logout）**

在 `tests/services/test_runner_service.py` 追加：

```python
@pytest.mark.asyncio
async def test_do_execute_shares_isolated_env_and_no_logout(runner_service, tmp_path):
    """import 与 docupipe 子进程共享同一隔离 HOME；不再调 auth logout。"""
    rid = uuid.uuid4()
    runner_service._active_runs.add(rid)
    task_id = uuid.uuid4()

    run_mock = MagicMock(); run_mock.id = rid; run_mock.task_id = task_id
    run_mock.mode = "incremental"; run_mock.pipeline_name = None
    task_mock = MagicMock(); task_mock.id = task_id
    task_mock.credential_id = uuid.uuid4()
    task_mock.credential_type = CredentialType.dws
    task_mock.config_yaml = "k: v"; task_mock.slug = "demo"
    cred_mock = MagicMock(); cred_mock.auth_blob.hex = MagicMock(return_value="00" * 16)

    sessions = [MagicMock(), MagicMock(), MagicMock(), MagicMock()]
    idx = {"i": 0}
    def fake_factory():
        s = sessions[idx["i"]]; idx["i"] += 1
        cm = MagicMock(); cm.__aenter__ = AsyncMock(return_value=s); cm.__aexit__ = AsyncMock(return_value=None)
        return cm
    sessions[0].get = AsyncMock(side_effect=[run_mock, task_mock, cred_mock])
    sessions[0].execute = AsyncMock(return_value=_empty_env_result())
    sessions[1].execute = AsyncMock(); sessions[1].commit = AsyncMock()
    sessions[2].execute = AsyncMock(); sessions[2].commit = AsyncMock()
    sessions[3].execute = AsyncMock(); sessions[3].commit = AsyncMock()
    runner_service._session_factory = fake_factory
    runner_service._settings.data_dir = str(tmp_path)

    with patch("docupipe_manager.services.runner_service.decrypt_sm4", return_value="auth"), \
         patch("asyncio.create_subprocess_exec", new=AsyncMock()) as mock_sub:
        import_proc = MagicMock(); import_proc.communicate = AsyncMock(return_value=(b"", b""))
        docupipe_proc = MagicMock()
        docupipe_proc.stdout.readline = AsyncMock(side_effect=[b"hi\n", b""])
        docupipe_proc.wait = AsyncMock(return_value=0); docupipe_proc.pid = 1
        mock_sub.side_effect = [import_proc, docupipe_proc]

        await runner_service._do_execute(rid)

    # 只有 import + docupipe 两个子进程，没有 logout
    all_args = [list(c[0]) for c in mock_sub.call_args_list]
    assert not any("logout" in a for a in all_args)
    envs = [c.kwargs.get("env") for c in mock_sub.call_args_list]
    homes = {e["HOME"] for e in envs}
    assert len(homes) == 1                       # 共享同一隔离 HOME
    assert envs[0]["DWS_DISABLE_KEYCHAIN"] == "1"
    assert next(iter(homes)) != os.environ.get("HOME")
```

文件顶部如未 import `os`，确认已有（第 1 行 `import os` 已存在）。

- [ ] **Step 2: 更新现有 runner 测试的 side_effect（少一个 logout 子进程）**

在 `test_do_execute_flushes_and_broadcasts_each_line`（约 250-257 行）把：

```python
        mock_sub.side_effect = [proc1, proc1, proc2]
```

改为：

```python
        mock_sub.side_effect = [proc1, proc2]
```

并在该测试 `with patch(...)` 块中**删除** `patch("tempfile.mkstemp", ...)` 那一行（不再用 mkstemp）。

对 `test_do_execute_truncates_log_file_at_max_bytes`（约 318-328 行）做同样两处改动：`side_effect = [proc1, proc2]`，删 `patch("tempfile.mkstemp", ...)`。

- [ ] **Step 3: 跑测试确认失败**

Run: `pytest tests/services/test_runner_service.py -k "do_execute" -v`
Expected: FAIL — `_import_credential` 签名不接 env；`_stream_subprocess` 仍用 `os.environ`。

- [ ] **Step 4: 重写 `_import_credential`**

把 `runner_service.py` 中 `_import_credential` 整体替换为：

```python
    async def _import_credential(self, ctx: _RunContext, env: dict[str, str]) -> None:
        key_hex = self._settings.encryption_key
        auth_b64 = decrypt_sm4(ctx.credential.auth_blob.hex(), key_hex)

        import_path = os.path.join(env["HOME"], "auth.b64")
        with open(import_path, "w") as f:
            f.write(auth_b64)

        import_proc = await asyncio.create_subprocess_exec(
            self._settings.dws_cli_path, "auth", "import", "--base64", "-i", import_path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
        )
        await import_proc.communicate()
```

- [ ] **Step 5: `_stream_subprocess` 接收 `env`**

把 `_stream_subprocess` 签名与 `create_subprocess_exec` 调用改为：

```python
    async def _stream_subprocess(
        self, cmd: list[str], env: dict[str, str],
        project_dir: str, log_path: str, run_id: uuid.UUID,
    ) -> tuple[int, str | None]:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            env=env,
            cwd=project_dir,
        )
```

（方法其余不变。）

- [ ] **Step 6: 重写 `_do_execute` 的执行段**

把 `_do_execute` 中 `auth_temp_path = None / imported = False` 起到方法末尾的 `finally` 整体替换为：

```python
        with isolated_dws_env() as dws_env:
            if ctx.credential is not None:
                await self._import_credential(ctx, dws_env)

            started_at = datetime.now(timezone.utc)
            async with self._session_factory() as session:
                await session.execute(
                    update(Job).where(Job.id == run_id).values(
                        status=JobStatus.running, started_at=started_at,
                        log_path=log_path, command_text=command_text,
                    )
                )
                await session.commit()

            run_env = {**dws_env, **ctx.project_env}
            exit_code, error_message = await self._stream_subprocess(
                cmd, run_env, project_dir, log_path, run_id,
            )

            await self._finalize_run(run_id, exit_code, error_message, ctx.task.id)
```

（删掉原 `try/finally`、`auth_temp_path`、`imported`、finally 里的 `os.unlink` 与 `auth logout`——隔离 root 由 `with` 退出时 rmtree。）

- [ ] **Step 7: import helper**

在 `runner_service.py` import 区（紧挨 `from docupipe_manager.config import Settings`）加：

```python
from docupipe_manager.services.dws_env import isolated_dws_env
```

- [ ] **Step 8: 跑全量 runner 测试**

Run: `pytest tests/services/test_runner_service.py -v`
Expected: PASS — 含新 `test_do_execute_shares_isolated_env_and_no_logout`；`test_do_execute_flushes...` / `_truncates...` / `_runs_without_credential` / `_injects_project_env...` 回归通过。

- [ ] **Step 9: 补断言到 `test_do_execute_injects_project_env_into_subprocess`**

在该测试末尾的断言块追加：

```python
    assert env_passed["DWS_DISABLE_KEYCHAIN"] == "1"
    assert env_passed["MY_PLAIN"] == "hello"
    assert env_passed["MY_SECRET"] == "topsecret"
```

（替换原有三条，确保隔离 flag 与 project env 同时存在。）

- [ ] **Step 10: 提交**

```bash
git add docupipe_manager/services/runner_service.py tests/services/test_runner_service.py
git commit -m "refactor: runner shares one isolated dws env across import + docupipe run"
```

---

### Task 7: 集成测试（默认跳过）

**Files:**
- Create: `tests/services/test_dws_isolation_integration.py`

**Interfaces:**
- 验证 spec"事实核查"中本次未跑成的全循环：用 flag 在隔离 env 跑 `import→status→wiki space list→export`，断言真实 `~/.dws` 全程未被触碰。

- [ ] **Step 1: 写集成测试**

```python
"""Integration: real dws CLI in an isolated env (skipped by default).

Run with: pytest -m integration
Requires: a portable auth blob exported with DWS_DISABLE_KEYCHAIN=1, path in
env DOCUPIPE_TEST_DWS_BLOB. Skips if not provided.
"""
import os
import shutil
import uuid

import pytest

from docupipe_manager.services.dws_env import isolated_dws_env, make_dws_env

pytestmark = [pytest.mark.integration]

DWS = os.environ.get("DWS_CLI_PATH", "dws")
BLOB_ENV = "DOCUPIPE_TEST_DWS_BLOB"


def _require_blob():
    path = os.environ.get(BLOB_ENV)
    if not path or not os.path.isfile(path):
        pytest.skip(f"set {BLOB_ENV} to a flag-exported auth blob to run this test")
    with open(path) as f:
        return f.read().strip()


def _real_dws_files():
    real = os.path.join(os.path.expanduser("~"), ".dws")
    if not os.path.isdir(real):
        return set()
    return {os.path.relpath(os.path.join(r, fn), real) for r, _, fs in os.walk(real) for fn in fs}


async def _run(args, env):
    import asyncio
    proc = await asyncio.create_subprocess_exec(
        DWS, *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
    return proc.returncode, stdout, stderr


@pytest.mark.asyncio
async def test_isolated_dws_cycle_does_not_touch_real_home():
    blob = _require_blob()
    before = _real_dws_files()

    with isolated_dws_env() as env:
        import_path = os.path.join(env["HOME"], "auth.b64")
        with open(import_path, "w") as f:
            f.write(blob)

        rc, out, err = await _run(["auth", "import", "--base64", "-i", import_path], env)
        assert rc == 0, err

        rc, out, _ = await _run(["auth", "status", "--format", "json"], env)
        assert rc == 0
        assert b'"authenticated": true' in out or b'"authenticated":true' in out

        rc, _, err = await _run(["wiki", "space", "list"], env)
        assert rc == 0, err

        export_path = os.path.join(env["HOME"], "export.b64")
        rc, _, err = await _run(["auth", "export", "--base64", "-o", export_path], env)
        assert rc == 0 and os.path.isfile(export_path), err

        # 导出的 blob 应能再次 import（可移植）
        rc2, _, err2 = await _run(["auth", "import", "--base64", "-i", export_path, "--force"], env)
        assert rc2 == 0, err2

    after = _real_dws_files()
    assert before == after, f"real ~/.dws changed during isolated cycle: {before ^ after}"
```

- [ ] **Step 2: 确认默认跳过**

Run: `pytest tests/services/test_dws_isolation_integration.py -v`
Expected: 1 skipped（`-m 'not integration'` 默认生效）。

- [ ] **Step 3: （可选，需可移植 blob）实际跑一次**

```bash
# 先用 flag 在临时环境导出一个可移植 blob（替换成你的真实操作）：
#   DWS_DISABLE_KEYCHAIN=1 HOME=$(mktemp -d) dws auth login --device  # 扫码
#   DWS_DISABLE_KEYCHAIN=1 HOME=<同上> dws auth export --base64 -o /tmp/portable.b64
DOCUPIPE_TEST_DWS_BLOB=/tmp/portable.b64 pytest -m integration tests/services/test_dws_isolation_integration.py -v
```
Expected: PASS — `test_isolated_dws_cycle_does_not_touch_real_home` 通过。

- [ ] **Step 4: 提交**

```bash
git add tests/services/test_dws_isolation_integration.py
git commit -m "test: integration test for isolated dws cycle (import/status/wiki/export)"
```

---

## Self-Review

**Spec coverage**（逐条对照 spec）：
- helper `services/dws_env.py`（`make_dws_env`+`isolated_dws_env`）→ Task 1 ✓
- `_run_dws` 加 `env` 参数 → Task 2 ✓
- `_probe_auth_blob` 隔离、去锁、去 logout → Task 3 ✓
- `refresh_credential` 隔离、删 `_ensure_dws_state`、删 `_dws_lock` → Task 4 ✓
- `start_device_login`/`finalize_login`/`_cleanup_session` 隔离、去 Keychains 目录 → Task 5 ✓
- runner `_import_credential`/`_do_execute`/`_stream_subprocess` 共享隔离会话、去 finally logout/unlink → Task 6 ✓
- 删除项：`_dws_lock`（Task 4）、`_ensure_dws_state`（Task 4）、logout 清场（Task 3/4/6）、mkstemp（Task 3/6）✓
- 集成测试补全循环 → Task 7 ✓

**Placeholder scan**：无 TBD/TODO；每步含完整代码与确切命令。

**Type/命名一致性**：
- helper：`make_dws_env(root)` / `isolated_dws_env()` 全程一致。
- `_run_dws(args, env=None, log_path=None, timeout=120.0)` 在 Task 2 定义、Task 4 调用一致。
- `_import_credential(ctx, env)` 在 Task 6 定义与调用一致。
- session 结构 `{root, env, ...}` 在 Task 5 定义、`_cleanup_session` 消费一致。
- `_stream_subprocess(cmd, env, project_dir, log_path, run_id)` 定义与调用一致。

无遗漏。
