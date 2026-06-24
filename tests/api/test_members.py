"""Tests for member API endpoints (Task 7)."""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from docupipe_manager.api.projects import _require_access_async, _require_owner_async
from docupipe_manager.main import app

from tests.conftest import override_get_current_user, clear_overrides


@pytest.mark.asyncio
async def test_list_members(async_client):
    owner_id = uuid.uuid4()
    member_id = uuid.uuid4()
    override_get_current_user({"id": str(owner_id), "username": "o", "role": "user"})
    app.dependency_overrides[_require_access_async] = lambda: {"id": str(owner_id), "role": "user"}
    pid = uuid.uuid4()
    with patch("docupipe_manager.api.members._get_engine") as mock_ge:
        mock_owner_row = MagicMock()
        mock_owner_row.owner_id = owner_id
        mock_member_row = MagicMock()
        mock_member_row.user_id = member_id
        mock_member_row.added_by = owner_id
        mock_member_row.created_at = "2025-01-01T00:00:00"
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(side_effect=[
            MagicMock(fetchone=MagicMock(return_value=mock_owner_row)),
            MagicMock(fetchall=MagicMock(return_value=[mock_member_row])),
        ])
        mock_engine = MagicMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_ge.return_value = mock_engine
        app.state.platform_client.batch_get_users = AsyncMock(return_value={
            owner_id: {"username": "owner", "display_name": "Owner", "email": "owner@x.com", "role": "admin"},
            member_id: {"username": "member", "display_name": "Member", "email": "member@x.com", "role": "user"},
        })
        r = await async_client.get(f"/docupipe/api/projects/{pid}/members")
        assert r.status_code == 200
        data = r.json()
        assert data["owner"]["user_id"] == str(owner_id)
        assert data["owner"]["is_owner"] is True
        assert data["owner"]["username"] == "owner"
        assert data["owner"]["display_name"] == "Owner"
        assert data["owner"]["email"] == "owner@x.com"
        assert len(data["members"]) == 1
        assert data["members"][0]["user_id"] == str(member_id)
        assert data["members"][0]["username"] == "member"
        assert data["members"][0]["display_name"] == "Member"
    clear_overrides()


@pytest.mark.asyncio
async def test_add_member_owner_ok(async_client):
    owner_id = uuid.uuid4()
    override_get_current_user({"id": str(owner_id), "username": "o", "role": "user"})
    app.dependency_overrides[_require_owner_async] = lambda: {"id": str(owner_id), "role": "user"}
    pid = uuid.uuid4()
    with patch("docupipe_manager.api.members._get_engine") as mock_ge:
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(side_effect=[
            MagicMock(fetchone=MagicMock(return_value=MagicMock(owner_id=owner_id))),
            MagicMock(fetchone=MagicMock(return_value=None)),
            MagicMock(),
        ])
        mock_engine = MagicMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_ge.return_value = mock_engine
        r = await async_client.post(f"/docupipe/api/projects/{pid}/members", json={"user_id": str(uuid.uuid4())})
        assert r.status_code == 200
        assert r.json()["status"] == "added"
    clear_overrides()


@pytest.mark.asyncio
async def test_add_member_duplicate(async_client):
    owner_id = uuid.uuid4()
    member_id = uuid.uuid4()
    override_get_current_user({"id": str(owner_id), "username": "o", "role": "user"})
    app.dependency_overrides[_require_owner_async] = lambda: {"id": str(owner_id), "role": "user"}
    pid = uuid.uuid4()
    with patch("docupipe_manager.api.members._get_engine") as mock_ge:
        mock_conn = AsyncMock()
        existing_row = MagicMock()
        existing_row.user_id = member_id
        mock_conn.execute = AsyncMock(side_effect=[
            MagicMock(fetchone=MagicMock(return_value=MagicMock(owner_id=owner_id))),
            MagicMock(fetchone=MagicMock(return_value=existing_row)),
        ])
        mock_engine = MagicMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_ge.return_value = mock_engine
        r = await async_client.post(f"/docupipe/api/projects/{pid}/members", json={"user_id": str(member_id)})
        assert r.status_code == 409
    clear_overrides()


@pytest.mark.asyncio
async def test_add_member_self_owner(async_client):
    owner_id = uuid.uuid4()
    override_get_current_user({"id": str(owner_id), "username": "o", "role": "user"})
    app.dependency_overrides[_require_owner_async] = lambda: {"id": str(owner_id), "role": "user"}
    pid = uuid.uuid4()
    with patch("docupipe_manager.api.members._get_engine") as mock_ge:
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value=MagicMock(fetchone=MagicMock(return_value=MagicMock(owner_id=owner_id))))
        mock_engine = MagicMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_ge.return_value = mock_engine
        r = await async_client.post(f"/docupipe/api/projects/{pid}/members", json={"user_id": str(owner_id)})
        assert r.status_code == 400
    clear_overrides()


@pytest.mark.asyncio
async def test_remove_member_ok(async_client):
    owner_id = uuid.uuid4()
    member_id = uuid.uuid4()
    override_get_current_user({"id": str(owner_id), "username": "o", "role": "user"})
    app.dependency_overrides[_require_owner_async] = lambda: {"id": str(owner_id), "role": "user"}
    pid = uuid.uuid4()
    with patch("docupipe_manager.api.members._get_engine") as mock_ge:
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(side_effect=[
            MagicMock(fetchone=MagicMock(return_value=MagicMock(owner_id=owner_id))),
            MagicMock(),
        ])
        mock_engine = MagicMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_ge.return_value = mock_engine
        r = await async_client.delete(f"/docupipe/api/projects/{pid}/members/{member_id}")
        assert r.status_code == 200
        assert r.json()["status"] == "removed"
    clear_overrides()


@pytest.mark.asyncio
async def test_remove_member_owner(async_client):
    owner_id = uuid.uuid4()
    override_get_current_user({"id": str(owner_id), "username": "o", "role": "user"})
    app.dependency_overrides[_require_owner_async] = lambda: {"id": str(owner_id), "role": "user"}
    pid = uuid.uuid4()
    with patch("docupipe_manager.api.members._get_engine") as mock_ge:
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value=MagicMock(fetchone=MagicMock(return_value=MagicMock(owner_id=owner_id))))
        mock_engine = MagicMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_ge.return_value = mock_engine
        r = await async_client.delete(f"/docupipe/api/projects/{pid}/members/{owner_id}")
        assert r.status_code == 400
    clear_overrides()
