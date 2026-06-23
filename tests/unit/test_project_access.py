# tests/unit/test_project_access.py
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
    with patch("docupipe_manager.auth.project_access.get_engine") as mock_ge:
        mock_conn = AsyncMock()
        mock_row = MagicMock()
        mock_row.owner_id = owner_id
        mock_conn.execute = AsyncMock(return_value=MagicMock(fetchone=MagicMock(return_value=mock_row)))
        mock_engine = MagicMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_ge.return_value = mock_engine
        assert await is_project_owner(uuid.uuid4(), user) is True


@pytest.mark.asyncio
async def test_not_owner():
    user = {"id": str(uuid.uuid4()), "role": "user"}
    with patch("docupipe_manager.auth.project_access.get_engine") as mock_ge:
        mock_conn = AsyncMock()
        mock_row = MagicMock()
        mock_row.owner_id = uuid.uuid4()
        mock_conn.execute = AsyncMock(return_value=MagicMock(fetchone=MagicMock(return_value=mock_row)))
        mock_engine = MagicMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_ge.return_value = mock_engine
        assert await is_project_owner(uuid.uuid4(), user) is False
