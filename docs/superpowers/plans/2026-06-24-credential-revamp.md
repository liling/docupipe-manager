# 凭证功能改造 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让凭证支持「导入」与「设备码」两种创建方式，界面增加凭证类型选择、有效期展示、可用性测试。

**Architecture:** 后端在 `CredentialService` 提取 `_probe_auth_blob`（import+status）复用于导入创建与测试回写；新增 `create_from_import`、增强 `check_status`（测试并回写）、修复 `finalize_login` 持久化过期时间。API 新增 `POST /import`、`POST /{id}/test`（替代旧 `GET /{id}/status`）、增强 list。前端用统一对话框承载两种创建方式，列表补类型/有效期列与测试按钮。

**Tech Stack:** FastAPI + SQLAlchemy[asyncio] + Alembic（手写 raw SQL 迁移）+ pytest-asyncio + 原生 JS（无前端测试框架）。

## Global Constraints

- Python ≥ 3.12；包管理用 `uv`（`uv run pytest`、`uv run ruff`）。
- 加密复用 `docupipe_manager/crypto.py` 的 `encrypt_sm4` / `decrypt_sm4`（SM4-ECB，key 为 16 字节 hex）。
- 迁移手写 raw SQL + 幂等（`IF NOT EXISTS`），不依赖 alembic autogenerate，沿用 `docupipe_manager/migrations/versions/` 现有编号风格。
- DB 枚举类型 `docupipe_manager.credential_type`（仅 `dws`）已在初始迁移 0001 创建并被 `tasks.credential_type` 引用；**复用** `docupipe_manager.models.task.CredentialType` 枚举类，不新建。
- 测试：API 层用 `conftest.py` 的 `async_client` + `override_get_current_user` + patch `app.state.credential`；service 层实例化 `CredentialService`（MagicMock engine/settings/platform_client）+ patch `_session_factory`。真实 dws CLI 调用走 `@pytest.mark.integration`（默认跳过）。
- 每个 task 的 commit 前必须通过：`uv run ruff check docupipe_manager tests` 和 `uv run pytest tests/<相关文件> -v`。
- 提交信息沿用项目中文风格（`feat:` / `fix:` / `refactor:` / `test:` 前缀）。

---

## File Structure

| 文件 | 责任 | 本计划动作 |
|---|---|---|
| `docupipe_manager/models/dws_credential.py` | DwsCredential ORM | 新增 `credential_type` 列（复用 task.CredentialType） |
| `docupipe_manager/migrations/versions/0004_add_credential_type.py` | 迁移 | 新建：加列 |
| `docupipe_manager/services/credential_service.py` | 凭证生命周期 | 提取 `_probe_auth_blob` + `_parse_dt`；新增 `create_from_import`；增强 `check_status`（回写）；修复 `finalize_login` |
| `docupipe_manager/api/credentials.py` | 凭证 API | 新增 `/import`、`POST /{id}/test`；移除旧 `GET /{id}/status`；增强 list |
| `docupipe_manager/static/js/project_detail.js` | 凭证 Tab UI | 重写 `loadCredentials`（统一对话框 + 新列 + 测试按钮） |
| `tests/unit/test_models.py` | 模型映射测试 | 追加 credential_type 列断言 |
| `tests/services/test_credential_service.py` | service 测试 | 追加 `_parse_dt`/`_probe_auth_blob`/`create_from_import`/`check_status`/`finalize` 测试 |
| `tests/api/test_credentials.py` | API 测试 | 新增 import/test 端点测试；重写旧 status 测试；增强 list 测试 |

---

## Task 1: 数据模型 + 迁移（credential_type 列）

**Files:**
- Modify: `docupipe_manager/models/dws_credential.py`
- Create: `docupipe_manager/migrations/versions/0004_add_credential_type.py`
- Test: `tests/unit/test_models.py`

**Interfaces:**
- Produces: `DwsCredential.credential_type` 列（`CredentialType`，default `dws`）；DB 列 `dws_credentials.credential_type`。

- [ ] **Step 1: 写失败测试**

追加到 `tests/unit/test_models.py` 末尾：

```python
def test_dws_credential_has_credential_type():
    cols = DwsCredential.__table__.columns
    assert "credential_type" in cols
    assert cols["credential_type"].default is not None
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/unit/test_models.py::test_dws_credential_has_credential_type -v`
Expected: FAIL — `KeyError: 'credential_type'`（列尚不存在）

- [ ] **Step 3: 修改模型**

在 `docupipe_manager/models/dws_credential.py` 顶部 import 区加：

```python
from docupipe_manager.models.task import CredentialType
```

在 `DwsCredential` 类内（`status` 列之后、`created_by` 之前）加：

