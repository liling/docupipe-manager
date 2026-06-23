import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from docupipe_manager.models.task import CredentialType
from docupipe_manager.models.pipeline_run import RunStatus
from docupipe_manager.services.runner_service import RunnerService


@pytest.fixture
def runner_service():
    engine = MagicMock()
    settings = MagicMock()
    settings.max_concurrent_runs = 3
    settings.data_dir = "/tmp/docupipe-test"
    settings.dws_cli_path = "dws"
    settings.docupipe_python = "python"
    settings.run_log_max_bytes = 10 * 1024 * 1024
    settings.encryption_key = "0123456789abcdef0123456789abcdef"
    platform_client = MagicMock()
    platform_client.push_audit = AsyncMock()
    return RunnerService(engine, settings, platform_client)


@pytest.mark.asyncio
async def test_start_run_creates_run_with_task_id(runner_service):
    task_id = uuid.uuid4()
    with patch.object(runner_service, "_session_factory") as mock_sf:
        mock_session = AsyncMock()
        mock_sf.return_value.__aenter__.return_value = mock_session
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()
        with patch.object(runner_service, "_execute_run", new=AsyncMock()):
            run = await runner_service.start_run(
                task_id=task_id, trigger_type="manual", triggered_by=uuid.uuid4(),
            )
            assert run.status == RunStatus.pending
            assert run.task_id == task_id


@pytest.mark.asyncio
async def test_cancel_pending_run(runner_service):
    run_mock = MagicMock()
    run_mock.status = RunStatus.pending
    with patch.object(runner_service, "_session_factory") as mock_sf:
        mock_session = AsyncMock()
        mock_sf.return_value.__aenter__.return_value = mock_session
        mock_session.get = AsyncMock(return_value=run_mock)
        await runner_service.cancel_run(uuid.uuid4())
        assert run_mock.status == RunStatus.cancelled


@pytest.mark.asyncio
async def test_mark_run_failed(runner_service):
    with patch.object(runner_service, "_session_factory") as mock_sf:
        mock_session = AsyncMock()
        mock_sf.return_value.__aenter__.return_value = mock_session
        mock_session.execute = AsyncMock()
        mock_session.commit = AsyncMock()
        await runner_service._mark_run_failed(uuid.uuid4(), "err")
        mock_session.execute.assert_awaited_once()
