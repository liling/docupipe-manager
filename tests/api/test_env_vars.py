"""Tests for project env var API endpoints."""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import override_get_current_user, clear_overrides


def _row(**kw):
    r = MagicMock()
    r.id = kw.get("id", uuid.uuid4())
    r.key = kw.get("key", "FOO")
    r.value = kw.get("value", "plain")
    r.is_secret = kw.get("is_secret", False)
    r.description = kw.get("description", None)
    r.created_at = kw.get("created_at", "2026-01-01T00:00:00+00:00")
    return r


def _mock_engine(rows=None, fetchone_row=None):
    mock_conn = AsyncMock()
    if rows is not None:
        mock_conn.execute.return_value = MagicMock(fetchall=MagicMock(return_value=rows))
    elif fetchone_row is not None:
        mock_conn.execute.return_value = MagicMock(
            fetchall=MagicMock(return_value=[]),
            fetchone=MagicMock(return_value=fetchone_row),
        )
    else:
        mock_conn.execute.return_value = MagicMock(
            fetchall=MagicMock(return_value=[]), fetchone=MagicMock(return_value=None)
        )
    mock_engine = MagicMock()
    mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
    return mock_engine


@pytest.mark.asyncio
async def test_list_masks_secret_value(async_client):
    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    pid = uuid.uuid4()
    rows = [_row(key="FOO", value="plain", is_secret=False),
            _row(key="BAR", value="enc", is_secret=True)]
    with patch("docupipe_manager.api.env_vars._require_access_async", new=AsyncMock(return_value={"role": "admin"})):
        with patch("docupipe_manager.api.env_vars._get_engine", return_value=_mock_engine(rows=rows)):
            r = await async_client.get(f"/api/projects/{pid}/env-vars")
            assert r.status_code == 200
            data = r.json()
            assert data[0]["value"] == "plain"
            assert data[1]["value"] is None
            assert data[1]["is_secret"] is True
    clear_overrides()


@pytest.mark.asyncio
async def test_create_plain_var(async_client):
    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    pid = uuid.uuid4()
    with patch("docupipe_manager.api.env_vars._require_access_async", new=AsyncMock(return_value={"role": "admin"})):
        with patch("docupipe_manager.api.env_vars._get_engine", return_value=_mock_engine(fetchone_row=None)):
            r = await async_client.post(f"/api/projects/{pid}/env-vars",
                                        json={"key": "FOO", "value": "bar"})
            assert r.status_code == 200
            assert "id" in r.json()
    clear_overrides()


@pytest.mark.asyncio
async def test_create_invalid_key(async_client):
    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    pid = uuid.uuid4()
    with patch("docupipe_manager.api.env_vars._require_access_async", new=AsyncMock(return_value={"role": "admin"})):
        r = await async_client.post(f"/api/projects/{pid}/env-vars",
                                    json={"key": "1-bad", "value": "x"})
        assert r.status_code == 422
    clear_overrides()


@pytest.mark.asyncio
async def test_create_duplicate_key_conflict(async_client):
    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    pid = uuid.uuid4()
    existing = _row(key="FOO")
    with patch("docupipe_manager.api.env_vars._require_access_async", new=AsyncMock(return_value={"role": "admin"})):
        with patch("docupipe_manager.api.env_vars._get_engine", return_value=_mock_engine(fetchone_row=existing)):
            r = await async_client.post(f"/api/projects/{pid}/env-vars",
                                        json={"key": "FOO", "value": "x"})
            assert r.status_code == 409
    clear_overrides()


@pytest.mark.asyncio
async def test_update_secret_empty_value_keeps_original(async_client):
    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    pid = uuid.uuid4()
    var_id = uuid.uuid4()
    current = _row(id=var_id, key="BAR", value="ciphertext", is_secret=True)
    with patch("docupipe_manager.api.env_vars._require_access_async", new=AsyncMock(return_value={"role": "admin"})):
        with patch("docupipe_manager.api.env_vars._get_engine", return_value=_mock_engine(fetchone_row=current)):
            r = await async_client.put(f"/api/projects/{pid}/env-vars/{var_id}",
                                       json={"description": "new desc"})
            assert r.status_code == 200
    clear_overrides()


@pytest.mark.asyncio
async def test_delete_var(async_client):
    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    pid = uuid.uuid4()
    var_id = uuid.uuid4()
    existing = _row(id=var_id)
    with patch("docupipe_manager.api.env_vars._require_access_async", new=AsyncMock(return_value={"role": "admin"})):
        with patch("docupipe_manager.api.env_vars._get_engine", return_value=_mock_engine(fetchone_row=existing)):
            r = await async_client.delete(f"/api/projects/{pid}/env-vars/{var_id}")
            assert r.status_code == 200
    clear_overrides()
