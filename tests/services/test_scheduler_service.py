import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from croniter import croniter

from docupipe_manager.models.docupipe_project import DocupipeProject, ProjectStatus
from docupipe_manager.services.scheduler_service import SchedulerService


@pytest.fixture
def scheduler_service():
    runner = MagicMock()
    runner.start_run = AsyncMock()
    engine = MagicMock()
    settings = MagicMock()
    return SchedulerService(runner, engine, settings)


@pytest.mark.asyncio
async def test_schedule_project(scheduler_service):
    with patch.object(scheduler_service, "_session_factory") as mock_sf:
        mock_session = AsyncMock()
        mock_sf.return_value.__aenter__.return_value = mock_session
        mock_project = MagicMock(spec=DocupipeProject)
        mock_project.status = ProjectStatus.active
        mock_project.schedule_enabled = True
        mock_project.schedule_cron = "0 3 * * *"
        mock_project.slug = "test-project"
        mock_session.get = AsyncMock(return_value=mock_project)

        await scheduler_service.schedule_project(uuid.uuid4())
        assert len(scheduler_service._scheduler.get_jobs()) > 0


@pytest.mark.asyncio
async def test_unschedule_project(scheduler_service):
    project_id = uuid.uuid4()
    scheduler_service._scheduler.add_job(
        lambda: None, "interval", seconds=60, id=f"project-{project_id}"
    )
    assert scheduler_service._scheduler.get_job(f"project-{project_id}") is not None
    await scheduler_service.unschedule_project(project_id)
    assert scheduler_service._scheduler.get_job(f"project-{project_id}") is None


@pytest.mark.asyncio
async def test_schedule_project_paused(scheduler_service):
    with patch.object(scheduler_service, "_session_factory") as mock_sf:
        mock_session = AsyncMock()
        mock_sf.return_value.__aenter__.return_value = mock_session
        mock_project = MagicMock(spec=DocupipeProject)
        mock_project.status = ProjectStatus.paused
        mock_project.schedule_enabled = True
        mock_project.schedule_cron = "0 3 * * *"
        mock_session.get = AsyncMock(return_value=mock_project)

        await scheduler_service.schedule_project(uuid.uuid4())
        assert len(scheduler_service._scheduler.get_jobs()) == 0


@pytest.mark.asyncio
async def test_schedule_project_disabled(scheduler_service):
    with patch.object(scheduler_service, "_session_factory") as mock_sf:
        mock_session = AsyncMock()
        mock_sf.return_value.__aenter__.return_value = mock_session
        mock_project = MagicMock(spec=DocupipeProject)
        mock_project.status = ProjectStatus.active
        mock_project.schedule_enabled = False
        mock_project.schedule_cron = "0 3 * * *"
        mock_session.get = AsyncMock(return_value=mock_project)

        await scheduler_service.schedule_project(uuid.uuid4())
        assert len(scheduler_service._scheduler.get_jobs()) == 0


@pytest.mark.asyncio
async def test_start_stop(scheduler_service):
    with patch.object(scheduler_service, "_reload_all") as mock_reload:
        mock_reload.return_value = None
        await scheduler_service.start()
        await scheduler_service.stop()
