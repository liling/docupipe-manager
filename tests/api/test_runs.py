import asyncio
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import override_get_current_user, clear_overrides


def _detail_row(run, job, task):
    """_run_detail raw SQL row（16 列，匹配 runs.py 的 SELECT 顺序）。"""
    return (
        run.id, run.task_id, run.pipeline_name, run.mode,
        job.status, job.exit_code, job.command_text,
        job.started_at, job.completed_at, job.error_message,
        job.log_path, job.created_at, job.trigger_type, job.triggered_by,
        task.name, task.project_id,
    )


@pytest.mark.asyncio
async def test_list_runs_admin(async_client):
    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    with patch("docupipe_manager.deps.get_engine") as mock_get_engine:
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(side_effect=[
            MagicMock(scalar=MagicMock(return_value=0)),
            MagicMock(fetchall=MagicMock(return_value=[])),
        ])
        mock_engine = MagicMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_get_engine.return_value = mock_engine
        r = await async_client.get("/docupipe/api/runs")
        assert r.status_code == 200
        assert r.json()["total"] == 0
    clear_overrides()


@pytest.mark.asyncio
async def test_list_runs_non_admin_empty(async_client):
    uid = str(uuid.uuid4())
    override_get_current_user({"id": uid, "role": "user"})
    with patch("docupipe_manager.deps.get_engine") as mock_get_engine:
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(side_effect=[
            MagicMock(fetchall=MagicMock(return_value=[])),
        ])
        mock_engine = MagicMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_get_engine.return_value = mock_engine

        r = await async_client.get("/docupipe/api/runs")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 0
        assert data["runs"] == []
    clear_overrides()


@pytest.mark.asyncio
async def test_get_run_not_found(async_client):
    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    with patch("docupipe_manager.deps.get_engine") as mock_get_engine:
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value=MagicMock(one_or_none=MagicMock(return_value=None)))
        mock_engine = MagicMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_get_engine.return_value = mock_engine

        r = await async_client.get(f"/docupipe/api/runs/{uuid.uuid4()}")
        assert r.status_code == 404
    clear_overrides()


@pytest.mark.asyncio
async def test_cancel_run(async_client):
    run_id = uuid.uuid4()
    run_mock = MagicMock()
    run_mock.task_id = uuid.uuid4()
    job_mock = MagicMock()
    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    with (
        patch("docupipe_manager.deps.get_engine") as mock_get_engine,
        patch("docupipe_manager.deps.get_runner") as mock_get_runner,
    ):
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value=MagicMock(
            one_or_none=MagicMock(return_value=(run_mock, job_mock))
        ))
        mock_engine = MagicMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_get_engine.return_value = mock_engine
        mock_runner = MagicMock()
        mock_runner.cancel_run = AsyncMock(return_value=None)
        mock_get_runner.return_value = mock_runner

        r = await async_client.post(f"/docupipe/api/runs/{run_id}/cancel")
        assert r.status_code == 200
        assert r.json()["status"] == "cancelled"
    clear_overrides()


@pytest.mark.asyncio
async def test_get_run_includes_command_and_task_name(async_client):
    rid = uuid.uuid4()
    job_mock = MagicMock()
    job_mock.id = rid
    job_mock.command_text = "python -m docupipe run"
    job_mock.log_path = "/tmp/x.log"
    job_mock.exit_code = 0
    job_mock.status = "succeeded"
    job_mock.started_at = None
    job_mock.completed_at = None
    job_mock.error_message = None
    job_mock.trigger_type = "manual"
    job_mock.triggered_by = None
    job_mock.created_at = "2026-06-23"
    run_mock = MagicMock()
    run_mock.id = rid
    run_mock.task_id = uuid.uuid4()
    run_mock.pipeline_name = None
    run_mock.mode = "incremental"
    task_mock = MagicMock()
    task_mock.name = "demo-task"
    task_mock.project_id = uuid.uuid4()

    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    with patch("docupipe_manager.deps.get_engine") as mock_get_engine:
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(side_effect=[
            MagicMock(one_or_none=MagicMock(return_value=(run_mock, job_mock))),  # access
            MagicMock(one_or_none=MagicMock(return_value=_detail_row(run_mock, job_mock, task_mock))),  # detail
        ])
        mock_engine = MagicMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_get_engine.return_value = mock_engine

        r = await async_client.get(f"/docupipe/api/runs/{rid}")
        assert r.status_code == 200
        data = r.json()
        assert data["command_text"] == "python -m docupipe run"
        assert data["task_name"] == "demo-task"
        assert "project_id" in data
    clear_overrides()


