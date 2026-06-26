"""Tests for project access helpers.
After migration 0006, owner_id was moved from projects to project_members table.
The actual code now queries project_members with 'SELECT 1'. The mock just needs
to return a truthy value for "owner match" and None/falsy for "not owner".
"""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from docupipe_manager.auth.project_access import is_project_owner, is_project_member


@pytest.mark.asyncio
async def test_admin_is_always_owner():
    user = {"id": str(uuid.uuid4()), "role": "admin"}
    assert await is_project_owner(uuid.uuid4(), user) is True


@pytest.mark.asyncio
async def test_admin_is_always_member():
    user = {"id": str(uuid.uuid4()), "role": "admin"}
    assert await is_project_member(uuid.uuid4(), user) is True


@pytest.mark.asyncio
async def test_owner_match():
    owner_id = uuid.uuid4()
    user = {"id": str(owner_id), "role": "user"}
    with patch("docupipe_manager.deps.get_engine") as mock_get_engine:
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value=MagicMock(fetchone=MagicMock(return_value=MagicMock())))
        mock_engine = MagicMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_get_engine.return_value = mock_engine
        assert await is_project_owner(uuid.uuid4(), user) is True


@pytest.mark.asyncio
async def test_not_owner():
    user = {"id": str(uuid.uuid4()), "role": "user"}
    with patch("docupipe_manager.deps.get_engine") as mock_get_engine:
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value=MagicMock(fetchone=MagicMock(return_value=None)))
        mock_engine = MagicMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_get_engine.return_value = mock_engine
        assert await is_project_owner(uuid.uuid4(), user) is False
