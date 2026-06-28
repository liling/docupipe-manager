import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import override_get_current_user, clear_overrides


@pytest.mark.asyncio
async def test_list_schedules_admin_returns_sorted(async_client):
    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    items = [
        {"kind": "keepalive", "scheduler_job_id": "k1", "name": "keepalive-b",
         "cron": "0 3 * * *", "next_run_time": "2026-06-29T03:00:00+08:00",
         "context": {}, "config_enabled": True, "registered": True},
        {"kind": "task", "scheduler_job_id": "t1", "name": "task-a",
         "cron": "0 2 * * *", "next_run_time": "2026-06-29T02:00:00+08:00",
         "context": {}, "config_enabled": True, "registered": True},
        {"kind": "task", "scheduler_job_id": None, "name": "task-drift",
         "cron": "0 5 * * *", "next_run_time": None,
         "context": {}, "config_enabled": True, "registered": False},
    ]
    with patch("docupipe_manager.deps.get_scheduler") as mock_get_scheduler, \
         patch("docupipe_manager.deps.get_settings") as mock_get_settings:
        mock_sched = MagicMock()
        mock_sched.list_schedules = AsyncMock(return_value=items)
        mock_get_scheduler.return_value = mock_sched
        mock_settings = MagicMock()
        mock_settings.credential_keepalive_cron = "0 3 * * *"
        mock_settings.credential_keepalive_enabled = True
        mock_get_settings.return_value = mock_settings

        r = await async_client.get("/docupipe/api/schedules")

    assert r.status_code == 200
    data = r.json()
    names = [s["name"] for s in data["schedules"]]
    assert names == ["task-a", "keepalive-b", "task-drift"]
    assert data["count"] == 3
    assert data["keepalive_cron"] == "0 3 * * *"
    assert data["keepalive_enabled"] is True
    clear_overrides()


@pytest.mark.asyncio
async def test_list_schedules_non_admin_forbidden(async_client):
    override_get_current_user({"id": str(uuid.uuid4()), "role": "user"})
    r = await async_client.get("/docupipe/api/schedules")
    assert r.status_code == 403
    clear_overrides()