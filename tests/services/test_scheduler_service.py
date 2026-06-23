import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from docupipe_manager.models.task import Task, TaskStatus
from docupipe_manager.services.scheduler_service import SchedulerService


@pytest.fixture
def scheduler_service():
    runner = MagicMock()
    runner.start_run = AsyncMock()
    engine = MagicMock()
    settings = MagicMock()
    return SchedulerService(runner, engine, settings)


def _make_task(status=TaskStatus.active, enabled=True, cron="0 3 * * *", slug="t1"):
    t = MagicMock(spec=Task)
    t.status = status
    t.schedule_enabled = enabled
    t.schedule_cron = cron
    t.slug = slug
    t.schedule_pipeline = None
    t.schedule_mode = "incremental"
    return t


@pytest.mark.asyncio
async def test_schedule_task(scheduler_service):
    with patch.object(scheduler_service, "_session_factory") as mock_sf:
        mock_session = AsyncMock()
        mock_sf.return_value.__aenter__.return_value = mock_session
        mock_session.get = AsyncMock(return_value=_make_task())
        await scheduler_service.schedule_task(uuid.uuid4())
        assert len(scheduler_service._scheduler.get_jobs()) > 0


@pytest.mark.asyncio
async def test_unschedule_task(scheduler_service):
    tid = uuid.uuid4()
    scheduler_service._scheduler.add_job(lambda: None, "interval", seconds=60, id=f"task-{tid}")
    await scheduler_service.unschedule_task(tid)
    assert scheduler_service._scheduler.get_job(f"task-{tid}") is None


@pytest.mark.asyncio
async def test_schedule_task_paused(scheduler_service):
    with patch.object(scheduler_service, "_session_factory") as mock_sf:
        mock_session = AsyncMock()
        mock_sf.return_value.__aenter__.return_value = mock_session
        mock_session.get = AsyncMock(return_value=_make_task(status=TaskStatus.paused))
        await scheduler_service.schedule_task(uuid.uuid4())
        assert len(scheduler_service._scheduler.get_jobs()) == 0


@pytest.mark.asyncio
async def test_scheduled_run_calls_runner(scheduler_service):
    tid = uuid.uuid4()
    with patch.object(scheduler_service, "_session_factory") as mock_sf:
        mock_session = AsyncMock()
        mock_sf.return_value.__aenter__.return_value = mock_session
        mock_session.get = AsyncMock(return_value=_make_task())
        await scheduler_service._scheduled_run(tid)
        scheduler_service._runner.start_run.assert_awaited_once()
        kwargs = scheduler_service._runner.start_run.call_args.kwargs
        assert kwargs["task_id"] == tid
        assert kwargs["trigger_type"] == "scheduled"
