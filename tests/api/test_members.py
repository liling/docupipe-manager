"""Tests for member API endpoints (Task 7)."""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from docupipe_manager.api.projects import _require_access_async, _require_owner_async
from docupipe_manager.main import app

from tests.conftest import override_get_current_user, clear_overrides


class _DictCache(dict):
    """Minimal cache mock that supports get/set."""

    def get(self, key):
        return super().get(key)

    def set(self, key, value):
        self[key] = value


@pytest.mark.asyncio
async def test_list_members(async_client):
    owner_id = uuid.uuid4()
    member_id = uuid.uuid4()
    override_get_current_user({"id": str(owner_id), "username": "o", "role": "user"})
    app.dependency_overrides[_require_access_async] = lambda: {"id": str(owner_id), "role": "user"}
    pid = uuid.uuid4()

    owner_row = MagicMock()
    owner_row.user_id = owner_id
    owner_row.role = "owner"
    owner_row.created_at = "2025-01-01T00:00:00"

    member_row = MagicMock()
    member_row.user_id = member_id
    member_row.role = "member"
    member_row.created_at = "2025-01-02T00:00:00"

    cache = _DictCache()

    with (
        patch("docupipe_manager.deps.get_engine") as mock_ge,
        patch("docupipe_manager.deps.get_user_cache", return_value=cache),
        patch("docupipe_manager.deps.get_platform_client") as mock_pc,
    ):
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value=MagicMock(
            fetchall=MagicMock(return_value=[owner_row, member_row])
        ))
        mock_engine = MagicMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_ge.return_value = mock_engine

        mock_pc.return_value.batch_get_users = AsyncMock(return_value={
            owner_id: {"username": "owner", "display_name": "Owner", "email": "owner@x.com", "role": "admin"},
            member_id: {"username": "member", "display_name": "Member", "email": "member@x.com", "role": "user"},
        })

        r = await async_client.get(f"/docupipe/api/projects/{pid}/members")
        assert r.status_code == 200
        data = r.json()
        assert len(data["members"]) == 2

        owner_data = [m for m in data["members"] if m["user_id"] == str(owner_id)][0]
        assert owner_data["role"] == "owner"
        assert owner_data["username"] == "owner"
        assert owner_data["display_name"] == "Owner"
        assert owner_data["email"] == "owner@x.com"

        member_data = [m for m in data["members"] if m["user_id"] == str(member_id)][0]
        assert member_data["role"] == "member"
        assert member_data["username"] == "member"
        assert member_data["display_name"] == "Member"
        assert member_data["email"] == "member@x.com"
    clear_overrides()


@pytest.mark.asyncio
async def test_add_member_owner_ok(async_client):
    owner_id = uuid.uuid4()
    new_id = uuid.uuid4()
    override_get_current_user({"id": str(owner_id), "username": "o", "role": "user"})
    app.dependency_overrides[_require_owner_async] = lambda: {"id": str(owner_id), "role": "user"}
    pid = uuid.uuid4()
    with patch("docupipe_manager.deps.get_engine") as mock_ge:
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(side_effect=[
            MagicMock(fetchone=MagicMock(return_value=None)),  # existing: not duplicate
            MagicMock(),  # insert
        ])
        mock_engine = MagicMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_ge.return_value = mock_engine
        r = await async_client.post(f"/docupipe/api/projects/{pid}/members", json={"user_id": str(new_id)})
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
    with patch("docupipe_manager.deps.get_engine") as mock_ge:
        mock_conn = AsyncMock()
        existing_row = MagicMock()
        existing_row.user_id = member_id
        mock_conn.execute = AsyncMock(side_effect=[
            MagicMock(fetchone=MagicMock(return_value=existing_row)),  # existing: duplicate
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
    """Adding self as member returns 409 (already a member as owner)."""
    owner_id = uuid.uuid4()
    override_get_current_user({"id": str(owner_id), "username": "o", "role": "user"})
    app.dependency_overrides[_require_owner_async] = lambda: {"id": str(owner_id), "role": "user"}
    pid = uuid.uuid4()
    with patch("docupipe_manager.deps.get_engine") as mock_ge:
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value=MagicMock(fetchone=MagicMock(return_value=MagicMock())))
        mock_engine = MagicMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_ge.return_value = mock_engine
        r = await async_client.post(f"/docupipe/api/projects/{pid}/members", json={"user_id": str(owner_id)})
        assert r.status_code == 409


@pytest.mark.asyncio
async def test_remove_member_ok(async_client):
    owner_id = uuid.uuid4()
    member_id = uuid.uuid4()
    override_get_current_user({"id": str(owner_id), "username": "o", "role": "user"})
    app.dependency_overrides[_require_owner_async] = lambda: {"id": str(owner_id), "role": "user"}
    pid = uuid.uuid4()
    with patch("docupipe_manager.deps.get_engine") as mock_ge:
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(side_effect=[
            MagicMock(fetchone=MagicMock(return_value=MagicMock())),
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
    """Owner can remove themselves (no self-protection check currently)."""
    owner_id = uuid.uuid4()
    override_get_current_user({"id": str(owner_id), "username": "o", "role": "user"})
    app.dependency_overrides[_require_owner_async] = lambda: {"id": str(owner_id), "role": "user"}
    pid = uuid.uuid4()
    with patch("docupipe_manager.deps.get_engine") as mock_ge:
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value=MagicMock(fetchone=MagicMock(return_value=MagicMock())))
        mock_engine = MagicMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_ge.return_value = mock_engine
        r = await async_client.delete(f"/docupipe/api/projects/{pid}/members/{owner_id}")
        assert r.status_code == 200
