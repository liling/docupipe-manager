"""Tests for task API endpoints (Task 9)."""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import override_get_current_user, clear_overrides

VALID_YAML = "pipelines:\n  - name: p1\n"


@pytest.mark.asyncio
async def test_create_task_invalid_yaml(async_client):
    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    pid = uuid.uuid4()
    r = await async_client.post(f"/docupipe/api/projects/{pid}/tasks",
                                json={"name": "t", "slug": "t", "config_yaml": "not: a: list"})
    assert r.status_code == 422
    clear_overrides()


@pytest.mark.asyncio
async def test_create_task_ok(async_client):
    uid = str(uuid.uuid4())
    override_get_current_user({"id": uid, "role": "admin"})
    pid = uuid.uuid4()
    with (
        patch("docupipe_manager.deps.get_engine") as mock_get_engine,
        patch("docupipe_manager.deps.get_scheduler") as mock_get_scheduler,
    ):
        mock_conn = AsyncMock()
        mock_engine = MagicMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_get_engine.return_value = mock_engine
        mock_get_scheduler.return_value.schedule_task = AsyncMock()
        r = await async_client.post(f"/docupipe/api/projects/{pid}/tasks",
                                    json={"name": "t", "slug": "t", "config_yaml": VALID_YAML})
        assert r.status_code == 200
        assert "id" in r.json()
    clear_overrides()


@pytest.mark.asyncio
async def test_get_task_not_found(async_client):
    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    pid = uuid.uuid4()
    task_id = uuid.uuid4()
    with patch("docupipe_manager.deps.get_engine") as mock_get_engine:
        mock_conn = AsyncMock()
        mock_conn.execute.return_value = MagicMock(fetchone=MagicMock(return_value=None))
        mock_engine = MagicMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_get_engine.return_value = mock_engine
        r = await async_client.get(f"/docupipe/api/projects/{pid}/tasks/{task_id}")
        assert r.status_code == 404
    clear_overrides()


@pytest.mark.asyncio
async def test_get_task_ok(async_client):
    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    pid = uuid.uuid4()
    task_id = uuid.uuid4()
    mock_row = MagicMock()
    mock_row.id = task_id
    mock_row.name = "test-task"
    mock_row.slug = "test-task"
    mock_row.description = "desc"
    mock_row.config_yaml = VALID_YAML
    mock_row.credential_id = None
    mock_row.credential_type = None
    mock_row.schedule_cron = None
    mock_row.schedule_enabled = True
    mock_row.schedule_pipeline = None
    mock_row.schedule_mode = "incremental"
    mock_row.status = "active"
    with patch("docupipe_manager.deps.get_engine") as mock_get_engine:
        mock_conn = AsyncMock()
        mock_conn.execute.return_value = MagicMock(fetchone=MagicMock(return_value=mock_row))
        mock_engine = MagicMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_get_engine.return_value = mock_engine
        r = await async_client.get(f"/docupipe/api/projects/{pid}/tasks/{task_id}")
        assert r.status_code == 200
        data = r.json()
        assert data["name"] == "test-task"
    clear_overrides()


@pytest.mark.asyncio
async def test_trigger_task_ok(async_client):
    uid = str(uuid.uuid4())
    override_get_current_user({"id": uid, "role": "admin"})
    pid = uuid.uuid4()
    task_id = uuid.uuid4()
    mock_run = MagicMock()
    mock_run.id = uuid.uuid4()
    mock_run.status = "running"
    mock_task_row = MagicMock()
    mock_task_row.schedule_pipeline = None
    mock_task_row.schedule_mode = "incremental"
    with (
        patch("docupipe_manager.deps.get_engine") as mock_get_engine,
        patch("docupipe_manager.deps.get_runner") as mock_get_runner,
    ):
        mock_conn = AsyncMock()
        mock_conn.execute.return_value = MagicMock(fetchone=MagicMock(return_value=mock_task_row))
        mock_engine = MagicMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_get_engine.return_value = mock_engine
        mock_get_runner.return_value.start_run = AsyncMock(return_value=mock_run)
        r = await async_client.post(f"/docupipe/api/projects/{pid}/tasks/{task_id}/trigger", json={})
        assert r.status_code == 200
        data = r.json()
        assert data["run_id"] == str(mock_run.id)
        assert data["status"] == "running"
    clear_overrides()
