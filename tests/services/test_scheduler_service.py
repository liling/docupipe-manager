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


@pytest.mark.asyncio
async def test_list_schedules_returns_task_and_keepalive(scheduler_service):
    from datetime import datetime, timezone, timedelta
    from apscheduler.triggers.cron import CronTrigger
    from docupipe_manager.models.project import Project
    from docupipe_manager.models.dws_credential import DwsCredential, CredentialStatus

    tz = timezone(timedelta(hours=8))
    tid, cid = uuid.uuid4(), uuid.uuid4()

    task_job = MagicMock()
    task_job.id = f"task-{tid}"
    task_job.trigger = CronTrigger.from_crontab("0 2 * * *")
    task_job.next_run_time = datetime(2026, 6, 29, 2, 0, tzinfo=tz)

    ka_job = MagicMock()
    ka_job.id = f"keepalive-{cid}"
    ka_job.trigger = CronTrigger.from_crontab("0 3 * * *")
    ka_job.next_run_time = datetime(2026, 6, 29, 3, 0, tzinfo=tz)

    scheduler_service._scheduler.get_jobs = MagicMock(return_value=[task_job, ka_job])

    task_mock = MagicMock(spec=Task)
    task_mock.id = tid
    task_mock.slug = "t1"
    task_mock.name = "Task One"
    task_mock.schedule_enabled = True
    task_mock.schedule_cron = "0 2 * * *"
    proj_mock = MagicMock(spec=Project)
    proj_mock.id = uuid.uuid4()
    proj_mock.name = "Proj A"

    cred_mock = MagicMock(spec=DwsCredential)
    cred_mock.id = cid
    cred_mock.name = "Cred One"
    cred_mock.status = CredentialStatus.active

    tresult = MagicMock()
    tresult.all.return_value = [(task_mock, proj_mock)]
    cresult = MagicMock()
    cresult.scalars.return_value = [cred_mock]

    with patch.object(scheduler_service, "_session_factory") as mock_sf:
        ms = AsyncMock()
        mock_sf.return_value.__aenter__.return_value = ms
        ms.execute = AsyncMock(side_effect=[tresult, cresult])
        items = await scheduler_service.list_schedules()

    by_kind = {it["kind"]: it for it in items}
    t = by_kind["task"]
    assert t["scheduler_job_id"] == f"task-{tid}"
    assert t["cron"] == "0 2 * * *"
    assert t["next_run_time"].startswith("2026-06-29T02:00")
    assert t["registered"] is True
    assert t["context"]["task_name"] == "Task One"
    assert t["context"]["project_name"] == "Proj A"
    k = by_kind["keepalive"]
    assert k["cron"] == "0 3 * * *"
    assert k["registered"] is True
    assert k["context"]["credential_name"] == "Cred One"


@pytest.mark.asyncio
async def test_list_schedules_paused_job_next_run_none(scheduler_service):
    from apscheduler.triggers.cron import CronTrigger
    from docupipe_manager.models.dws_credential import DwsCredential, CredentialStatus

    cid = uuid.uuid4()
    ka_job = MagicMock()
    ka_job.id = f"keepalive-{cid}"
    ka_job.trigger = CronTrigger.from_crontab("0 3 * * *")
    ka_job.next_run_time = None
    scheduler_service._scheduler.get_jobs = MagicMock(return_value=[ka_job])

    cred_mock = MagicMock(spec=DwsCredential)
    cred_mock.id = cid
    cred_mock.name = "Paused Cred"
    cred_mock.status = CredentialStatus.active

    tresult = MagicMock(); tresult.all.return_value = []
    cresult = MagicMock(); cresult.scalars.return_value = [cred_mock]

    with patch.object(scheduler_service, "_session_factory") as mock_sf:
        ms = AsyncMock()
        mock_sf.return_value.__aenter__.return_value = ms
        ms.execute = AsyncMock(side_effect=[tresult, cresult])
        items = await scheduler_service.list_schedules()

    assert len(items) == 1
    assert items[0]["kind"] == "keepalive"
    assert items[0]["registered"] is True
    assert items[0]["next_run_time"] is None
    assert items[0]["cron"] == "0 3 * * *"


@pytest.mark.asyncio
async def test_list_schedules_task_configured_but_not_registered(scheduler_service):
    from docupipe_manager.models.project import Project

    scheduler_service._scheduler.get_jobs = MagicMock(return_value=[])

    tid = uuid.uuid4()
    task_mock = MagicMock(spec=Task)
    task_mock.id = tid
    task_mock.slug = "drift"
    task_mock.name = "Drift Task"
    task_mock.schedule_enabled = True
    task_mock.schedule_cron = "0 5 * * *"
    proj_mock = MagicMock(spec=Project)
    proj_mock.id = uuid.uuid4()
    proj_mock.name = "Proj B"

    tresult = MagicMock(); tresult.all.return_value = [(task_mock, proj_mock)]
    cresult = MagicMock(); cresult.scalars.return_value = []

    with patch.object(scheduler_service, "_session_factory") as mock_sf:
        ms = AsyncMock()
        mock_sf.return_value.__aenter__.return_value = ms
        ms.execute = AsyncMock(side_effect=[tresult, cresult])
        items = await scheduler_service.list_schedules()

    assert len(items) == 1
    t = items[0]
    assert t["registered"] is False
    assert t["scheduler_job_id"] is None
    assert t["next_run_time"] is None
    assert t["cron"] == "0 5 * * *"
    assert t["config_enabled"] is True


@pytest.mark.asyncio
async def test_list_schedules_keepalive_disabled_skips_credentials(scheduler_service):
    scheduler_service._settings.credential_keepalive_enabled = False
    scheduler_service._scheduler.get_jobs = MagicMock(return_value=[])

    tresult = MagicMock(); tresult.all.return_value = []
    with patch.object(scheduler_service, "_session_factory") as mock_sf:
        ms = AsyncMock()
        mock_sf.return_value.__aenter__.return_value = ms
        ms.execute = AsyncMock(side_effect=[tresult])
        items = await scheduler_service.list_schedules()

    assert items == []
