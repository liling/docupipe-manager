import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import override_get_current_user, clear_overrides


@pytest.mark.asyncio
async def test_list_runs_admin(async_client):
    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    with patch("docupipe_manager.main.app") as mock_app:
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(side_effect=[
            MagicMock(scalar=MagicMock(return_value=0)),
            MagicMock(fetchall=MagicMock(return_value=[])),
        ])
        mock_engine = MagicMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_app.state.engine = mock_engine
        r = await async_client.get("/api/runs")
        assert r.status_code == 200
        assert r.json()["total"] == 0
    clear_overrides()


@pytest.mark.asyncio
async def test_list_runs_non_admin_empty(async_client):
    uid = str(uuid.uuid4())
    override_get_current_user({"id": uid, "role": "user"})
    with patch("docupipe_manager.main.app") as mock_app:
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(side_effect=[
            MagicMock(fetchall=MagicMock(return_value=[])),
        ])
        mock_engine = MagicMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_app.state.engine = mock_engine

        r = await async_client.get("/api/runs")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 0
        assert data["runs"] == []
    clear_overrides()


@pytest.mark.asyncio
async def test_get_run_not_found(async_client):
    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    with patch("docupipe_manager.main.app") as mock_app:
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
        mock_engine = MagicMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_app.state.engine = mock_engine

        r = await async_client.get(f"/api/runs/{uuid.uuid4()}")
        assert r.status_code == 404
    clear_overrides()


@pytest.mark.asyncio
async def test_cancel_run(async_client):
    run_id = uuid.uuid4()
    run_mock = MagicMock()
    run_mock.task_id = uuid.uuid4()
    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    with patch("docupipe_manager.main.app") as mock_app:
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=run_mock)
        ))
        mock_engine = MagicMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_app.state.engine = mock_engine
        mock_app.state.runner = AsyncMock()
        mock_app.state.runner.cancel_run = AsyncMock(return_value=None)

        r = await async_client.post(f"/api/runs/{run_id}/cancel")
        assert r.status_code == 200
        assert r.json()["status"] == "cancelled"
    clear_overrides()
