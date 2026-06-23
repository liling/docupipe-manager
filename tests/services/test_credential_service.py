import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from docupipe_manager.models.dws_credential import CredentialStatus
from docupipe_manager.services.credential_service import CredentialService


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
async def test_start_device_login(credential_service):
    with patch("asyncio.create_subprocess_exec") as mock_subprocess:
        mock_proc = AsyncMock()
        mock_proc.stdout.readline = AsyncMock(return_value=b'{"verification_url": "https://", "user_code": "ABC"}')
        mock_proc.returncode = None
        mock_subprocess.return_value = mock_proc

        result = await credential_service.start_device_login("test-cred")
        assert result["session_key"] is not None
        assert result["verification_url"] == "https://"
        assert result["user_code"] == "ABC"
        assert result["session_key"] in credential_service._active_sessions


@pytest.mark.asyncio
async def test_poll_device_login_pending(credential_service):
    session_key = "test-session"
    credential_service._active_sessions[session_key] = {
        "proc": AsyncMock(returncode=None),
        "home_dir": "/tmp/test",
        "name": "test",
        "created_at": 0,
    }

    result = await credential_service.poll_device_login(session_key)
    assert result["status"] == "pending"


@pytest.mark.asyncio
async def test_poll_device_login_session_not_found(credential_service):
    result = await credential_service.poll_device_login("nonexistent")
    assert result["status"] == "failed"
    assert "not found" in result["error"]


@pytest.mark.asyncio
async def test_revoke(credential_service):
    with patch.object(credential_service, "_session_factory") as mock_sf:
        mock_session = AsyncMock()
        mock_sf.return_value.__aenter__.return_value = mock_session
        mock_session.get = AsyncMock(return_value=MagicMock(status=CredentialStatus.active))

        await credential_service.revoke(uuid.uuid4(), uuid.uuid4())

        mock_session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_cleanup_expired_sessions(credential_service):
    import time
    old_key = "old-session"
    credential_service._active_sessions[old_key] = {
        "proc": AsyncMock(returncode=None),
        "home_dir": "/tmp/old",
        "name": "old",
        "created_at": time.monotonic() - 1000,
    }

    with patch("shutil.rmtree"):
        await credential_service.cleanup_expired_sessions()

    assert old_key not in credential_service._active_sessions


@pytest.mark.asyncio
async def test_list_credentials(credential_service):
    with patch.object(credential_service, "_session_factory") as mock_sf:
        mock_session = AsyncMock()
        mock_sf.return_value.__aenter__.return_value = mock_session
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)

        result = await credential_service.list_credentials()
        assert result == []
