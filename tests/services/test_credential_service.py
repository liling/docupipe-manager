import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest

from docupipe_manager.services.credential_service import CredentialService, _parse_dt


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
                            ms.add = AsyncMock(); ms.commit = AsyncMock(); ms.refresh = AsyncMock()
                            mock_sf.return_value = ms
                            await credential_service.finalize_login("sk", "n", uid, pid)

    assert saved.get("token_expires_at") is not None
    assert saved.get("refresh_token_expires_at") is not None
    assert saved.get("credential_type") is not None
