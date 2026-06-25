import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from docupipe_manager.platform.client import XinyiPlatformClient
from docupipe_manager.platform.config import PlatformSettings


@pytest.fixture
def settings():
    return PlatformSettings(
        platform_url="http://platform:8000",
        oauth_client_id="dm-prod",
        oauth_client_secret="test-secret",
        oauth_redirect_uri="http://localhost:8002/auth/callback",
        request_timeout_seconds=10,
    )


def _mock_response(status_code=200, json_data=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=json_data or {})
    resp.text = "mock response"
    return resp


def _mock_client(settings, json_data=None, status_code=200, side_effect=None):
    http_mock = MagicMock()
    if side_effect:
        http_mock.post = AsyncMock(side_effect=side_effect)
    else:
        http_mock.post = AsyncMock(return_value=_mock_response(status_code, json_data))
    http_mock.aclose = AsyncMock()
    return XinyiPlatformClient(settings, http_client=http_mock), http_mock


@pytest.mark.asyncio
async def test_exchange_oauth_code_success(settings):
    client, _ = _mock_client(settings, {"access_token": "at", "refresh_token": "rt"}, 200)
    result = await client.exchange_oauth_code("code123", settings.oauth_redirect_uri)
    assert result == {"access_token": "at", "refresh_token": "rt"}


@pytest.mark.asyncio
async def test_exchange_oauth_code_failure(settings):
    client, _ = _mock_client(settings, {}, 400)
    result = await client.exchange_oauth_code("bad-code", settings.oauth_redirect_uri)
    assert result is None


@pytest.mark.asyncio
async def test_refresh_token_success(settings):
    client, _ = _mock_client(settings, {"access_token": "new-at", "refresh_token": "new-rt"}, 200)
    result = await client.refresh_token("old-rt")
    assert result == {"access_token": "new-at", "refresh_token": "new-rt"}


@pytest.mark.asyncio
async def test_refresh_token_revoked_returns_none(settings):
    client, _ = _mock_client(settings, {}, 401)
    result = await client.refresh_token("revoked-rt")
    assert result is None


@pytest.mark.asyncio
async def test_revoke_token(settings):
    client, http = _mock_client(settings, {}, 200)
    await client.revoke_token("some-token")
    http.post.assert_awaited()


@pytest.mark.asyncio
async def test_batch_get_users_retries_on_server_error(settings):
    client, _ = _mock_client(settings, {}, 500)
    uids = [uuid.uuid4()]
    result = await client.batch_get_users(uids)
    assert result == {uids[0]: None}


@pytest.mark.asyncio
async def test_batch_get_users_partial_null_for_missing(settings):
    uid1 = uuid.uuid4()
    uid2 = uuid.uuid4()
    client, _ = _mock_client(settings, {
        "users": {str(uid1): {"username": "found"}},
    }, 200)
    result = await client.batch_get_users([uid1, uid2])
    assert result[uid1] == {"username": "found"}
    assert result[uid2] is None


@pytest.mark.asyncio
async def test_batch_get_users_empty(settings):
    client, _ = _mock_client(settings, {}, 200)
    result = await client.batch_get_users([])
    assert result == {}


@pytest.mark.asyncio
async def test_push_audit_failure_does_not_block_caller(settings):
    client, _ = _mock_client(settings, side_effect=RuntimeError("network error"))
    await client.push_audit({"event": "test"})
    assert True


@pytest.mark.asyncio
async def test_aclose(settings):
    client, http = _mock_client(settings, {}, 200)
    await client.aclose()
    http.aclose.assert_awaited_once()
