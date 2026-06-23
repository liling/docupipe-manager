import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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
