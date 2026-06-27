import os
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from docupipe_manager.models.task import CredentialType
from docupipe_manager.models.job import JobStatus
from docupipe_manager.services.runner_service import RunnerService


def _empty_env_result():
    """session.execute(select(ProjectEnvVar)...) 的空结果 mock。"""
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    return result


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
            run, _ = await runner_service.start_run(
                task_id=task_id, trigger_type="manual", triggered_by=uuid.uuid4(),
            )
            assert run.task_id == task_id


@pytest.mark.asyncio
async def test_start_run_creates_job_and_pipeline_run(runner_service):
    """start_run 同时创建 Job(共享 id) 和 PipelineRun(job_id=run.id)，PipelineRun 无执行字段。"""
    from docupipe_manager.models.job import Job, JobKind, JobTriggerType
    from docupipe_manager.models.pipeline_run import PipelineRun
    task_id = uuid.uuid4()
    added = []
    with patch.object(runner_service, "_session_factory") as mock_sf:
        ms = AsyncMock(); ms.__aenter__.return_value = ms
        ms.add = MagicMock(side_effect=lambda obj: added.append(obj))
        ms.commit = AsyncMock(); ms.refresh = AsyncMock()
        mock_sf.return_value = ms
        with patch.object(runner_service, "_execute_run", new=AsyncMock()):
            run, _ = await runner_service.start_run(
                task_id=task_id, trigger_type="manual", triggered_by=uuid.uuid4(),
            )
    kinds = [type(a).__name__ for a in added]
    assert "PipelineRun" in kinds and "Job" in kinds
    job = next(a for a in added if isinstance(a, Job))
    assert job.id == run.id                       # 共享 id
    assert job.kind == JobKind.docupipe_run
    assert job.trigger_type.value == "manual"
    run_obj = next(a for a in added if isinstance(a, PipelineRun))
    assert run_obj.job_id == run.id
    # PipelineRun 无执行字段：不设 status/pid/exit_code/command_text 等
    assert not hasattr(PipelineRun, "status")


@pytest.mark.asyncio
async def test_finalize_run_preserves_job_log_path(runner_service):
    """_finalize_run 写入 Job 时不应清空 log_path（之前 _do_execute 已写入）。"""
    rid = uuid.uuid4()
    compiled_stmts = []
    with patch.object(runner_service, "_session_factory") as mock_sf:
        ms = AsyncMock(); ms.__aenter__ = AsyncMock(return_value=ms); ms.__aexit__ = AsyncMock(return_value=None)
        ms.commit = AsyncMock()
        async def fake_exec(stmt, *a, **kw):
            compiled_stmts.append(str(stmt.compile()))
            return None
        ms.execute = fake_exec
        mock_sf.return_value = ms
        await runner_service._finalize_run(rid, 0, None, uuid.uuid4())
    job_updates = [c for c in compiled_stmts if "UPDATE" in c and ".jobs" in c]
    assert job_updates, "expected a Job update in _finalize_run"
    for compiled in job_updates:
        assert "log_path" not in compiled, f"_finalize_run clobbered Job.log_path: {compiled}"
    # PipelineRun update 应不存在（已移除）
    pr_updates = [c for c in compiled_stmts if "UPDATE" in c and ".pipeline_runs" in c]
    assert not pr_updates, f"_finalize_run should not update PipelineRun: {pr_updates}"


@pytest.mark.asyncio
async def test_cancel_pending_run(runner_service):
    job_mock = MagicMock()
    job_mock.status = JobStatus.pending
    with patch.object(runner_service, "_session_factory") as mock_sf:
        mock_session = AsyncMock()
        mock_sf.return_value.__aenter__.return_value = mock_session
        mock_session.get = AsyncMock(return_value=job_mock)
        await runner_service.cancel_run(uuid.uuid4())
        assert job_mock.status == JobStatus.cancelled


