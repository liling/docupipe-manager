import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest

from docupipe_manager.services.credential_service import CredentialError, CredentialService, _parse_dt


@pytest.fixture
def credential_service():
    engine = MagicMock()
    settings = MagicMock()
    settings.dws_cli_path = "dws"
    settings.encryption_key = "0123456789abcdef0123456789abcdef"
    platform_client = MagicMock()
    platform_client.push_audit = AsyncMock()
    return CredentialService(engine, settings, platform_client)


@pytest.mark.asyncio
async def test_revoke_filters_by_project(credential_service):
    pid = uuid.uuid4()
    other_pid = uuid.uuid4()
    cred = MagicMock()
    cred.project_id = other_pid
    with patch.object(credential_service, "_session_factory") as mock_sf:
        mock_session = AsyncMock()
        mock_sf.return_value.__aenter__.return_value = mock_session
        mock_session.get = AsyncMock(return_value=cred)
        with pytest.raises(ValueError):
            await credential_service.revoke(uuid.uuid4(), uuid.uuid4(), pid)


@pytest.mark.asyncio
async def test_list_credentials_filters_by_project(credential_service):
    pid = uuid.uuid4()
    with patch.object(credential_service, "_session_factory") as mock_sf:
        mock_session = AsyncMock()
        mock_sf.return_value.__aenter__.return_value = mock_session
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)
        result = await credential_service.list_credentials(pid)
        assert result == []


def test_parse_dt_valid():
    assert _parse_dt("2026-12-31T23:59:59Z") == datetime(
        2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc
    )


def test_parse_dt_none_and_invalid():
    assert _parse_dt(None) is None
    assert _parse_dt("") is None
    assert _parse_dt("not-a-date") is None


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


@pytest.mark.asyncio
async def test_create_from_import_success(credential_service):
    pid = uuid.uuid4(); uid = uuid.uuid4()
    meta = {"corp_id": "corp-x", "expires_at": "2026-12-31T00:00:00Z",
            "refresh_expires_at": "2027-01-01T00:00:00Z"}
    captured = {}
    with patch.object(credential_service, "_probe_auth_blob", AsyncMock(return_value=meta)):
        with patch.object(credential_service, "_session_factory") as mock_sf:
            ms = AsyncMock(); ms.__aenter__.return_value = ms
            ms.add = MagicMock(side_effect=lambda c: captured.__setitem__("cred", c))
            ms.commit = AsyncMock(); ms.refresh = AsyncMock()
            mock_sf.return_value = ms
            await credential_service.create_from_import(pid, "imp", "YWJj", uid)

    cred = captured["cred"]
    assert cred.corp_id == "corp-x"
    assert cred.token_expires_at is not None
    assert cred.refresh_token_expires_at is not None
    assert cred.credential_type.value == "dws"


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