@pytest.mark.asyncio
async def test_stream_completed_run_reads_file(async_client, tmp_path):
    rid = uuid.uuid4()
    log_file = tmp_path / "run.log"
    log_file.write_text("alpha\nbeta\n")

    run_mock = MagicMock()
    run_mock.id = rid
    run_mock.task_id = uuid.uuid4()
    run_mock.pipeline_name = None
    run_mock.mode = "incremental"
    job_mock = MagicMock()
    job_mock.id = rid
    job_mock.trigger_type = "manual"
    job_mock.triggered_by = None
    job_mock.status = "succeeded"
    job_mock.exit_code = 0
    job_mock.command_text = "cmd"
    job_mock.started_at = None
    job_mock.completed_at = None
    job_mock.log_path = str(log_file)
    job_mock.error_message = None
    job_mock.created_at = "2026-06-23"
    task_mock = MagicMock()
    task_mock.name = "t"
    task_mock.project_id = uuid.uuid4()

    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    with (
        patch("docupipe_manager.deps.get_engine") as mock_get_engine,
        patch("docupipe_manager.deps.get_runner") as mock_get_runner,
    ):
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(side_effect=[
            MagicMock(one_or_none=MagicMock(return_value=(run_mock, job_mock))),  # access
            MagicMock(one_or_none=MagicMock(return_value=_detail_row(run_mock, job_mock, task_mock))),  # detail(meta)
            MagicMock(one_or_none=MagicMock(return_value=_detail_row(run_mock, job_mock, task_mock))),  # detail(end)
        ])
        mock_engine = MagicMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_get_engine.return_value = mock_engine
        mock_runner = MagicMock()
        mock_runner.is_active = MagicMock(return_value=False)
        mock_get_runner.return_value = mock_runner

        r = await async_client.get(f"/docupipe/api/runs/{rid}/stream")
        assert r.status_code == 200
        text = r.text
        assert "event: meta" in text
        assert '"alpha"' in text and '"beta"' in text
        assert "event: end" in text
    clear_overrides()


@pytest.mark.asyncio
async def test_stream_active_run_replays_history_then_live_then_end(async_client):
    rid = uuid.uuid4()
    run_mock = MagicMock()
    run_mock.id = rid
    run_mock.task_id = uuid.uuid4()
    run_mock.pipeline_name = None
    run_mock.mode = "incremental"
    job_mock = MagicMock()
    job_mock.id = rid
    job_mock.trigger_type = "manual"
    job_mock.triggered_by = None
    job_mock.status = "running"
    job_mock.exit_code = None
    job_mock.command_text = "cmd"
    job_mock.started_at = None
    job_mock.completed_at = None
    job_mock.log_path = "/tmp/x.log"
    job_mock.error_message = None
    job_mock.created_at = "2026-06-23"
    task_mock = MagicMock()
    task_mock.name = "t"
    task_mock.project_id = uuid.uuid4()

    q: asyncio.Queue = asyncio.Queue()
    q.put_nowait("beta")
    q.put_nowait(None)

    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    with (
        patch("docupipe_manager.deps.get_engine") as mock_get_engine,
        patch("docupipe_manager.deps.get_runner") as mock_get_runner,
    ):
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(side_effect=[
            MagicMock(one_or_none=MagicMock(return_value=(run_mock, job_mock))),  # access
            MagicMock(one_or_none=MagicMock(return_value=_detail_row(run_mock, job_mock, task_mock))),  # detail(meta)
            MagicMock(one_or_none=MagicMock(return_value=_detail_row(run_mock, job_mock, task_mock))),  # detail(end)
        ])
        mock_engine = MagicMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_get_engine.return_value = mock_engine

        mock_runner = MagicMock()
        mock_runner.is_active = MagicMock(return_value=True)
        mock_runner.subscribe = MagicMock(return_value=(["alpha"], q))
        mock_runner.unsubscribe = MagicMock()
        mock_get_runner.return_value = mock_runner

        r = await async_client.get(f"/docupipe/api/runs/{rid}/stream")
        assert r.status_code == 200
        text = r.text
        assert "event: meta" in text
        assert '"alpha"' in text
        assert '"beta"' in text
        assert "event: end" in text
        mock_runner.unsubscribe.assert_called_once_with(rid, q)
    clear_overrides()


def test_run_detail_page_route_registered_and_template_exists():
    from docupipe_manager.main import app

    url = app.url_path_for("run_detail", run_id="abc")
    assert url == "/docupipe/runs/abc"

    template = Path(__file__).resolve().parents[2] / "docupipe_manager" / "templates" / "docupipe" / "runs" / "detail.html"
    assert template.is_file(), f"missing template: {template}"
    assert "{% extends \"base.html\" %}" in template.read_text(encoding="utf-8")