@pytest.mark.asyncio
async def test_mark_run_failed(runner_service):
    with patch.object(runner_service, "_session_factory") as mock_sf:
        mock_session = AsyncMock()
        mock_sf.return_value.__aenter__.return_value = mock_session
        mock_session.execute = AsyncMock()
        mock_session.commit = AsyncMock()
        await runner_service._mark_run_failed(uuid.uuid4(), "err")
        # Task 4: only Job update (no PipelineRun dual-write)
        assert mock_session.execute.await_count == 1


@pytest.mark.asyncio
async def test_subscribe_returns_buffer_then_live(runner_service):
    rid = uuid.uuid4()
    runner_service._active_runs.add(rid)
    runner_service._broadcast(rid, "old line 1")
    runner_service._broadcast(rid, "old line 2")
    history, queue = runner_service.subscribe(rid)
    assert history == ["old line 1", "old line 2"]
    runner_service._broadcast(rid, "live line")
    assert queue.get_nowait() == "live line"


@pytest.mark.asyncio
async def test_unsubscribe_stops_broadcast(runner_service):
    rid = uuid.uuid4()
    runner_service._active_runs.add(rid)
    history, queue = runner_service.subscribe(rid)
    runner_service.unsubscribe(rid, queue)
    runner_service._broadcast(rid, "after")
    assert queue.empty()
    assert rid not in runner_service._subscribers


def test_broadcast_drops_old_lines_beyond_maxlen(runner_service):
    rid = uuid.uuid4()
    for i in range(2500):
        runner_service._broadcast(rid, f"line {i}")
    history, _ = runner_service.subscribe(rid)
    assert len(history) == 2000
    assert history[0] == "line 500"
    assert history[-1] == "line 2499"


@pytest.mark.asyncio
async def test_subscribe_after_run_ended_gets_sentinel(runner_service):
    rid = uuid.uuid4()
    runner_service._broadcast(rid, "seeded line")
    history, queue = runner_service.subscribe(rid)
    assert history == ["seeded line"]
    assert queue.get_nowait() is None


@pytest.mark.asyncio
async def test_close_subscribers_sends_sentinel_and_cleans(runner_service):
    rid = uuid.uuid4()
    runner_service._active_runs.add(rid)
    _, queue = runner_service.subscribe(rid)
    runner_service._broadcast(rid, "x")
    await runner_service._close_subscribers(rid)
    assert queue.get_nowait() == "x"
    assert queue.get_nowait() is None
    assert rid not in runner_service._subscribers
    assert rid not in runner_service._log_buffers


@pytest.mark.asyncio
async def test_execute_run_marks_active_and_closes_on_success(runner_service):
    rid = uuid.uuid4()
    runner_service._do_execute = AsyncMock()
    runner_service._active_runs.add(rid)
    _, queue = runner_service.subscribe(rid)
    await runner_service._execute_run(rid)
    assert not runner_service.is_active(rid)
    assert queue.get_nowait() is None


@pytest.mark.asyncio
async def test_execute_run_closes_subscribers_on_exception(runner_service):
    rid = uuid.uuid4()
    runner_service._do_execute = AsyncMock(side_effect=RuntimeError("boom"))
    runner_service._mark_run_failed = AsyncMock()
    runner_service._active_runs.add(rid)
    _, queue = runner_service.subscribe(rid)
    await runner_service._execute_run(rid)
    assert queue.get_nowait() is None
    assert not runner_service.is_active(rid)


