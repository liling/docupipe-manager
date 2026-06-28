import asyncio
import logging
import os
import shlex
import signal
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from docupipe_manager.config import Settings
from docupipe_manager.crypto import decrypt_sm4
from docupipe_manager.models.dws_credential import DwsCredential
from docupipe_manager.models.job import Job, JobKind, JobStatus, JobTriggerType
from docupipe_manager.models.pipeline_run import PipelineRun
from docupipe_manager.models.project_env_var import ProjectEnvVar
from docupipe_manager.models.task import CredentialType, Task
from docupipe_manager.platform.client import XinyiPlatformClient
from docupipe_manager.services.dws_env import isolated_dws_env

logger = logging.getLogger(__name__)


@dataclass
class _RunContext:
    task: Task
    credential: DwsCredential | None
    slug: str
    mode: str
    pipeline_name: str | None
    config_yaml: str
    project_env: dict[str, str] = field(default_factory=dict)


class RunnerService:
    """Manage docupipe subprocess execution."""

    def __init__(self, engine: AsyncEngine, settings: Settings, platform_client: XinyiPlatformClient):
        self._engine = engine
        self._settings = settings
        self._platform_client = platform_client
        self._session_factory = async_sessionmaker(engine, expire_on_commit=False)
        self._semaphore = asyncio.Semaphore(settings.max_concurrent_runs)
        self._log_buffers: dict[uuid.UUID, "deque[str]"] = {}
        self._subscribers: dict[uuid.UUID, set[asyncio.Queue]] = {}
        self._active_runs: set[uuid.UUID] = set()

    def is_active(self, run_id: uuid.UUID) -> bool:
        return run_id in self._active_runs

    def subscribe(self, run_id: uuid.UUID) -> tuple[list[str], asyncio.Queue]:
        buffer = self._log_buffers.get(run_id)
        history = list(buffer) if buffer else []
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.setdefault(run_id, set()).add(queue)
        if run_id not in self._active_runs:
            queue.put_nowait(None)
        return history, queue

    def unsubscribe(self, run_id: uuid.UUID, queue: asyncio.Queue) -> None:
        subs = self._subscribers.get(run_id)
        if subs and queue in subs:
            subs.discard(queue)
            if not subs:
                self._subscribers.pop(run_id, None)

    def _broadcast(self, run_id: uuid.UUID, line: str) -> None:
        buffer = self._log_buffers.get(run_id)
        if buffer is None:
            buffer = deque(maxlen=2000)
            self._log_buffers[run_id] = buffer
        buffer.append(line)
        for q in list(self._subscribers.get(run_id, ())):
            q.put_nowait(line)

    async def _close_subscribers(self, run_id: uuid.UUID) -> None:
        for q in list(self._subscribers.get(run_id, ())):
            q.put_nowait(None)
        self._subscribers.pop(run_id, None)
        self._log_buffers.pop(run_id, None)

    async def start_run(
        self,
        task_id: uuid.UUID,
        trigger_type: str,
        triggered_by: uuid.UUID | None,
        pipeline_name: str | None = None,
        mode: str = "incremental",
    ) -> tuple[PipelineRun, Job]:
        run_id = uuid.uuid4()
        job = Job(
            id=run_id,
            kind=JobKind.docupipe_run,
            status=JobStatus.pending,
            trigger_type=JobTriggerType(trigger_type),
            triggered_by=triggered_by,
            command_text=None,
        )
        run = PipelineRun(
            id=run_id,
            job_id=run_id,
            task_id=task_id,
            pipeline_name=pipeline_name,
            mode=mode,
        )
        async with self._session_factory() as session:
            session.add(job)
            session.add(run)
            await session.commit()
            await session.refresh(run)

        asyncio.create_task(self._execute_run(run.id))
        return run, job

    async def cancel_run(self, run_id: uuid.UUID) -> None:
        async with self._session_factory() as session:
            job = await session.get(Job, run_id)
            if job is None:
                raise ValueError("Run not found")
            if job.status == JobStatus.pending:
                job.status = JobStatus.cancelled
                await session.commit()
            elif job.status == JobStatus.running and job.pid:
                try:
                    os.kill(job.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                job.status = JobStatus.cancelled
                job.pid = None
                await session.commit()

    async def _execute_run(self, run_id: uuid.UUID) -> None:
        self._active_runs.add(run_id)
        async with self._semaphore:
            try:
                await self._do_execute(run_id)
            except asyncio.CancelledError:
                logger.info("Run %s cancelled during shutdown", run_id)
                await self._mark_run_failed(run_id, "server shutdown")
                raise
            except Exception as e:
                logger.error("Run %s failed: %s", run_id, e)
                await self._mark_run_failed(run_id, str(e))
            finally:
                self._active_runs.discard(run_id)
                await self._close_subscribers(run_id)

    async def _load_context(self, session, run) -> _RunContext | None:
        run_id = run.id
        task = await session.get(Task, run.task_id)
        if task is None:
            await self._mark_run_failed(run_id, "Task not found")
            return None
        credential = None
        if task.credential_id is not None and task.credential_type is not None:
            if task.credential_type == CredentialType.dws:
                credential = await session.get(DwsCredential, task.credential_id)
            else:
                await self._mark_run_failed(run_id, f"Unsupported credential type: {task.credential_type}")
                return None
            if credential is None:
                await self._mark_run_failed(run_id, "Credential not found")
                return None

        env_var_rows = (await session.execute(
            select(ProjectEnvVar).where(ProjectEnvVar.project_id == task.project_id)
        )).scalars().all()

        project_env: dict[str, str] = {}
        for ev in env_var_rows:
            if ev.is_secret:
                try:
                    ev_value = decrypt_sm4(ev.value, self._settings.encryption_key)
                except Exception:
                    await self._mark_run_failed(run_id, f"环境变量 {ev.key} 解密失败")
                    return None
            else:
                ev_value = ev.value
            project_env[ev.key] = ev_value

        return _RunContext(
            task=task,
            credential=credential,
            slug=task.slug,
            mode=run.mode,
            pipeline_name=run.pipeline_name,
            config_yaml=task.config_yaml,
            project_env=project_env,
        )

    def _write_config(self, project_dir: str, config_yaml: str) -> str:
        config_path = os.path.join(project_dir, "config.yaml")
        with open(config_path, "w") as f:
            f.write(config_yaml)
        return config_path

    def _build_command(self, ctx: _RunContext, config_path: str, state_dir: str) -> tuple[list[str], str]:
        cmd = [
            self._settings.docupipe_python, "-m", "docupipe",
            "--state-dir", state_dir,
            "--log-level", "INFO",
            "run",
            "--config", config_path,
            "--mode", ctx.mode,
        ]
        if ctx.pipeline_name:
            cmd.extend(["--pipeline", ctx.pipeline_name])
        command_text = " ".join(shlex.quote(c) for c in cmd)
        return cmd, command_text

    async def _import_credential(self, ctx: _RunContext, env: dict[str, str]) -> None:
        key_hex = self._settings.encryption_key
        auth_b64 = decrypt_sm4(ctx.credential.auth_blob.hex(), key_hex)

        import_path = os.path.join(env["HOME"], "auth.b64")
        with open(import_path, "w") as f:
            f.write(auth_b64)

        import_proc = await asyncio.create_subprocess_exec(
            self._settings.dws_cli_path, "auth", "import", "--base64", "-i", import_path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
        )
        await import_proc.communicate()

    async def _stream_subprocess(
        self, cmd: list[str], env: dict[str, str],
        project_dir: str, log_path: str, run_id: uuid.UUID,
    ) -> tuple[int, str | None]:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            env=env,
            cwd=project_dir,
        )
        async with self._session_factory() as session:
            await session.execute(
                update(Job).where(Job.id == run_id).values(
                    pid=proc.pid, status=JobStatus.running, started_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()

        max_bytes = self._settings.run_log_max_bytes
        with open(log_path, "w") as log_file:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace")
                log_file.write(text)
                log_file.flush()
                if log_file.tell() > max_bytes:
                    log_file.truncate(max_bytes // 2)
                    log_file.seek(0, 2)
                self._broadcast(run_id, text.rstrip("\n"))

        exit_code = await proc.wait()
        error_message = None
        if exit_code != 0:
            try:
                with open(log_path, "r") as f:
                    content = f.read()
                error_message = content[-2048:] if len(content) > 2048 else content
            except OSError:
                pass
        return exit_code, error_message

    async def _finalize_run(
        self, run_id: uuid.UUID, exit_code: int,
        error_message: str | None, task_id: uuid.UUID,
    ) -> None:
        completed_at = datetime.now(timezone.utc)
        job_status = JobStatus.succeeded if exit_code == 0 else JobStatus.failed

        async with self._session_factory() as session:
            await session.execute(
                update(Job).where(Job.id == run_id).values(
                    status=job_status, exit_code=exit_code, completed_at=completed_at,
                    error_message=error_message, pid=None,
                )
            )
            await session.commit()

        asyncio.create_task(self._platform_client.push_audit({
            "action": "run.complete",
            "resource_type": "run",
            "resource_id": str(run_id),
            "detail": {"task_id": str(task_id), "exit_code": exit_code},
        }))

    async def _do_execute(self, run_id: uuid.UUID) -> None:
        async with self._session_factory() as session:
            run = await session.get(PipelineRun, run_id)
            if run is None:
                return
            ctx = await self._load_context(session, run)
            if ctx is None:
                return

        project_dir = os.path.join(self._settings.data_dir, "tasks", str(ctx.task.id))
        state_dir = os.path.join(project_dir, ".state")
        log_dir = os.path.join(project_dir, "runs")
        os.makedirs(project_dir, exist_ok=True)
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, f"{run_id}.log")

        config_path = self._write_config(project_dir, ctx.config_yaml)

        cmd, command_text = self._build_command(ctx, config_path, state_dir)

        with isolated_dws_env() as dws_env:
            if ctx.credential is not None:
                await self._import_credential(ctx, dws_env)

            started_at = datetime.now(timezone.utc)
            async with self._session_factory() as session:
                await session.execute(
                    update(Job).where(Job.id == run_id).values(
                        status=JobStatus.running, started_at=started_at,
                        log_path=log_path, command_text=command_text,
                    )
                )
                await session.commit()

            run_env = {**dws_env, **ctx.project_env}
            exit_code, error_message = await self._stream_subprocess(
                cmd, run_env, project_dir, log_path, run_id,
            )

            await self._finalize_run(run_id, exit_code, error_message, ctx.task.id)

    async def _mark_run_failed(self, run_id: uuid.UUID, error_message: str) -> None:
        try:
            async with self._session_factory() as session:
                await session.execute(
                    update(Job).where(Job.id == run_id).values(
                        status=JobStatus.failed, error_message=error_message[:2048],
                        completed_at=datetime.now(timezone.utc),
                    )
                )
                await session.commit()
        except Exception as e:
            logger.error("Failed to mark run %s as failed: %s", run_id, e)