```python
    credential_type: Mapped[CredentialType] = mapped_column(
        Enum(CredentialType, name="credential_type", schema=_SCHEMA, create_constraint=True),
        default=CredentialType.dws,
        nullable=False,
    )
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `uv run pytest tests/unit/test_models.py -v`
Expected: PASS（全部，含新测试）

- [ ] **Step 5: 新建迁移 `0004_add_credential_type.py`**

```python
"""Add credential_type column to dws_credentials.

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-24
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE docupipe_manager.dws_credentials "
        "ADD COLUMN IF NOT EXISTS credential_type docupipe_manager.credential_type "
        "NOT NULL DEFAULT 'dws'"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE docupipe_manager.dws_credentials "
        "DROP COLUMN IF EXISTS credential_type"
    )
```

- [ ] **Step 6: lint + 提交**

Run: `uv run ruff check docupipe_manager tests`
```bash
git add docupipe_manager/models/dws_credential.py \
  docupipe_manager/migrations/versions/0004_add_credential_type.py \
  tests/unit/test_models.py
git commit -m "feat: DwsCredential 新增 credential_type 列与迁移"
```

---

## Task 2: 提取 `_parse_dt` + `_probe_auth_blob`，修复 `finalize_login` 过期时间

**Files:**
- Modify: `docupipe_manager/services/credential_service.py`
- Test: `tests/services/test_credential_service.py`

**Interfaces:**
- Produces:
  - `_parse_dt(s: str | None) -> datetime | None`（模块级函数）
  - `CredentialService._probe_auth_blob(self, auth_b64: str) -> dict`（import 失败抛 `ValueError`）
  - `check_status` 改为复用 `_probe_auth_blob`（本任务保持只读返回，回写在 Task 4）
- Consumes: `decrypt_sm4`（现有）；`CredentialType`（Task 1）

- [ ] **Step 1: 写失败测试 — `_parse_dt`**

追加到 `tests/services/test_credential_service.py`（文件顶部加 `from docupipe_manager.services.credential_service import CredentialService, _parse_dt`，更新现有 import 行）：

```python
from datetime import datetime, timezone

from docupipe_manager.services.credential_service import CredentialService, _parse_dt


def test_parse_dt_valid():
    assert _parse_dt("2026-12-31T23:59:59Z") == datetime(
        2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc
    )


def test_parse_dt_none_and_invalid():
    assert _parse_dt(None) is None
    assert _parse_dt("") is None
    assert _parse_dt("not-a-date") is None
```

> 注意：现有文件第 6 行 `from docupipe_manager.services.credential_service import CredentialService` 需扩展为同时导入 `_parse_dt`。

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/services/test_credential_service.py::test_parse_dt_valid -v`
Expected: FAIL — `ImportError: cannot import name '_parse_dt'`

- [ ] **Step 3: 实现 `_parse_dt` 与 `_probe_auth_blob`，重构 `check_status`**

在 `docupipe_manager/services/credential_service.py`：

(a) 顶部 import 补充（现有 `import` 块内）：

```python
import binascii
from datetime import datetime, timezone
```

(b) 在模块级（`logger = ...` 之后、`class CredentialService` 之前）加：

```python
def _parse_dt(s: str | None) -> datetime | None:
    """宽松解析 ISO 8601 字符串（兼容 'Z' 后缀）；失败返回 None。"""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
```

(c) 在 `CredentialService` 类内（`check_status` 之前）加 `_probe_auth_blob`：

```python
    async def _probe_auth_blob(self, auth_b64: str) -> dict:
        """把 base64 auth 写入临时 HOME，import 后调 status，返回 status 元数据。
        import 失败抛 ValueError；finally 清理临时目录。"""
        try:
            binascii.a2b_base64(auth_b64.encode("utf-8"))
        except (binascii.Error, ValueError) as e:
            raise ValueError(f"auth_blob 不是合法的 base64: {e}") from e

        home_dir = mkdtemp(prefix="dws-probe-")
        try:
            import_path = os.path.join(home_dir, "auth.b64")
            with open(import_path, "w") as f:
                f.write(auth_b64)

            import_proc = await asyncio.create_subprocess_exec(
                self._settings.dws_cli_path, "auth", "import", "-i", import_path, "--base64",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "HOME": home_dir},
            )
            try:
                await asyncio.wait_for(import_proc.communicate(), timeout=30)
            except asyncio.TimeoutError:
                import_proc.kill()
                raise ValueError("dws auth import 超时")
            if import_proc.returncode != 0:
                raise ValueError("dws auth import 失败：凭证无效")

            status_proc = await asyncio.create_subprocess_exec(
                self._settings.dws_cli_path, "auth", "status", "--format", "json",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "HOME": home_dir},
            )
            try:
                stdout, _ = await asyncio.wait_for(status_proc.communicate(), timeout=30)
            except asyncio.TimeoutError:
                status_proc.kill()
                raise ValueError("dws auth status 超时")
            return json.loads(stdout.decode()) if stdout else {}
        finally:
            shutil.rmtree(home_dir, ignore_errors=True)
```

(d) 把现有 `check_status`（第 152–185 行整段）替换为复用版本（本任务保持只读）：

```python
    async def check_status(self, credential_id: uuid.UUID, project_id: uuid.UUID) -> dict:
        """读凭证并 import+status 探测（本任务仅返回，回写见 Task 4）。"""
        async with self._session_factory() as db_session:
            credential = await db_session.get(DwsCredential, credential_id)
            if credential is None or credential.project_id != project_id:
                raise ValueError("Credential not found")

        key_hex = self._settings.encryption_key
        auth_b64 = decrypt_sm4(credential.auth_blob.hex(), key_hex)
        return await self._probe_auth_blob(auth_b64)
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `uv run pytest tests/services/test_credential_service.py -v`
Expected: PASS（含新 parse_dt 测试 + 现有 revoke/list 测试）

- [ ] **Step 5: 写失败测试 — `_probe_auth_blob` import 失败**

追加到 `tests/services/test_credential_service.py`：

```python
@pytest.mark.asyncio
async def test_probe_auth_blob_invalid_base64(credential_service):
    with pytest.raises(ValueError):
        await credential_service._probe_auth_blob("@@not base64@@")


@pytest.mark.asyncio
async def test_probe_auth_blob_import_fails(credential_service):
    fake_proc = AsyncMock()
    fake_proc.returncode = 1
    fake_proc.communicate = AsyncMock(return_value=(b"", b"err"))
    with patch("docupipe_manager.services.credential_service.asyncio.create_subprocess_exec",
               AsyncMock(return_value=fake_proc)):
        with pytest.raises(ValueError):
            await credential_service._probe_auth_blob("YWJjZGVm")  # 合法 base64，但 import 失败
```

- [ ] **Step 6: 运行测试，确认通过**

Run: `uv run pytest tests/services/test_credential_service.py::test_probe_auth_blob_import_fails tests/services/test_credential_service.py::test_probe_auth_blob_invalid_base64 -v`
Expected: PASS

- [ ] **Step 7: 写失败测试 — `finalize_login` 持久化过期时间（回归 bug）**

追加到 `tests/services/test_credential_service.py`：

```python
@pytest.mark.asyncio
async def test_finalize_login_persists_expires(credential_service):
    """finalize 应把 status 返回的过期时间存入 DwsCredential（回归现有写死 None 的 bug）。"""
    pid = uuid.uuid4(); uid = uuid.uuid4()
    session_obj = {"home_dir": "/tmp/fake-home", "name": "n", "project_id": pid}

    status_proc = AsyncMock()
    status_proc.communicate = AsyncMock(
        return_value=(b'{"corp_id":"c1","token_expires_at":"2026-12-31T00:00:00Z","refresh_token_expires_at":"2027-01-01T00:00:00Z"}', b"")
    )
    export_proc = AsyncMock()
    export_proc.returncode = 0
    export_proc.communicate = AsyncMock(return_value=(b"", b""))

    async def fake_exec(*args, **kwargs):
        if "export" in args:
            return export_proc
        return status_proc

    saved = {}
    class FakeCred:
        def __init__(self, **kw):
            self.__dict__.update(kw)
            saved.update(kw)

    with patch.object(credential_service, "_active_sessions", {**credential_service._active_sessions, "sk": session_obj}):
        with patch("docupipe_manager.services.credential_service.asyncio.create_subprocess_exec", side_effect=fake_exec):
            with patch("builtins.open", MagicMock()):  # 写 export 文件
                with patch("docupipe_manager.services.credential_service.DwsCredential", FakeCred):
                    with patch.object(credential_service, "_session_factory") as mock_sf:
                        ms = AsyncMock(); ms.__aenter__.return_value = ms
                        ms.add = AsyncMock(); ms.commit = AsyncMock(); ms.refresh = AsyncMock()
                        mock_sf.return_value = ms
                        await credential_service.finalize_login("sk", "n", uid, pid)

    assert saved.get("token_expires_at") is not None
    assert saved.get("refresh_token_expires_at") is not None
    assert saved.get("credential_type") is not None
```

> 说明：此测试白盒验证 `finalize_login` 构造 `DwsCredential` 时传入的 `token_expires_at` / `refresh_token_expires_at` / `credential_type` 不再为 None。

- [ ] **Step 8: 修复 `finalize_login`**

在 `docupipe_manager/services/credential_service.py` 的 `finalize_login` 内，把构造 `DwsCredential(...)` 的块（原第 126–135 行）替换为：

```python
        credential = DwsCredential(
            name=name,
            corp_id=corp_id,
            auth_blob=bytes.fromhex(auth_blob_hex),
            token_expires_at=_parse_dt(token_expires_at_str),
            refresh_token_expires_at=_parse_dt(refresh_expires_at_str),
            credential_type=CredentialType.dws,
            status=CredentialStatus.active,
            created_by=user_id,
            project_id=project_id,
        )
```

并在文件顶部 import 区确认已 `from docupipe_manager.models.task import CredentialType`（若 Task 1 未在此文件导入，补上）。

- [ ] **Step 9: 运行测试，确认通过**

Run: `uv run pytest tests/services/test_credential_service.py -v`
Expected: PASS（含 finalize 回归测试）

- [ ] **Step 10: lint + 提交**

Run: `uv run ruff check docupipe_manager tests`
```bash
git add docupipe_manager/services/credential_service.py tests/services/test_credential_service.py
git commit -m "refactor: 提取 _probe_auth_blob/_parse_dt，修复 finalize 过期时间持久化"
```

---

## Task 3: `create_from_import`（导入创建，方式 A）

**Files:**
- Modify: `docupipe_manager/services/credential_service.py`
- Test: `tests/services/test_credential_service.py`

**Interfaces:**
- Produces: `CredentialService.create_from_import(self, project_id: uuid.UUID, name: str, auth_b64: str, user_id: uuid.UUID) -> DwsCredential`（base64 非法 / import 失败抛 `ValueError`）
- Consumes: `_probe_auth_blob`（Task 2）、`encrypt_sm4`、`CredentialType`

- [ ] **Step 1: 写失败测试 — 成功路径**

追加到 `tests/services/test_credential_service.py`：

```python
@pytest.mark.asyncio
async def test_create_from_import_success(credential_service):
    pid = uuid.uuid4(); uid = uuid.uuid4()
    meta = {"corp_id": "corp-x", "token_expires_at": "2026-12-31T00:00:00Z",
            "refresh_token_expires_at": "2027-01-01T00:00:00Z"}
    captured = {}
    with patch.object(credential_service, "_probe_auth_blob", AsyncMock(return_value=meta)):
        with patch.object(credential_service, "_session_factory") as mock_sf:
            ms = AsyncMock(); ms.__aenter__.return_value = ms
            ms.add = AsyncMock(side_effect=lambda c: captured.__setitem__("cred", c))
            ms.commit = AsyncMock(); ms.refresh = AsyncMock()
            mock_sf.return_value = ms
            await credential_service.create_from_import(pid, "imp", "YWJj", uid)

    cred = captured["cred"]
    assert cred.corp_id == "corp-x"
    assert cred.token_expires_at is not None
    assert cred.refresh_token_expires_at is not None
    assert cred.credential_type.value == "dws"
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/services/test_credential_service.py::test_create_from_import_success -v`
Expected: FAIL — `AttributeError: 'CredentialService' object has no attribute 'create_from_import'`

- [ ] **Step 3: 实现 `create_from_import`**

在 `CredentialService` 类内（`finalize_login` 之后）加：

```python
    async def create_from_import(
        self, project_id: uuid.UUID, name: str, auth_b64: str, user_id: uuid.UUID
    ) -> DwsCredential:
        """方式 A：用户粘贴/上传 dws auth export 的 base64，import+status 验证后加密存储。"""
        meta = await self._probe_auth_blob(auth_b64)  # base64 / import 失败抛 ValueError

        key_hex = self._settings.encryption_key
        auth_blob_hex = encrypt_sm4(auth_b64, key_hex)

        credential = DwsCredential(
            name=name,
            corp_id=meta.get("corp_id", ""),
            auth_blob=bytes.fromhex(auth_blob_hex),
            token_expires_at=_parse_dt(meta.get("token_expires_at")),
            refresh_token_expires_at=_parse_dt(meta.get("refresh_token_expires_at")),
            credential_type=CredentialType.dws,
            status=CredentialStatus.active,
            created_by=user_id,
            project_id=project_id,
        )

        async with self._session_factory() as db_session:
            db_session.add(credential)
            await db_session.commit()
            await db_session.refresh(credential)

        asyncio.create_task(self._platform_client.push_audit({
            "event": "docupipe.credential.create",
            "credential_id": str(credential.id),
            "name": name,
            "source": "import",
        }))
        return credential
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `uv run pytest tests/services/test_credential_service.py::test_create_from_import_success -v`
Expected: PASS

- [ ] **Step 5: 写测试 — 无效 blob 不入库**

追加：

```python
@pytest.mark.asyncio
async def test_create_from_import_invalid_blob(credential_service):
    pid = uuid.uuid4(); uid = uuid.uuid4()
    added = []
    with patch.object(credential_service, "_probe_auth_blob",
                      AsyncMock(side_effect=ValueError("invalid"))):
        with patch.object(credential_service, "_session_factory") as mock_sf:
            ms = AsyncMock(); ms.__aenter__.return_value = ms
            ms.add = AsyncMock(side_effect=lambda c: added.append(c))
            mock_sf.return_value = ms
            with pytest.raises(ValueError):
                await credential_service.create_from_import(pid, "imp", "bad", uid)
    assert added == []  # 未入库
```

- [ ] **Step 6: 运行测试，确认通过**

Run: `uv run pytest tests/services/test_credential_service.py -v`
Expected: PASS

- [ ] **Step 7: lint + 提交**

Run: `uv run ruff check docupipe_manager tests`
```bash
git add docupipe_manager/services/credential_service.py tests/services/test_credential_service.py
git commit -m "feat: 新增 create_from_import 导入创建凭证"
```

---

## Task 4: 增强 `check_status`（测试并回写）

**Files:**
- Modify: `docupipe_manager/services/credential_service.py`
- Test: `tests/services/test_credential_service.py`

**Interfaces:**
- Produces: `check_status` 现在回写 DB（corp_id/expires/status）并返回 `{status, corp_id, token_expires_at, refresh_token_expires_at, error}`；import 失败回写 `expired` 且返回带 `error` 的 dict（不抛）；仅 credential 不存在抛 `ValueError`。
- Consumes: `_probe_auth_blob`、`_parse_dt`（Task 2）

- [ ] **Step 1: 写失败测试 — 成功回写 active**

追加到 `tests/services/test_credential_service.py`：

```python
@pytest.mark.asyncio
async def test_check_status_writes_back_active(credential_service):
    pid = uuid.uuid4(); cid = uuid.uuid4()
    cred = MagicMock()
    cred.id = cid; cred.project_id = pid; cred.corp_id = "old"
    cred.auth_blob = b"\x00"
    meta = {"corp_id": "new-corp", "token_expires_at": "2099-12-31T00:00:00Z",
            "refresh_token_expires_at": "2099-12-31T00:00:00Z"}
    with patch.object(credential_service, "_session_factory") as mock_sf:
        ms = AsyncMock(); ms.__aenter__.return_value = ms
        ms.get = AsyncMock(return_value=cred)
        ms.commit = AsyncMock()
        mock_sf.return_value = ms
        with patch("docupipe_manager.services.credential_service.decrypt_sm4", return_value="b64"):
            with patch.object(credential_service, "_probe_auth_blob", AsyncMock(return_value=meta)):
                result = await credential_service.check_status(cid, pid)
    assert result["status"] == "active"
    assert result["corp_id"] == "new-corp"
    assert result["error"] is None
    assert cred.corp_id == "new-corp"
    assert cred.status.value == "active"
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/services/test_credential_service.py::test_check_status_writes_back_active -v`
Expected: FAIL（当前 check_status 只返回 _probe 原始 dict，无 status/corp_id/error 结构、不回写）

- [ ] **Step 3: 实现 check_status 回写**

把 Task 2 里复用版的 `check_status` 替换为：

```python
    async def check_status(self, credential_id: uuid.UUID, project_id: uuid.UUID) -> dict:
        """测试凭证可用性并回写最新 corp_id/过期时间/status。"""
        async with self._session_factory() as db_session:
            credential = await db_session.get(DwsCredential, credential_id)
            if credential is None or credential.project_id != project_id:
                raise ValueError("Credential not found")
            key_hex = self._settings.encryption_key
            auth_b64 = decrypt_sm4(credential.auth_blob.hex(), key_hex)

        try:
            meta = await self._probe_auth_blob(auth_b64)
        except ValueError as e:
            async with self._session_factory() as db_session:
                credential = await db_session.get(DwsCredential, credential_id)
                credential.status = CredentialStatus.expired
                await db_session.commit()
            return {"status": "expired", "corp_id": credential.corp_id if credential else "",
                    "token_expires_at": None, "refresh_token_expires_at": None, "error": str(e)}

        corp_id = meta.get("corp_id") or ""
        token_exp = _parse_dt(meta.get("token_expires_at"))
        refresh_exp = _parse_dt(meta.get("refresh_token_expires_at"))
        now = datetime.now(timezone.utc)
        new_status = (CredentialStatus.expired
                      if (refresh_exp is not None and refresh_exp < now)
                      else CredentialStatus.active)

        async with self._session_factory() as db_session:
            credential = await db_session.get(DwsCredential, credential_id)
            credential.corp_id = corp_id
            if token_exp is not None:
                credential.token_expires_at = token_exp
            if refresh_exp is not None:
                credential.refresh_token_expires_at = refresh_exp
            credential.status = new_status
            await db_session.commit()

        return {"status": new_status.value, "corp_id": corp_id,
                "token_expires_at": str(token_exp) if token_exp else None,
                "refresh_token_expires_at": str(refresh_exp) if refresh_exp else None,
                "error": None}
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `uv run pytest tests/services/test_credential_service.py::test_check_status_writes_back_active -v`
Expected: PASS

- [ ] **Step 5: 写测试 — refresh 过期 / import 失败 / 不存在**

追加：

```python
@pytest.mark.asyncio
async def test_check_status_refresh_expired(credential_service):
    pid = uuid.uuid4(); cid = uuid.uuid4()
    cred = MagicMock(); cred.id = cid; cred.project_id = pid; cred.auth_blob = b"\x00"
    meta = {"corp_id": "c", "refresh_token_expires_at": "2000-01-01T00:00:00Z"}
    with patch.object(credential_service, "_session_factory") as mock_sf:
        ms = AsyncMock(); ms.__aenter__.return_value = ms
        ms.get = AsyncMock(return_value=cred); ms.commit = AsyncMock()
        mock_sf.return_value = ms
        with patch("docupipe_manager.services.credential_service.decrypt_sm4", return_value="b64"):
            with patch.object(credential_service, "_probe_auth_blob", AsyncMock(return_value=meta)):
                result = await credential_service.check_status(cid, pid)
    assert result["status"] == "expired"


@pytest.mark.asyncio
async def test_check_status_import_error_marks_expired(credential_service):
    pid = uuid.uuid4(); cid = uuid.uuid4()
    cred = MagicMock(); cred.id = cid; cred.project_id = pid; cred.auth_blob = b"\x00"
    cred.corp_id = "c"
    with patch.object(credential_service, "_session_factory") as mock_sf:
        ms = AsyncMock(); ms.__aenter__.return_value = ms
        ms.get = AsyncMock(return_value=cred); ms.commit = AsyncMock()
        mock_sf.return_value = ms
        with patch("docupipe_manager.services.credential_service.decrypt_sm4", return_value="b64"):
            with patch.object(credential_service, "_probe_auth_blob",
                              AsyncMock(side_effect=ValueError("import failed"))):
                result = await credential_service.check_status(cid, pid)
    assert result["status"] == "expired"
    assert result["error"] == "import failed"


@pytest.mark.asyncio
async def test_check_status_not_found(credential_service):
    pid = uuid.uuid4(); cid = uuid.uuid4()
    with patch.object(credential_service, "_session_factory") as mock_sf:
        ms = AsyncMock(); ms.__aenter__.return_value = ms
        ms.get = AsyncMock(return_value=None)
        mock_sf.return_value = ms
        with pytest.raises(ValueError):
            await credential_service.check_status(cid, pid)
```

- [ ] **Step 6: 运行测试，确认通过**

Run: `uv run pytest tests/services/test_credential_service.py -v`
Expected: PASS（全部）

- [ ] **Step 7: lint + 提交**

Run: `uv run ruff check docupipe_manager tests`
```bash
git add docupipe_manager/services/credential_service.py tests/services/test_credential_service.py
git commit -m "feat: check_status 增强为测试并回写状态/过期时间"
```

---

## Task 5: API — `/import`、`POST /{id}/test`、list 增强

**Files:**
- Modify: `docupipe_manager/api/credentials.py`
- Test: `tests/api/test_credentials.py`

**Interfaces:**
- Produces:
  - `POST /api/projects/{project_id}/credentials/import`（body `{name, auth_blob}`，失败 400）
  - `POST /api/projects/{project_id}/credentials/{credential_id}/test`（返回 service dict，404 on 不存在）
  - `GET .../credentials` list 响应补 `credential_type`、`refresh_token_expires_at`
  - 移除旧 `GET .../credentials/{credential_id}/status`
- Consumes: `create_from_import`（Task 3）、`check_status`（Task 4）

- [ ] **Step 1: 更新 list 测试（补字段断言）**

修改 `tests/api/test_credentials.py` 的 `test_list_credentials`，把 `cred` 的 MagicMock 补上 `credential_type` 与 `refresh_token_expires_at`，并断言响应字段：

```python
@pytest.mark.asyncio
async def test_list_credentials(async_client):
    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    pid = uuid.uuid4()
    cred = MagicMock()
    cred.id = uuid.uuid4(); cred.name = "c1"; cred.corp_id = "x"
    cred.status = MagicMock(value="active")
    cred.credential_type = MagicMock(value="dws")
    cred.token_expires_at = None; cred.refresh_token_expires_at = None
    cred.created_at = "2026-01-01"
    with patch("docupipe_manager.api.credentials._require_access_async", new=AsyncMock(return_value={"role": "admin"})):
        with patch("docupipe_manager.main.app") as mock_app:
            mock_app.state.credential.list_credentials = AsyncMock(return_value=[cred])
            r = await async_client.get(f"/api/projects/{pid}/credentials")
            assert r.status_code == 200
            data = r.json()
            assert len(data) == 1
            assert data[0]["credential_type"] == "dws"
            assert data[0]["refresh_token_expires_at"] is None
    clear_overrides()
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `uv run pytest tests/api/test_credentials.py::test_list_credentials -v`
Expected: FAIL（响应暂无 `credential_type` 键）

- [ ] **Step 3: 增强 list 端点**

在 `docupipe_manager/api/credentials.py` 的 `list_credentials` 返回字典补两键：

```python
@router.get("")
async def list_credentials(project_id: uuid.UUID, user: dict = Depends(_require_access_async)):
    from docupipe_manager.main import app
    creds = await app.state.credential.list_credentials(project_id)
    return [
        {"id": str(c.id), "name": c.name, "corp_id": c.corp_id,
         "credential_type": c.credential_type.value,
         "status": c.status.value,
         "token_expires_at": str(c.token_expires_at) if c.token_expires_at else None,
         "refresh_token_expires_at": str(c.refresh_token_expires_at) if c.refresh_token_expires_at else None,
         "created_at": str(c.created_at)}
        for c in creds
    ]
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `uv run pytest tests/api/test_credentials.py::test_list_credentials -v`
Expected: PASS

- [ ] **Step 5: 写 `/import` 端点测试（成功 + 无效）**

追加到 `tests/api/test_credentials.py`：

```python
@pytest.mark.asyncio
async def test_import_credential(async_client):
    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    pid = uuid.uuid4()
    mock_cred = MagicMock(); mock_cred.id = uuid.uuid4()
    with patch("docupipe_manager.api.credentials._require_access_async", new=AsyncMock(return_value={"role": "admin"})):
        with patch("docupipe_manager.main.app") as mock_app:
            mock_app.state.credential.create_from_import = AsyncMock(return_value=mock_cred)
            r = await async_client.post(
                f"/api/projects/{pid}/credentials/import",
                json={"name": "imp", "auth_blob": "YWJj"},
            )
            assert r.status_code == 200
            assert r.json()["status"] == "active"
    clear_overrides()


@pytest.mark.asyncio
async def test_import_credential_invalid(async_client):
    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    pid = uuid.uuid4()
    with patch("docupipe_manager.api.credentials._require_access_async", new=AsyncMock(return_value={"role": "admin"})):
        with patch("docupipe_manager.main.app") as mock_app:
            mock_app.state.credential.create_from_import = AsyncMock(side_effect=ValueError("bad blob"))
            r = await async_client.post(
                f"/api/projects/{pid}/credentials/import",
                json={"name": "imp", "auth_blob": "x"},
            )
            assert r.status_code == 400
    clear_overrides()
```

- [ ] **Step 6: 写 `/test` 端点测试（成功 + 404）**

追加（替换旧 `test_check_status` / `test_check_status_404`，见 Step 8）：

```python
@pytest.mark.asyncio
async def test_test_endpoint(async_client):
    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    pid = uuid.uuid4(); cid = uuid.uuid4()
    with patch("docupipe_manager.api.credentials._require_access_async", new=AsyncMock(return_value={"role": "admin"})):
        with patch("docupipe_manager.main.app") as mock_app:
            mock_app.state.credential.check_status = AsyncMock(
                return_value={"status": "active", "corp_id": "c", "error": None}
            )
            r = await async_client.post(f"/api/projects/{pid}/credentials/{cid}/test")
            assert r.status_code == 200
            assert r.json()["status"] == "active"
    clear_overrides()


@pytest.mark.asyncio
async def test_test_endpoint_404(async_client):
    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    pid = uuid.uuid4(); cid = uuid.uuid4()
    with patch("docupipe_manager.api.credentials._require_access_async", new=AsyncMock(return_value={"role": "admin"})):
        with patch("docupipe_manager.main.app") as mock_app:
            mock_app.state.credential.check_status = AsyncMock(side_effect=ValueError("not found"))
            r = await async_client.post(f"/api/projects/{pid}/credentials/{cid}/test")
            assert r.status_code == 404
    clear_overrides()
```

- [ ] **Step 7: 运行新测试，确认失败**

Run: `uv run pytest tests/api/test_credentials.py::test_import_credential tests/api/test_credentials.py::test_test_endpoint -v`
Expected: FAIL（端点尚不存在）

- [ ] **Step 8: 改 API — 加 ImportRequest、/import、/test；移除旧 /status**

在 `docupipe_manager/api/credentials.py`：

(a) 在 `FinalizeRequest` 之后加：

```python
class ImportRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    auth_blob: str = Field(..., min_length=1)
```

(b) 在 `list_credentials` 之后、`device-login/start` 之前加导入端点：

```python
@router.post("/import")
async def import_credential(project_id: uuid.UUID, body: ImportRequest,
                            user: dict = Depends(_require_access_async)):
    from docupipe_manager.main import app
    try:
        cred = await app.state.credential.create_from_import(
            project_id, body.name, body.auth_blob, uuid.UUID(user["id"])
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"id": str(cred.id), "status": "active"}
```

(c) 把旧 `GET /{credential_id}/status`（`check_status` 端点，原第 54–61 行）**整段删除**，替换为：

```python
@router.post("/{credential_id}/test")
async def test_credential(project_id: uuid.UUID, credential_id: uuid.UUID,
                          user: dict = Depends(_require_access_async)):
    from docupipe_manager.main import app
    try:
        return await app.state.credential.check_status(credential_id, project_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
```

(d) 删除 `tests/api/test_credentials.py` 中旧的 `test_check_status` 与 `test_check_status_404`（已被 Step 6 的 `test_test_endpoint*` 取代）。

- [ ] **Step 9: 运行全部 API 测试，确认通过**

Run: `uv run pytest tests/api/test_credentials.py -v`
Expected: PASS（含 import / test / list / device-login / revoke 全部）

- [ ] **Step 10: lint + 提交**

Run: `uv run ruff check docupipe_manager tests`
```bash
git add docupipe_manager/api/credentials.py tests/api/test_credentials.py
git commit -m "feat: 凭证 API 新增 /import 与 /test，增强 list，移除旧 status 端点"
```

---

## Task 6: 前端 — 统一添加对话框 + 列表新列 + 测试按钮

**Files:**
- Modify: `docupipe_manager/static/js/project_detail.js`
- 验证：手动（项目无 JS 测试框架）

> 本项目前端为原生 JS，无自动化测试设施，本任务用「实现 + 手测清单」验证。

- [ ] **Step 1: 替换 `loadCredentials` 函数**

把 `docupipe_manager/static/js/project_detail.js` 中整个 `loadCredentials` 函数（原第 53–135 行）替换为：

```javascript
async function loadCredentials() {
  const r = await fetch(`/api/projects/${pid}/credentials`);
  const creds = await r.json();
  const box = document.getElementById("tab-credentials");

  let html = '<div style="margin-bottom:10px"><button class="btn btn-sm btn-primary" id="cred-add">添加凭证</button></div>';
  html += '<div id="cred-dialog-mount"></div>';

  if (!creds.length) {
    html += '<div class="empty-state">暂无凭证。</div>';
  } else {
    html += '<table class="data-table"><thead><tr><th>名称</th><th>类型</th><th>CorpId</th><th>状态</th><th>Access 过期</th><th>Refresh 过期</th><th>操作</th></tr></thead><tbody>';
    for (const c of creds) {
      html += `<tr>
        <td>${c.name}</td>
        <td><span class="status-tag">${(c.credential_type || "dws").toUpperCase()}</span></td>
        <td>${c.corp_id ? `<code>${c.corp_id}</code>` : "—"}</td>
        <td><span class="status-tag ${statusTagClass(c.status)}">${c.status}</span></td>
        <td>${fmtExpires(c.token_expires_at)}</td>
        <td class="text-muted">${fmtExpires(c.refresh_token_expires_at)}</td>
        <td class="action-cell">
          <button class="btn btn-sm btn-secondary test-cred" data-id="${c.id}">测试</button>
          <button class="btn btn-sm btn-danger revoke-cred" data-id="${c.id}">吊销</button>
        </td>
      </tr>`;
    }
    html += '</tbody></table>';
  }
  box.innerHTML = html;

  document.getElementById("cred-add").addEventListener("click", () => showCredentialDialog());
  box.querySelectorAll(".revoke-cred").forEach(b => b.addEventListener("click", async () => {
    if (!confirm("确认吊销此凭证？")) return;
    const rr = await fetch(`/api/projects/${pid}/credentials/${b.dataset.id}`, {method: "DELETE"});
    if (rr.ok) { loadCredentials(); } else { alert("吊销失败"); }
  }));
  box.querySelectorAll(".test-cred").forEach(b => b.addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    const old = btn.textContent; btn.textContent = "测试中..."; btn.disabled = true;
    const tr = await fetch(`/api/projects/${pid}/credentials/${b.dataset.id}/test`, {method: "POST"});
    const data = await tr.json();
    if (!tr.ok) { alert("测试失败"); btn.textContent = old; btn.disabled = false; return; }
    if (data.error) { alert("测试失败：" + data.error); }
    loadCredentials();  // 回写后刷新整表
  }));
}

function fmtExpires(s) {
  if (!s) return "—";
  const dt = new Date(s);
  if (isNaN(dt)) return s;
  const now = new Date();
  const diff = dt - now;
  const abs = Math.abs(diff);
  const days = Math.floor(abs / 86400000);
  const hours = Math.floor((abs % 86400000) / 3600000);
  const rel = diff >= 0 ? `还剩 ${days}天${hours}h` : `已过期 ${days}天${hours}h`;
  const cls = diff < 0 ? "is-failed" : (abs < 86400000 ? "is-running" : "");
  return `<span class="status-tag ${cls}">${dt.toLocaleString()} · ${rel}</span>`;
}

function showCredentialDialog() {
  let dialog = document.getElementById("cred-dialog");
  if (!dialog) {
    dialog = document.createElement("dialog");
    dialog.id = "cred-dialog";
    document.body.appendChild(dialog);
  }
  dialog.innerHTML = `
    <h3 style="margin:0 0 16px">添加凭证</h3>
    <div class="form-group"><label>凭证类型</label>
      <select id="cred-type" class="form-control"><option value="dws">DWS（钉钉）</option></select></div>
    <div class="form-group"><label>创建方式</label>
      <div class="check-row">
        <label><input type="radio" name="cred-mode" value="import" checked> 导入已有凭证</label>
        <label><input type="radio" name="cred-mode" value="device"> 设备码登录</label>
      </div></div>
    <div class="form-group"><label>凭证名称</label>
      <input id="cred-name" class="form-control" placeholder="凭证名称"></div>
    <div id="cred-import-area">
      <div class="form-group"><label>粘贴 base64（dws auth export --base64 输出）</label>
        <textarea id="cred-blob" class="form-control" rows="5" placeholder="粘贴 base64 文本"></textarea></div>
      <div class="form-group"><label>或上传文件</label>
        <input type="file" id="cred-file" class="form-control"></div>
    </div>
    <div id="cred-device-area" class="hidden"></div>
    <div class="form-actions" style="margin-top:16px">
      <button class="btn btn-sm btn-primary" id="cred-save">保存</button>
      <button class="btn btn-sm btn-secondary" id="cred-cancel">取消</button>
    </div>`;
  dialog.showModal();

  const importArea = dialog.querySelector("#cred-import-area");
  const deviceArea = dialog.querySelector("#cred-device-area");
  const saveBtn = dialog.querySelector("#cred-save");

  dialog.querySelectorAll('input[name="cred-mode"]').forEach(r => r.addEventListener("change", () => {
    const mode = dialog.querySelector('input[name="cred-mode"]:checked').value;
    importArea.classList.toggle("hidden", mode !== "import");
    deviceArea.classList.toggle("hidden", mode !== "device");
    saveBtn.style.display = mode === "import" ? "" : "none";
    if (mode === "device") startDeviceFlow(deviceArea, dialog);
  }));

  dialog.querySelector("#cred-file").addEventListener("change", (e) => {
    const f = e.target.files[0];
    if (!f) return;
    const reader = new FileReader();
    reader.onload = () => { dialog.querySelector("#cred-blob").value = reader.result; };
    reader.readAsText(f);
  });

  dialog.querySelector("#cred-cancel").addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", (e) => { if (e.target === dialog) dialog.close(); });

  saveBtn.addEventListener("click", async () => {
    const name = dialog.querySelector("#cred-name").value.trim();
    const auth_blob = dialog.querySelector("#cred-blob").value.trim();
    if (!name) { alert("请输入凭证名称"); return; }
    if (!auth_blob) { alert("请粘贴或上传凭证内容"); return; }
    saveBtn.disabled = true;
    const rr = await fetch(`/api/projects/${pid}/credentials/import`, {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({name, auth_blob}),
    });
    if (rr.ok) { dialog.close(); loadCredentials(); }
    else { const j = await rr.json(); alert(j.detail || "导入失败"); saveBtn.disabled = false; }
  });
}

function startDeviceFlow(area, dialog) {
  let sessionKey = null;
  area.innerHTML = '<p class="card-row-meta">启动设备登录...</p>';
  fetch(`/api/projects/${pid}/credentials/device-login/start?name=${encodeURIComponent(dialog.querySelector("#cred-name").value || "dws-cred")}`)
    .then(r => r.ok ? r.json() : Promise.reject())
    .then(data => {
      sessionKey = data.session_key;
      area.innerHTML = `
        <p>请在浏览器打开：<a href="${data.verification_url}" target="_blank">${data.verification_url}</a></p>
        <p>验证码：<span class="device-code">${data.user_code}</span></p>
        <p class="device-hint">有效期 ${data.expires_in || 300} 秒</p>
        <div class="form-actions">
          <button class="btn btn-sm btn-primary" id="df-poll">已完成，验证</button>
        </div>`;
      area.querySelector("#df-poll").addEventListener("click", async () => {
        area.innerHTML = '<p class="card-row-meta">验证中...</p>';
        const pr = await fetch(`/api/projects/${pid}/credentials/device-login/poll?session_key=${sessionKey}`);
        if (!pr.ok) { area.innerHTML = '<p class="status-tag is-failed">验证失败或已过期</p>'; return; }
        const pd = await pr.json();
        if (pd.status === "success" || pd.status === "authorized") {
          const fr = await fetch(`/api/projects/${pid}/credentials/device-login/finalize`, {
            method: "POST", headers: {"Content-Type": "application/json"},
            body: JSON.stringify({session_key: sessionKey, name: dialog.querySelector("#cred-name").value || "dws-cred"}),
          });
          if (fr.ok) { dialog.close(); loadCredentials(); }
          else { area.innerHTML = '<p class="status-tag is-failed">最终验证失败</p>'; }
        } else {
          area.innerHTML = '<p class="status-tag is-running">尚未授权，请在钉钉扫码确认</p>';
        }
      });
    })
    .catch(() => { area.innerHTML = '<p class="status-tag is-failed">启动设备登录失败</p>'; });
}
```

- [ ] **Step 2: 手测清单（启动应用验证）**

Run: `uv run uvicorn docupipe_manager.main:app --reload`
打开项目详情页「凭证」Tab，逐项验证：

- [ ] 列表新增「类型」「Refresh 过期」列；时间显示「绝对值 · 还剩/已过期 Xd Yh」，临期(<24h)黄色、过期红色。
- [ ] 点「添加凭证」→ 弹出统一对话框，含类型选择（仅 DWS）、创建方式 radio、名称输入。
- [ ] 选「导入」：粘贴框 + 文件上传；上传文件后粘贴框自动填充文本；提交后凭证出现在列表。
- [ ] 粘贴非法内容 → 弹出后端返回的 400 detail。
- [ ] 选「设备码」：导入区隐藏、设备码区出现「启动设备登录」流程；切换回「导入」恢复。
- [ ] 列表「测试」按钮：点击后刷新整表，状态/过期时间更新；损坏凭证弹出「测试失败：...」。
- [ ] 「吊销」按钮仍正常。

- [ ] **Step 3: lint + 提交**

Run: `uv run ruff check docupipe_manager tests`
```bash
git add docupipe_manager/static/js/project_detail.js
git commit -m "feat(ui): 凭证统一添加对话框、类型/有效期列、可用性测试按钮"
```

---

## Self-Review

**1. Spec 覆盖：**
- 两种创建方式（导入 / 设备码）→ Task 3（create_from_import）+ Task 6 对话框承载两种 + 设备码搬迁。✓
- 凭证类型选择 → Task 1（credential_type 列）+ Task 6 对话框 select。✓
- 有效期展示 → Task 1/2（列 + finalize 持久化）+ Task 6 表格两列 + fmtExpires。✓
- 可用性测试 → Task 4（check_status 回写）+ Task 5（/test 端点）+ Task 6（测试按钮）。✓
- 迁移 → Task 1。✓
- finalize bug 修复 → Task 2 Step 7–9（回归测试）。✓
- 移除旧 GET /status → Task 5 Step 8(c)。✓

**2. Placeholder 扫描：** 无 TBD/TODO；每个代码步骤含完整代码；前端手测清单是明确可执行项（非占位）。

**3. 类型/命名一致性：**
- `CredentialType` 全程复用 `task.CredentialType`，未新建（Task 1/2/3 一致）。
- `_probe_auth_blob` / `_parse_dt` / `create_from_import` / `check_status` 在定义任务（Task 2/3/4）与消费任务（Task 3/4/5）签名一致。
- API 路径 `POST /import`、`POST /{id}/test` 在 Task 5 与 Task 6 fetch 调用一致。
- `/test` 响应 `{status, corp_id, token_expires_at, refresh_token_expires_at, error}` 在 Task 4 返回与 Task 5 透传、Task 6 读取 `data.error` 一致。

无遗留问题。