@pytest.mark.asyncio
async def test_do_execute_flushes_and_broadcasts_each_line(runner_service, tmp_path):
    """端到端 mock：验证写文件 flush、广播、command_text 持久化。"""
    rid = uuid.uuid4()
    runner_service._active_runs.add(rid)
    task_id = uuid.uuid4()

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

    # _do_execute opens: session0=load_context, session1=running update, session2=pid update, session3=finalize
    sessions = [MagicMock(), MagicMock(), MagicMock(), MagicMock()]
    idx = {"i": 0}

    def fake_factory():
        s = sessions[idx["i"]]
        idx["i"] += 1
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=s)
        cm.__aexit__ = AsyncMock(return_value=None)
        return cm

    sessions[0].get = AsyncMock(side_effect=[run_mock, task_mock, cred_mock])
    sessions[0].execute = AsyncMock(return_value=_empty_env_result())
    sessions[1].execute = AsyncMock()
    sessions[1].commit = AsyncMock()
    sessions[2].execute = AsyncMock()
    sessions[2].commit = AsyncMock()
    sessions[3].execute = AsyncMock()
    sessions[3].commit = AsyncMock()
    runner_service._session_factory = fake_factory
    runner_service._settings.data_dir = str(tmp_path)

    with patch("docupipe_manager.services.runner_service.decrypt_sm4", return_value="auth"), \
         patch("asyncio.create_subprocess_exec", new=AsyncMock()) as mock_sub:
        proc1 = MagicMock()
        proc1.communicate = AsyncMock(return_value=(b"", b""))
        proc2 = MagicMock()
        proc2.stdout.readline = AsyncMock(side_effect=[b"line A\n", b"line B\n", b""])
        proc2.wait = AsyncMock(return_value=0)
        proc2.pid = 12345
        mock_sub.side_effect = [proc1, proc2]

        _, queue = runner_service.subscribe(rid)
        await runner_service._do_execute(rid)

    assert queue.get_nowait() == "line A"
    assert queue.get_nowait() == "line B"
    update_call = sessions[1].execute.call_args[0][0]
    compiled = update_call.compile()
    assert "command_text" in str(compiled)


@pytest.mark.asyncio
async def test_do_execute_truncates_log_file_at_max_bytes(runner_service, tmp_path):
    rid = uuid.uuid4()
    runner_service._active_runs.add(rid)
    task_id = uuid.uuid4()

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

    sessions = [MagicMock(), MagicMock(), MagicMock(), MagicMock()]
    idx = {"i": 0}

    def fake_factory():
        s = sessions[idx["i"]]
        idx["i"] += 1
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=s)
        cm.__aexit__ = AsyncMock(return_value=None)
        return cm

    sessions[0].get = AsyncMock(side_effect=[run_mock, task_mock, cred_mock])
    sessions[0].execute = AsyncMock(return_value=_empty_env_result())
    sessions[1].execute = AsyncMock()
    sessions[1].commit = AsyncMock()
    sessions[2].execute = AsyncMock()
    sessions[2].commit = AsyncMock()
    sessions[3].execute = AsyncMock()
    sessions[3].commit = AsyncMock()
    runner_service._session_factory = fake_factory
    runner_service._settings.data_dir = str(tmp_path / "data")
    runner_service._settings.run_log_max_bytes = 64

    lines = [b"this-is-a-long-line-XX\n"] * 20

    with patch("docupipe_manager.services.runner_service.decrypt_sm4", return_value="auth"), \
         patch("asyncio.create_subprocess_exec", new=AsyncMock()) as mock_sub:
        proc1 = MagicMock()
        proc1.communicate = AsyncMock(return_value=(b"", b""))
        proc2 = MagicMock()
        proc2.stdout.readline = AsyncMock(side_effect=lines + [b""])
        proc2.wait = AsyncMock(return_value=0)
        proc2.pid = 12345
        mock_sub.side_effect = [proc1, proc2]

        _, queue = runner_service.subscribe(rid)
        await runner_service._do_execute(rid)

    broadcast_count = 0
    while not queue.empty():
        line = queue.get_nowait()
        assert line is not None
        broadcast_count += 1
    assert broadcast_count == 20

    log_path = os.path.join(str(tmp_path / "data"), "tasks", str(task_id), "runs", f"{rid}.log")
    with open(log_path, "rb") as f:
        content = f.read()
    assert len(content) <= 64


