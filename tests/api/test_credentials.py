"""Tests for credential API endpoints (Task 8)."""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import override_get_current_user, clear_overrides


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


@pytest.mark.asyncio
async def test_start_device_login(async_client):
    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    pid = uuid.uuid4()
    with patch("docupipe_manager.api.credentials._require_access_async", new=AsyncMock(return_value={"role": "admin"})):
        with patch("docupipe_manager.main.app") as mock_app:
            mock_app.state.credential.start_device_login = AsyncMock(
                return_value={"session_key": "sk", "verification_url": "url", "user_code": "uc"}
            )
            r = await async_client.post(f"/api/projects/{pid}/credentials/device-login/start", params={"name": "test-cred"})
            assert r.status_code == 200
            data = r.json()
            assert data["session_key"] == "sk"
    clear_overrides()


@pytest.mark.asyncio
async def test_poll_device_login(async_client):
    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    pid = uuid.uuid4()
    with patch("docupipe_manager.api.credentials._require_access_async", new=AsyncMock(return_value={"role": "admin"})):
        with patch("docupipe_manager.main.app") as mock_app:
            mock_app.state.credential.poll_device_login = AsyncMock(return_value={"status": "pending"})
            r = await async_client.get(f"/api/projects/{pid}/credentials/device-login/poll", params={"session_key": "sk"})
            assert r.status_code == 200
            assert r.json()["status"] == "pending"
    clear_overrides()


@pytest.mark.asyncio
async def test_finalize_device_login(async_client):
    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    pid = uuid.uuid4()
    cred_id = uuid.uuid4()
    with patch("docupipe_manager.api.credentials._require_access_async", new=AsyncMock(return_value={"role": "admin"})):
        with patch("docupipe_manager.main.app") as mock_app:
            mock_cred = MagicMock()
            mock_cred.id = cred_id
            mock_app.state.credential.finalize_login = AsyncMock(return_value=mock_cred)
            r = await async_client.post(
                f"/api/projects/{pid}/credentials/device-login/finalize",
                json={"session_key": "sk", "name": "my-cred"},
            )
            assert r.status_code == 200
            assert r.json()["id"] == str(cred_id)
    clear_overrides()


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


@pytest.mark.asyncio
async def test_revoke_credential(async_client):
    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    pid = uuid.uuid4()
    cid = uuid.uuid4()
    with patch("docupipe_manager.api.credentials._require_access_async", new=AsyncMock(return_value={"role": "admin"})):
        with patch("docupipe_manager.main.app") as mock_app:
            mock_app.state.credential.revoke = AsyncMock(return_value=None)
            r = await async_client.delete(f"/api/projects/{pid}/credentials/{cid}")
            assert r.status_code == 200
            assert r.json()["status"] == "revoked"
    clear_overrides()


@pytest.mark.asyncio
async def test_revoke_credential_404(async_client):
    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    pid = uuid.uuid4()
    cid = uuid.uuid4()
    with patch("docupipe_manager.api.credentials._require_access_async", new=AsyncMock(return_value={"role": "admin"})):
        with patch("docupipe_manager.main.app") as mock_app:
            mock_app.state.credential.revoke = AsyncMock(side_effect=ValueError("not found"))
            r = await async_client.delete(f"/api/projects/{pid}/credentials/{cid}")
            assert r.status_code == 404
    clear_overrides()


@pytest.mark.asyncio
async def test_rename_credential(async_client):
    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    pid = uuid.uuid4(); cid = uuid.uuid4()
    with patch("docupipe_manager.api.credentials._require_access_async", new=AsyncMock(return_value={"role": "admin"})):
        with patch("docupipe_manager.main.app") as mock_app:
            mock_app.state.credential.rename_credential = AsyncMock()
            mock_cred = MagicMock(); mock_cred.id = cid; mock_cred.name = "renamed"
            mock_app.state.credential.rename_credential.return_value = mock_cred
            r = await async_client.put(
                f"/api/projects/{pid}/credentials/{cid}",
                json={"name": "renamed"},
            )
            assert r.status_code == 200
            assert r.json()["name"] == "renamed"
    clear_overrides()


@pytest.mark.asyncio
async def test_rename_credential_404(async_client):
    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    pid = uuid.uuid4(); cid = uuid.uuid4()
    with patch("docupipe_manager.api.credentials._require_access_async", new=AsyncMock(return_value={"role": "admin"})):
        with patch("docupipe_manager.main.app") as mock_app:
            mock_app.state.credential.rename_credential = AsyncMock(side_effect=ValueError("not found"))
            r = await async_client.put(
                f"/api/projects/{pid}/credentials/{cid}",
                json={"name": "x"},
            )
            assert r.status_code == 404
    clear_overrides()