@pytest.mark.asyncio
async def test_finalize_login_persists_expires(credential_service):
    """finalize 应把 status 返回的过期时间存入 DwsCredential（回归现有写死 None 的 bug）。"""
    pid = uuid.uuid4(); uid = uuid.uuid4()
    session_obj = {"root": "/tmp/fake-home",
                   "env": {"HOME": "/tmp/fake-home", "DWS_DISABLE_KEYCHAIN": "1",
                           "DWS_CONFIG_DIR": "/tmp/fake-home/dws-config",
                           "DWS_CACHE_DIR": "/tmp/fake-home/dws-cache", "PATH": "/usr/bin"},
                   "name": "n", "project_id": pid}

    status_proc = AsyncMock()
    status_proc.communicate = AsyncMock(
        return_value=(b'{"corp_id":"c1","expires_at":"2026-12-31T00:00:00Z","refresh_expires_at":"2027-01-01T00:00:00Z"}', b"")
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
        id = uuid.uuid4()
        def __init__(self, **kw):
            self.__dict__.update(kw)
            saved.update(kw)

    with patch.object(credential_service, "_active_sessions", {**credential_service._active_sessions, "sk": session_obj}):
        with patch("docupipe_manager.services.credential_service.asyncio.create_subprocess_exec", side_effect=fake_exec):
            with patch("builtins.open", mock_open(read_data="dGVzdA==")):  # 写 export 文件
                with patch("docupipe_manager.services.credential_service.os.path.exists", return_value=True):
                    with patch("docupipe_manager.services.credential_service.DwsCredential", FakeCred):
                        with patch.object(credential_service, "_session_factory") as mock_sf:
                            ms = AsyncMock(); ms.__aenter__.return_value = ms
                            ms.add = MagicMock(); ms.commit = AsyncMock(); ms.refresh = AsyncMock()
                            mock_sf.return_value = ms
                            await credential_service.finalize_login("sk", "n", uid, pid)

    assert saved.get("token_expires_at") is not None
    assert saved.get("refresh_token_expires_at") is not None
    assert saved.get("credential_type") is not None


@pytest.mark.asyncio
async def test_check_status_writes_back_active(credential_service):
    pid = uuid.uuid4(); cid = uuid.uuid4()
    cred = MagicMock()
    cred.id = cid; cred.project_id = pid; cred.corp_id = "old"
    cred.auth_blob = b"\x00"
    meta = {"corp_id": "new-corp", "expires_at": "2099-12-31T00:00:00Z",
            "refresh_expires_at": "2099-12-31T00:00:00Z"}
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


@pytest.mark.asyncio
async def test_check_status_refresh_expired(credential_service):
    pid = uuid.uuid4(); cid = uuid.uuid4()
    cred = MagicMock(); cred.id = cid; cred.project_id = pid; cred.auth_blob = b"\x00"
    meta = {"corp_id": "c", "refresh_expires_at": "2000-01-01T00:00:00Z"}
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


@pytest.mark.asyncio
async def test_rename_credential(credential_service):
    pid = uuid.uuid4(); cid = uuid.uuid4()
    cred = MagicMock(); cred.id = cid; cred.project_id = pid
    with patch.object(credential_service, "_session_factory") as mock_sf:
        ms = AsyncMock(); ms.__aenter__.return_value = ms
        ms.get = AsyncMock(return_value=cred)
        ms.commit = AsyncMock(); ms.refresh = AsyncMock()
        mock_sf.return_value = ms
        result = await credential_service.rename_credential(cid, "new-name", pid)
    assert result == cred
    assert cred.name == "new-name"


@pytest.mark.asyncio
async def test_rename_credential_not_found(credential_service):
    pid = uuid.uuid4(); cid = uuid.uuid4()
    with patch.object(credential_service, "_session_factory") as mock_sf:
        ms = AsyncMock(); ms.__aenter__.return_value = ms
        ms.get = AsyncMock(return_value=None)
        mock_sf.return_value = ms
        with pytest.raises(ValueError):
            await credential_service.rename_credential(cid, "new-name", pid)


@pytest.mark.asyncio
async def test_run_dws_command_construction(credential_service):
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(b'{"ok":true}', b""))
    proc.returncode = 0
    with patch("docupipe_manager.services.credential_service.asyncio.create_subprocess_exec",
               AsyncMock(return_value=proc)) as mock_exec:
        rc, stdout, stderr = await credential_service._run_dws(["auth", "status"])
    mock_exec.assert_called_once_with(
        "dws", "auth", "status",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    assert rc == 0
    assert json.loads(stdout) == {"ok": True}


@pytest.mark.asyncio
async def test_run_dws_nonzero_exit(credential_service):
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(b"", b"err"))
    proc.returncode = 1
    with patch("docupipe_manager.services.credential_service.asyncio.create_subprocess_exec",
               AsyncMock(return_value=proc)):
        rc, stdout, stderr = await credential_service._run_dws(["bad"])
    assert rc == 1


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

    sessions = []
    for _ in range(5):
        ms = AsyncMock(); ms.__aenter__ = AsyncMock(return_value=ms); ms.__aexit__ = AsyncMock(return_value=None)
        ms.commit = AsyncMock(); ms.execute = AsyncMock(); ms.refresh = AsyncMock()
        sessions.append(ms)
    idx = {"i": 0}
    def factory():
        s = sessions[idx["i"]]; idx["i"] += 1; return s
    sessions[0].get = AsyncMock(return_value=cred)
    sessions[1].add = MagicMock()
    sessions[2].get = AsyncMock(return_value=cred)
    sessions[3].get = AsyncMock(return_value=cred)
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


@pytest.mark.asyncio
async def test_refresh_credential_skips_none(credential_service):
    cid = uuid.uuid4()
    with patch.object(credential_service, "_session_factory") as mock_sf:
        ms = AsyncMock(); ms.__aenter__.return_value = ms
        ms.get = AsyncMock(return_value=None)
        mock_sf.return_value = ms
        await credential_service.refresh_credential(cid)