@pytest.mark.asyncio
async def test_do_execute_runs_without_credential(runner_service, tmp_path):
    rid = uuid.uuid4()
    runner_service._active_runs.add(rid)
    task_id = uuid.uuid4()

    run_mock = MagicMock()
    run_mock.id = rid
    run_mock.task_id = task_id
    run_mock.mode = "incremental"
    run_mock.pipeline_name = None
    task_mock = MagicMock()
    task_mock.id = task_id
    task_mock.credential_id = None
    task_mock.credential_type = None
    task_mock.config_yaml = "k: v"
    task_mock.slug = "demo"

    sessions = [MagicMock(), MagicMock(), MagicMock(), MagicMock()]
    idx = {"i": 0}

    def fake_factory():
        s = sessions[idx["i"]]
        idx["i"] += 1
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=s)
        cm.__aexit__ = AsyncMock(return_value=None)
        return cm

    sessions[0].get = AsyncMock(side_effect=[run_mock, task_mock])
    sessions[0].execute = AsyncMock(return_value=_empty_env_result())
    sessions[1].execute = AsyncMock()
    sessions[1].commit = AsyncMock()
    sessions[2].execute = AsyncMock()
    sessions[2].commit = AsyncMock()
    sessions[3].execute = AsyncMock()
    sessions[3].commit = AsyncMock()
    runner_service._session_factory = fake_factory
    runner_service._settings.data_dir = str(tmp_path)

    with patch("asyncio.create_subprocess_exec", new=AsyncMock()) as mock_sub:
        proc = MagicMock()
        proc.stdout.readline = AsyncMock(side_effect=[b"hello\n", b""])
        proc.wait = AsyncMock(return_value=0)
        proc.pid = 999
        mock_sub.side_effect = [proc]

        _, queue = runner_service.subscribe(rid)
        await runner_service._do_execute(rid)

    assert mock_sub.call_count == 1
    assert queue.get_nowait() == "hello"
    update_call = sessions[1].execute.call_args[0][0]
    assert "command_text" in str(update_call.compile())
    args = list(mock_sub.call_args_list[0][0])
    run_idx = args.index("run")
    assert "--state-dir" in args[:run_idx]
    assert "--log-level" in args[:run_idx]


@pytest.mark.asyncio
async def test_do_execute_injects_project_env_into_subprocess(runner_service, tmp_path):
    rid = uuid.uuid4()
    runner_service._active_runs.add(rid)
    task_id = uuid.uuid4()
    project_id = uuid.uuid4()

    run_mock = MagicMock()
    run_mock.id = rid
    run_mock.task_id = task_id
    run_mock.mode = "incremental"
    run_mock.pipeline_name = None
    task_mock = MagicMock()
    task_mock.id = task_id
    task_mock.project_id = project_id
    task_mock.credential_id = None
    task_mock.credential_type = None
    task_mock.config_yaml = "k: v"
    task_mock.slug = "demo"

    from docupipe_manager.crypto import encrypt_sm4
    plain = MagicMock()
    plain.is_secret = False
    plain.key = "MY_PLAIN"
    plain.value = "hello"
    secret = MagicMock()
    secret.is_secret = True
    secret.key = "MY_SECRET"
    secret.value = encrypt_sm4("topsecret", runner_service._settings.encryption_key)

    env_result = MagicMock()
    env_result.scalars.return_value.all.return_value = [plain, secret]

    sessions = [MagicMock(), MagicMock(), MagicMock(), MagicMock()]
    idx = {"i": 0}

    def fake_factory():
        s = sessions[idx["i"]]
        idx["i"] += 1
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=s)
        cm.__aexit__ = AsyncMock(return_value=None)
        return cm

    sessions[0].get = AsyncMock(side_effect=[run_mock, task_mock])
    sessions[0].execute = AsyncMock(return_value=env_result)
    sessions[1].execute = AsyncMock()
    sessions[1].commit = AsyncMock()
    sessions[2].execute = AsyncMock()
    sessions[2].commit = AsyncMock()
    sessions[3].execute = AsyncMock()
    sessions[3].commit = AsyncMock()
    runner_service._session_factory = fake_factory
    runner_service._settings.data_dir = str(tmp_path)

    with patch("asyncio.create_subprocess_exec", new=AsyncMock()) as mock_sub:
        proc = MagicMock()
        proc.stdout.readline = AsyncMock(side_effect=[b"ok\n", b""])
        proc.wait = AsyncMock(return_value=0)
        proc.pid = 999
        mock_sub.side_effect = [proc]

        await runner_service._do_execute(rid)

    assert mock_sub.call_count == 1
    env_passed = mock_sub.call_args.kwargs["env"]
    assert env_passed["DWS_DISABLE_KEYCHAIN"] == "1"
    assert env_passed["MY_PLAIN"] == "hello"
    assert env_passed["MY_SECRET"] == "topsecret"


