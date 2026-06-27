import asyncio
import json
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
    session_obj = {"home_dir": "/tmp/fake-home", "name": "n", "project_id": pid}

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
async def test_ensure_dws_state_already_exists(credential_service):
    with patch("docupipe_manager.services.credential_service.os.path.exists", return_value=True):
        await credential_service._ensure_dws_state()


@pytest.mark.asyncio
async def test_ensure_dws_state_bootstraps(credential_service):
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(b"[]", b""))
    proc.returncode = 0
    with patch("docupipe_manager.services.credential_service.os.path.exists", return_value=False), \
         patch("docupipe_manager.services.credential_service.asyncio.create_subprocess_exec",
               AsyncMock(return_value=proc)) as mock_exec:
        await credential_service._ensure_dws_state()
    mock_exec.assert_called_once()


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