@pytest.mark.asyncio
async def test_refresh_credential_api_failure_marks_job_failed(credential_service, tmp_path):
    from docupipe_manager.models.dws_credential import CredentialStatus
    from docupipe_manager.models.job import JobKind
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
    sessions[2].execute = AsyncMock()
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

    assert cred.status == CredentialStatus.active
    assert any(getattr(a, "kind", None) == JobKind.credential_keepalive for a in added)


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
    assert import_call["env"]["DWS_DISABLE_KEYCHAIN"] == "1"
    assert "DWS_CONFIG_DIR" in import_call["env"]


def test_credential_service_has_no_dws_lock(credential_service):
    assert not hasattr(credential_service, "_dws_lock")


@pytest.mark.asyncio
async def test_start_device_login_uses_isolated_env(credential_service):
    pid = uuid.uuid4()
    proc = AsyncMock()
    proc.stderr.readline = AsyncMock(side_effect=[
        b"    authorization code: ABC-123\n",
        b"    https://login.dingtalk.com/oauth2/device/verify.htm?user_code=ABC-123\n",
        b"",  # 供 drain task 收尾
    ])
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
    await sess["stderr_task"]
    credential_service._cleanup_session(result["session_key"])


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


# 录制的真实 dws device flow stderr 样本（含验证码 UI）
_DEVICE_STDERR_WITH_CODE = """● Step 1: Requesting device authorization code...

  ╭────────────────────────────────────────────────────────────────────────────────────╮
  │  Please open the following link in your browser and enter the authorization code:  │
  │                                                                                    │
  │    link: https://login.dingtalk.com/oauth2/device/verify.htm                       │
  │    authorization code: SKFG-WWXP                                                   │
  │                                                                                    │
  │  Or open the following link:                                                       │
  │    https://login.dingtalk.com/oauth2/device/verify.htm?user_code=SKFG-WWXP         │
  │                                                                                    │
  │  Authorization code will expire in 900 seconds.                                    │
  ╰────────────────────────────────────────────────────────────────────────────────────╯

● Step 2: Waiting for user authorization...
  (polling every 5 seconds)
"""

_DEVICE_STDERR_FAILED = _DEVICE_STDERR_WITH_CODE + """{
  "error": {
    "category": "auth",
    "code": 2,
    "message": "device authorization failed: context canceled"
  }
}
"""


def test_parse_device_code_extracts_code_url_and_ttl():
    from docupipe_manager.services.credential_service import _parse_device_code_from_stderr
    result = _parse_device_code_from_stderr(_DEVICE_STDERR_WITH_CODE)
    assert result is not None
    assert result["user_code"] == "SKFG-WWXP"
    assert result["verification_url"] == "https://login.dingtalk.com/oauth2/device/verify.htm?user_code=SKFG-WWXP"
    assert result["expires_in"] == 900


def test_parse_device_code_returns_none_before_code_appears():
    from docupipe_manager.services.credential_service import _parse_device_code_from_stderr
    assert _parse_device_code_from_stderr("● Step 1: Requesting device authorization code...\n") is None


def test_parse_device_code_waits_for_both_code_and_url():
    from docupipe_manager.services.credential_service import _parse_device_code_from_stderr
    # authorization code 行先出现，带 user_code 的 url 尚未到达
    partial = "  │    authorization code: SKFG-WWXP                                                   │\n"
    assert _parse_device_code_from_stderr(partial) is None


def test_parse_device_error_extracts_message():
    from docupipe_manager.services.credential_service import _parse_device_error
    assert _parse_device_error(_DEVICE_STDERR_FAILED) == "device authorization failed: context canceled"


def test_parse_device_error_returns_none_without_json():
    from docupipe_manager.services.credential_service import _parse_device_error
    assert _parse_device_error("just plain text, no json") is None


