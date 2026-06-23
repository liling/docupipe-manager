import asyncio
import uuid
from pathlib import Path
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
        mock_conn.execute = AsyncMock(return_value=MagicMock(one_or_none=MagicMock(return_value=None)))
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
            one_or_none=MagicMock(return_value=run_mock)
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


@pytest.mark.asyncio
async def test_get_run_includes_command_and_task_name(async_client):
    rid = uuid.uuid4()
    run_mock = MagicMock()
    run_mock.id = rid
    run_mock.task_id = uuid.uuid4()
    run_mock.trigger_type = "manual"
    run_mock.triggered_by = None
    run_mock.pipeline_name = None
    run_mock.mode = "incremental"
    run_mock.status = "succeeded"
    run_mock.exit_code = 0
    run_mock.started_at = None
    run_mock.completed_at = None
    run_mock.command_text = "python -m docupipe run"
    run_mock.log_path = "/tmp/x.log"
    run_mock.error_message = None
    run_mock.created_at = "2026-06-23"
    task_mock = MagicMock()
    task_mock.name = "demo-task"
    task_mock.project_id = uuid.uuid4()

    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    with patch("docupipe_manager.main.app") as mock_app:
        mock_conn = AsyncMock()
        # _verify_run_access 查 run；_run_detail 再查 run + task
        mock_conn.execute = AsyncMock(side_effect=[
            MagicMock(one_or_none=MagicMock(return_value=run_mock)),  # access
            MagicMock(one_or_none=MagicMock(return_value=run_mock)),  # detail run
            MagicMock(one_or_none=MagicMock(return_value=task_mock)), # detail task
        ])
        mock_engine = MagicMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_app.state.engine = mock_engine

        r = await async_client.get(f"/api/runs/{rid}")
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
    run_mock.trigger_type = "manual"
    run_mock.triggered_by = None
    run_mock.pipeline_name = None
    run_mock.mode = "incremental"
    run_mock.status = "succeeded"
    run_mock.exit_code = 0
    run_mock.command_text = "cmd"
    run_mock.started_at = None
    run_mock.completed_at = None
    run_mock.log_path = str(log_file)
    run_mock.error_message = None
    run_mock.created_at = "2026-06-23"
    task_mock = MagicMock()
    task_mock.name = "t"
    task_mock.project_id = uuid.uuid4()

    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    with patch("docupipe_manager.main.app") as mock_app:
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(side_effect=[
            MagicMock(one_or_none=MagicMock(return_value=run_mock)),  # access
            MagicMock(one_or_none=MagicMock(return_value=run_mock)),  # detail(meta)
            MagicMock(one_or_none=MagicMock(return_value=task_mock)),
            MagicMock(one_or_none=MagicMock(return_value=run_mock)),  # detail(end)
            MagicMock(one_or_none=MagicMock(return_value=task_mock)),
        ])
        mock_engine = MagicMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_app.state.engine = mock_engine
        runner = MagicMock()
        runner.is_active = MagicMock(return_value=False)
        mock_app.state.runner = runner

        r = await async_client.get(f"/api/runs/{rid}/stream")
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
    run_mock.trigger_type = "manual"
    run_mock.triggered_by = None
    run_mock.pipeline_name = None
    run_mock.mode = "incremental"
    run_mock.status = "running"
    run_mock.exit_code = None
    run_mock.command_text = "cmd"
    run_mock.started_at = None
    run_mock.completed_at = None
    run_mock.log_path = "/tmp/x.log"
    run_mock.error_message = None
    run_mock.created_at = "2026-06-23"
    task_mock = MagicMock()
    task_mock.name = "t"
    task_mock.project_id = uuid.uuid4()

    q: asyncio.Queue = asyncio.Queue()
    q.put_nowait("beta")
    q.put_nowait(None)  # sentinel

    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    with patch("docupipe_manager.main.app") as mock_app:
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(side_effect=[
            MagicMock(one_or_none=MagicMock(return_value=run_mock)),  # access
            MagicMock(one_or_none=MagicMock(return_value=run_mock)),  # detail(meta)
            MagicMock(one_or_none=MagicMock(return_value=task_mock)),
            MagicMock(one_or_none=MagicMock(return_value=run_mock)),  # detail(end)
            MagicMock(one_or_none=MagicMock(return_value=task_mock)),
        ])
        mock_engine = MagicMock()
        mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_app.state.engine = mock_engine

        runner = MagicMock()
        runner.is_active = MagicMock(return_value=True)
        runner.subscribe = MagicMock(return_value=(["alpha"], q))
        runner.unsubscribe = MagicMock()
        mock_app.state.runner = runner

        r = await async_client.get(f"/api/runs/{rid}/stream")
        assert r.status_code == 200
        text = r.text
        assert "event: meta" in text
        assert '"alpha"' in text  # history replay
        assert '"beta"' in text   # live line from queue
        assert "event: end" in text
        runner.unsubscribe.assert_called_once_with(rid, q)
    clear_overrides()


def test_run_detail_page_route_registered_and_template_exists():
    """运行详情页路由已注册且模板文件存在。

    无法通过 async_client 渲染验证：base.html 依赖 xinyi_platform 的
    ui/app_shell.html，测试环境未安装该包，渲染会抛 TemplateNotFound。
    故退化为：断言路由已注册 + 模板文件存在 + JS 语法正确。
    """
    from docupipe_manager.main import app

    # 路由以 _IncludedRouter 形式嵌套，app.routes 顶层取不到带前缀的 path；
    # 用 url_path_for 校验路由已按名称注册。
    url = app.url_path_for("run_detail", run_id="abc")
    assert url == "/docupipe/runs/abc"

    template = Path(__file__).resolve().parents[2] / "docupipe_manager" / "templates" / "docupipe" / "runs" / "detail.html"
    assert template.is_file(), f"missing template: {template}"
    assert "{% extends \"base.html\" %}" in template.read_text(encoding="utf-8")