@pytest.mark.asyncio
async def test_do_execute_shares_isolated_env_and_no_logout(runner_service, tmp_path):
    """import 与 docupipe 子进程共享同一隔离 HOME；不再调 auth logout。"""
    rid = uuid.uuid4()
    runner_service._active_runs.add(rid)
    task_id = uuid.uuid4()

    run_mock = MagicMock(); run_mock.id = rid; run_mock.task_id = task_id
    run_mock.mode = "incremental"; run_mock.pipeline_name = None
    task_mock = MagicMock(); task_mock.id = task_id
    task_mock.credential_id = uuid.uuid4()
    task_mock.credential_type = CredentialType.dws
    task_mock.config_yaml = "k: v"; task_mock.slug = "demo"
    cred_mock = MagicMock(); cred_mock.auth_blob.hex = MagicMock(return_value="00" * 16)

    sessions = [MagicMock(), MagicMock(), MagicMock(), MagicMock()]
    idx = {"i": 0}
    def fake_factory():
        s = sessions[idx["i"]]; idx["i"] += 1
        cm = MagicMock(); cm.__aenter__ = AsyncMock(return_value=s); cm.__aexit__ = AsyncMock(return_value=None)
        return cm
    sessions[0].get = AsyncMock(side_effect=[run_mock, task_mock, cred_mock])
    sessions[0].execute = AsyncMock(return_value=_empty_env_result())
    sessions[1].execute = AsyncMock(); sessions[1].commit = AsyncMock()
    sessions[2].execute = AsyncMock(); sessions[2].commit = AsyncMock()
    sessions[3].execute = AsyncMock(); sessions[3].commit = AsyncMock()
    runner_service._session_factory = fake_factory
    runner_service._settings.data_dir = str(tmp_path)

    with patch("docupipe_manager.services.runner_service.decrypt_sm4", return_value="auth"), \
         patch("asyncio.create_subprocess_exec", new=AsyncMock()) as mock_sub:
        import_proc = MagicMock(); import_proc.communicate = AsyncMock(return_value=(b"", b""))
        docupipe_proc = MagicMock()
        docupipe_proc.stdout.readline = AsyncMock(side_effect=[b"hi\n", b""])
        docupipe_proc.wait = AsyncMock(return_value=0); docupipe_proc.pid = 1
        mock_sub.side_effect = [import_proc, docupipe_proc]

        await runner_service._do_execute(rid)

    # 只有 import + docupipe 两个子进程，没有 logout
    all_args = [list(c[0]) for c in mock_sub.call_args_list]
    assert not any("logout" in a for a in all_args)
    envs = [c.kwargs.get("env") for c in mock_sub.call_args_list]
    homes = {e["HOME"] for e in envs}
    assert len(homes) == 1                       # 共享同一隔离 HOME
    assert envs[0]["DWS_DISABLE_KEYCHAIN"] == "1"
    assert next(iter(homes)) != os.environ.get("HOME")