@pytest.mark.asyncio
async def test_start_device_login_reads_stderr_for_code(credential_service):
    """dws device flow 把验证码输出到 stderr；start 应逐行读 stderr 直到解析出 code+url。"""
    pid = uuid.uuid4()
    proc = AsyncMock()
    proc.stderr.readline = AsyncMock(side_effect=[
        b"Step 1: Requesting device authorization code...\n",
        b"    authorization code: SKFG-WWXP\n",
        b"    https://login.dingtalk.com/oauth2/device/verify.htm?user_code=SKFG-WWXP\n",
        b"",  # EOF，供后台 drain task 收尾
    ])
    with patch("docupipe_manager.services.credential_service.asyncio.create_subprocess_exec",
               AsyncMock(return_value=proc)), \
         patch("docupipe_manager.services.credential_service.mkdtemp", return_value="/tmp/dws-device-FAKE"):
        result = await credential_service.start_device_login(pid, "n")

    assert result["user_code"] == "SKFG-WWXP"
    assert "user_code=SKFG-WWXP" in result["verification_url"]
    sess = credential_service._active_sessions[result["session_key"]]
    assert sess["proc"] is proc
    assert sess["stderr_task"] is not None
    assert sess["stderr_lines"][0].startswith("Step 1")
    await sess["stderr_task"]
    credential_service._cleanup_session(result["session_key"])


@pytest.mark.asyncio
async def test_start_device_login_raises_when_dws_exits_without_code(credential_service):
    """dws 启动即崩溃(stderr EOF 且无验证码)应抛 ValueError，并清理临时目录。"""
    pid = uuid.uuid4()
    proc = AsyncMock()
    proc.stderr.readline = AsyncMock(side_effect=[b""])  # 立即 EOF
    proc.kill = MagicMock()
    with patch("docupipe_manager.services.credential_service.asyncio.create_subprocess_exec",
               AsyncMock(return_value=proc)), \
         patch("docupipe_manager.services.credential_service.mkdtemp", return_value="/tmp/dws-device-FAKE"), \
         patch("docupipe_manager.services.credential_service.shutil.rmtree") as mock_rmtree:
        with pytest.raises(ValueError):
            await credential_service.start_device_login(pid, "n")
    proc.kill.assert_called_once()
    mock_rmtree.assert_called_once()
    assert credential_service._active_sessions == {}


async def _make_device_session(**overrides):
    """构造一个 device-login session，用于 poll/finalize 测试。"""
    task = asyncio.create_task(asyncio.sleep(0))
    await task
    session = {
        "proc": AsyncMock(),
        "stderr_lines": [],
        "stderr_task": task,
        "root": "/tmp/fake-dws",
        "env": {"HOME": "/tmp/fake-dws", "DWS_DISABLE_KEYCHAIN": "1",
                "DWS_CONFIG_DIR": "/tmp/fake-dws/cfg", "DWS_CACHE_DIR": "/tmp/fake-dws/cache"},
        "name": "n",
        "project_id": uuid.uuid4(),
        "created_at": 0,
    }
    session.update(overrides)
    return session


@pytest.mark.asyncio
async def test_poll_device_login_pending_when_running(credential_service):
    session = await _make_device_session()
    session["proc"].returncode = None
    credential_service._active_sessions["sk"] = session
    assert await credential_service.poll_device_login("sk") == {"status": "pending"}
    # 仍在运行时不应清理 session
    assert "sk" in credential_service._active_sessions


@pytest.mark.asyncio
async def test_poll_device_login_success_on_zero_exit(credential_service):
    session = await _make_device_session()
    session["proc"].returncode = 0
    session["stderr_lines"] = ["Step 2: Waiting...\n"]
    credential_service._active_sessions["sk"] = session
    result = await credential_service.poll_device_login("sk")
    assert result["status"] == "success"
    # 成功不清理，留给 finalize
    assert "sk" in credential_service._active_sessions


@pytest.mark.asyncio
async def test_poll_device_login_failed_extracts_error_message(credential_service):
    session = await _make_device_session()
    session["proc"].returncode = 1
    session["stderr_lines"] = ['{"error": {"message": "device authorization failed: denied"}}']
    credential_service._active_sessions["sk"] = session
    result = await credential_service.poll_device_login("sk")
    assert result["status"] == "failed"
    assert result["error"] == "device authorization failed: denied"
    # 失败应清理 session
    assert "sk" not in credential_service._active_sessions


@pytest.mark.asyncio
async def test_poll_device_login_failed_without_json_uses_exit_code(credential_service):
    session = await _make_device_session()
    session["proc"].returncode = 2
    session["stderr_lines"] = ["some plain stderr text"]
    credential_service._active_sessions["sk"] = session
    result = await credential_service.poll_device_login("sk")
    assert result["status"] == "failed"
    assert result["error"] == "dws exited with code 2"


@pytest.mark.asyncio
async def test_poll_device_login_session_not_found(credential_service):
    result = await credential_service.poll_device_login("missing")
    assert result == {"status": "failed", "error": "Session not found"}
