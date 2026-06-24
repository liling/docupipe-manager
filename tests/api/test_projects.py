"""Tests for project API endpoints (Task 6)."""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import override_get_current_user, clear_overrides


@pytest.mark.asyncio
async def test_create_project_requires_admin(async_client):
    override_get_current_user({"id": str(uuid.uuid4()), "username": "u", "role": "user"})
    r = await async_client.post("/docupipe/admin/api/projects", json={"name": "p", "slug": "p"})
    assert r.status_code == 403
    clear_overrides()


@pytest.mark.asyncio
async def test_create_project_admin_ok(async_client):
    uid = str(uuid.uuid4())
    override_get_current_user({"id": uid, "username": "a", "role": "admin"})
    fake_project = MagicMock()
    fake_project.id = uuid.uuid4()
    with patch("docupipe_manager.api.projects._get_engine") as mock_ge:
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value=MagicMock(fetchone=MagicMock(return_value=None)))
        mock_conn.add = MagicMock()
        mock_conn.flush = AsyncMock()

        def _add(p):
            p.id = fake_project.id
        mock_conn.add.side_effect = _add

        mock_engine = MagicMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_ge.return_value = mock_engine

        r = await async_client.post("/docupipe/admin/api/projects", json={"name": "p", "slug": "p"})
        assert r.status_code == 200
        assert "id" in r.json()
    clear_overrides()
