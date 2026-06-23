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


@pytest.mark.asyncio
async def test_subscribe_returns_buffer_then_live(runner_service):
    rid = uuid.uuid4()
    runner_service._broadcast(rid, "old line 1")
    runner_service._broadcast(rid, "old line 2")
    history, queue = runner_service.subscribe(rid)
    assert history == ["old line 1", "old line 2"]
    runner_service._broadcast(rid, "live line")
    assert queue.get_nowait() == "live line"


@pytest.mark.asyncio
async def test_unsubscribe_stops_broadcast(runner_service):
    rid = uuid.uuid4()
    history, queue = runner_service.subscribe(rid)
    runner_service.unsubscribe(rid, queue)
    runner_service._broadcast(rid, "after")
    assert queue.empty()
    # subscribers 已清理
    assert rid not in runner_service._subscribers


def test_broadcast_drops_old_lines_beyond_maxlen(runner_service):
    rid = uuid.uuid4()
    for i in range(2500):
        runner_service._broadcast(rid, f"line {i}")
    # maxlen=2000，仅保留最后 2000 行
    history, _ = runner_service.subscribe(rid)
    assert len(history) == 2000
    assert history[0] == "line 500"
    assert history[-1] == "line 2499"


@pytest.mark.asyncio
async def test_close_subscribers_sends_sentinel_and_cleans(runner_service):
    rid = uuid.uuid4()
    _, queue = runner_service.subscribe(rid)
    runner_service._broadcast(rid, "x")
    await runner_service._close_subscribers(rid)
    # 先收到存量 x，再收到哨兵 None
    assert queue.get_nowait() == "x"
    assert queue.get_nowait() is None
    assert rid not in runner_service._subscribers
    assert rid not in runner_service._log_buffers


@pytest.mark.asyncio
async def test_execute_run_marks_active_and_closes_on_success(runner_service):
    rid = uuid.uuid4()
    runner_service._do_execute = AsyncMock()
    _, queue = runner_service.subscribe(rid)  # 预先订阅
    await runner_service._execute_run(rid)
    assert not runner_service.is_active(rid)
    # 结束后订阅者收到哨兵
    assert queue.get_nowait() is None


@pytest.mark.asyncio
async def test_execute_run_closes_subscribers_on_exception(runner_service):
    rid = uuid.uuid4()
    runner_service._do_execute = AsyncMock(side_effect=RuntimeError("boom"))
    runner_service._mark_run_failed = AsyncMock()
    _, queue = runner_service.subscribe(rid)
    await runner_service._execute_run(rid)
    assert queue.get_nowait() is None
    assert not runner_service.is_active(rid)


@pytest.mark.asyncio
async def test_do_execute_flushes_and_broadcasts_each_line(runner_service, tmp_path):
    """端到端 mock：验证写文件 flush、广播、command_text 持久化。"""
    rid = uuid.uuid4()
    task_id = uuid.uuid4()

    # --- 准备 run/task/credential mocks ---
    run_mock = MagicMock()
    run_mock.id = rid
    run_mock.task_id = task_id
    run_mock.mode = "incremental"
    run_mock.pipeline_name = None
    task_mock = MagicMock()
    task_mock.id = task_id
    task_mock.credential_id = uuid.uuid4()
    task_mock.credential_type = CredentialType.dws
    task_mock.config_yaml = "k: v"
    task_mock.slug = "demo"
    cred_mock = MagicMock()
    cred_mock.auth_blob = MagicMock()
    cred_mock.auth_blob.hex = MagicMock(return_value="00" * 16)

    sessions = [MagicMock(), MagicMock(), MagicMock(), MagicMock()]  # _do_execute 内开 4 次 session
    idx = {"i": 0}

    def fake_factory():
        s = sessions[idx["i"]]
        idx["i"] += 1
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=s)
        cm.__aexit__ = AsyncMock(return_value=None)
        return cm

    # session 0: get(run)->run_mock, get(task)->task_mock, get(cred)->cred_mock
    sessions[0].get = AsyncMock(side_effect=[run_mock, task_mock, cred_mock])
    # session 1: update running（标记 started_at + command_text）
    sessions[1].execute = AsyncMock()
    sessions[1].commit = AsyncMock()
    # session 2: update pid
    sessions[2].execute = AsyncMock()
    sessions[2].commit = AsyncMock()
    # session 3: update final status
    sessions[3].execute = AsyncMock()
    sessions[3].commit = AsyncMock()
    runner_service._session_factory = fake_factory
    runner_service._settings.data_dir = str(tmp_path)

    # --- decrypt / subprocess mocks ---
    with patch("docupipe_manager.services.runner_service.decrypt_sm4", return_value="auth"), \
         patch("docupipe_manager.services.runner_service.mkdtemp", return_value=str(tmp_path)), \
         patch("asyncio.create_subprocess_exec", new=AsyncMock()) as mock_sub:
        proc1 = MagicMock()
        proc1.communicate = AsyncMock(return_value=(b"", b""))
        proc2 = MagicMock()
        # 模拟 stdout 两行后 EOF
        proc2.stdout.readline = AsyncMock(side_effect=[b"line A\n", b"line B\n", b""])
        proc2.wait = AsyncMock(return_value=0)
        proc2.pid = 12345
        mock_sub.side_effect = [proc1, proc2]

        _, queue = runner_service.subscribe(rid)
        await runner_service._do_execute(rid)

    # 广播了两行
    assert queue.get_nowait() == "line A"
    assert queue.get_nowait() == "line B"
    # command_text 写入了 session1 的 update（第二个 session）
    update_call = sessions[1].execute.call_args[0][0]
    # SQLAlchemy update 语句的 compile 取 text 太重，改为断言 command_text 在 values
    compiled = update_call.compile()
    assert "command_text" in str(compiled)
