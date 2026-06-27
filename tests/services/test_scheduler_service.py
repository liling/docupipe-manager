import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from docupipe_manager.models.task import Task, TaskStatus
from docupipe_manager.services.scheduler_service import SchedulerService


@pytest.fixture
def scheduler_service():
    runner = MagicMock()
    runner.start_run = AsyncMock()
    credential = MagicMock()
    credential.refresh_credential = AsyncMock()
    engine = MagicMock()
    settings = MagicMock()
    settings.credential_keepalive_enabled = True
    settings.credential_keepalive_cron = "0 3 * * *"
    settings.credential_keepalive_jitter_seconds = 0
    return SchedulerService(runner, credential, engine, settings)


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


@pytest.mark.asyncio
async def test_schedule_keepalive_registers_job(scheduler_service):
    cid = uuid.uuid4()
    await scheduler_service.schedule_keepalive(cid)
    assert scheduler_service._scheduler.get_job(f"keepalive-{cid}") is not None


@pytest.mark.asyncio
async def test_unschedule_keepalive_removes_job(scheduler_service):
    cid = uuid.uuid4()
    scheduler_service._scheduler.add_job(lambda: None, "interval", seconds=60, id=f"keepalive-{cid}")
    await scheduler_service.unschedule_keepalive(cid)
    assert scheduler_service._scheduler.get_job(f"keepalive-{cid}") is None


@pytest.mark.asyncio
async def test_keepalive_disabled_does_not_register(scheduler_service):
    scheduler_service._settings.credential_keepalive_enabled = False
    cid = uuid.uuid4()
    await scheduler_service.schedule_keepalive(cid)
    assert scheduler_service._scheduler.get_job(f"keepalive-{cid}") is None


@pytest.mark.asyncio
async def test_scheduled_keepalive_calls_refresh(scheduler_service):
    from docupipe_manager.models.dws_credential import CredentialStatus
    cid = uuid.uuid4()
    cred_mock = MagicMock()
    cred_mock.status = CredentialStatus.active
    with patch.object(scheduler_service, "_session_factory") as mock_sf:
        ms = AsyncMock()
        ms.__aenter__.return_value = ms
        ms.get = AsyncMock(return_value=cred_mock)
        mock_sf.return_value = ms
        await scheduler_service._scheduled_keepalive(cid)
    scheduler_service._credential.refresh_credential.assert_awaited_once_with(cid)


@pytest.mark.asyncio
async def test_scheduled_keepalive_skips_inactive(scheduler_service):
    from docupipe_manager.models.dws_credential import CredentialStatus
    cid = uuid.uuid4()
    cred_mock = MagicMock()
    cred_mock.status = CredentialStatus.revoked
    with patch.object(scheduler_service, "_session_factory") as mock_sf:
        ms = AsyncMock()
        ms.__aenter__.return_value = ms
        ms.get = AsyncMock(return_value=cred_mock)
        mock_sf.return_value = ms
        await scheduler_service._scheduled_keepalive(cid)
    scheduler_service._credential.refresh_credential.assert_not_awaited()


@pytest.mark.asyncio
async def test_scheduled_keepalive_skips_missing(scheduler_service):
    cid = uuid.uuid4()
    with patch.object(scheduler_service, "_session_factory") as mock_sf:
        ms = AsyncMock()
        ms.__aenter__.return_value = ms
        ms.get = AsyncMock(return_value=None)
        mock_sf.return_value = ms
        await scheduler_service._scheduled_keepalive(cid)
    scheduler_service._credential.refresh_credential.assert_not_awaited()


@pytest.mark.asyncio
async def test_reload_all_registers_keepalive_jobs(scheduler_service):
    from docupipe_manager.models.dws_credential import CredentialStatus, DwsCredential
    cid = uuid.uuid4()
    cred_mock = MagicMock(spec=DwsCredential)
    cred_mock.id = cid
    cred_mock.status = CredentialStatus.active
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [cred_mock]
    with patch.object(scheduler_service, "_session_factory") as mock_sf:
        ms = AsyncMock()
        ms.__aenter__.return_value = ms
        ms.execute = AsyncMock(return_value=mock_result)
        mock_sf.return_value = ms
        await scheduler_service._reload_all()
    assert scheduler_service._scheduler.get_job(f"keepalive-{cid}") is not None


@pytest.mark.asyncio
async def test_scheduled_keepalive_applies_jitter(scheduler_service):
    from docupipe_manager.models.dws_credential import CredentialStatus
    scheduler_service._settings.credential_keepalive_jitter_seconds = 300
    cred_mock = MagicMock()
    cred_mock.status = CredentialStatus.active
    with patch.object(scheduler_service, "_session_factory") as mock_sf, \
         patch("docupipe_manager.services.scheduler_service.asyncio.sleep", new=AsyncMock()) as mock_sleep, \
         patch("docupipe_manager.services.scheduler_service.random.uniform", return_value=42.0) as mock_uniform:
        ms = AsyncMock()
        ms.__aenter__.return_value = ms
        ms.get = AsyncMock(return_value=cred_mock)
        mock_sf.return_value = ms
        await scheduler_service._scheduled_keepalive(uuid.uuid4())
    mock_uniform.assert_called_once_with(0, 300)
    mock_sleep.assert_awaited_once_with(42.0)
    scheduler_service._credential.refresh_credential.assert_awaited_once()


@pytest.mark.asyncio
async def test_scheduled_keepalive_no_sleep_when_jitter_zero(scheduler_service):
    from docupipe_manager.models.dws_credential import CredentialStatus
    scheduler_service._settings.credential_keepalive_jitter_seconds = 0
    cred_mock = MagicMock()
    cred_mock.status = CredentialStatus.active
    with patch.object(scheduler_service, "_session_factory") as mock_sf, \
         patch("docupipe_manager.services.scheduler_service.asyncio.sleep", new=AsyncMock()) as mock_sleep:
        ms = AsyncMock()
        ms.__aenter__.return_value = ms
        ms.get = AsyncMock(return_value=cred_mock)
        mock_sf.return_value = ms
        await scheduler_service._scheduled_keepalive(uuid.uuid4())
    mock_sleep.assert_not_awaited()
    scheduler_service._credential.refresh_credential.assert_awaited_once()
