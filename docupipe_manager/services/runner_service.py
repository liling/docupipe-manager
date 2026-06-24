import asyncio
import logging
import os
import shlex
import shutil
import signal
import uuid
from collections import deque
from datetime import datetime, timezone
from tempfile import mkdtemp

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from docupipe_manager.config import Settings
from docupipe_manager.crypto import decrypt_sm4
from docupipe_manager.models.dws_credential import DwsCredential
from docupipe_manager.models.pipeline_run import PipelineRun, RunStatus
from docupipe_manager.models.project_env_var import ProjectEnvVar
from docupipe_manager.models.task import CredentialType, Task
from docupipe_manager.platform.client import XinyiPlatformClient

logger = logging.getLogger(__name__)


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
            queue.put_nowait(None)   # run already ended; guarantee the sentinel
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
    ) -> PipelineRun:
        """Create a run record and start execution in background."""
        run = PipelineRun(
            task_id=task_id,
            trigger_type=trigger_type,
            triggered_by=triggered_by,
            pipeline_name=pipeline_name,
            mode=mode,
            status=RunStatus.pending,
        )
        async with self._session_factory() as session:
            session.add(run)
            await session.commit()
            await session.refresh(run)

        asyncio.create_task(self._execute_run(run.id))
        return run

    async def cancel_run(self, run_id: uuid.UUID) -> None:
        """Cancel a run. Running → SIGTERM. Pending → status change."""
        async with self._session_factory() as session:
            run = await session.get(PipelineRun, run_id)
            if run is None:
                raise ValueError("Run not found")

            if run.status == RunStatus.pending:
                run.status = RunStatus.cancelled
                await session.commit()
            elif run.status == RunStatus.running and run.pid:
                try:
                    os.kill(run.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                run.status = RunStatus.cancelled
                await session.commit()

    async def _execute_run(self, run_id: uuid.UUID) -> None:
        """Run the pipeline in a subprocess. Fire-and-forget."""
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

    async def _do_execute(self, run_id: uuid.UUID) -> None:
        async with self._session_factory() as session:
            run = await session.get(PipelineRun, run_id)
            if run is None:
                return
            task = await session.get(Task, run.task_id)
            if task is None:
                await self._mark_run_failed(run_id, "Task not found")
                return
            credential = None
            if task.credential_id is not None and task.credential_type is not None:
                if task.credential_type == CredentialType.dws:
                    credential = await session.get(DwsCredential, task.credential_id)
                else:
                    await self._mark_run_failed(run_id, f"Unsupported credential type: {task.credential_type}")
                    return
                if credential is None:
                    await self._mark_run_failed(run_id, "Credential not found")
                    return

            config_yaml = task.config_yaml
            slug = task.slug
            mode = run.mode
            pipeline_name = run.pipeline_name
            cred_type = task.credential_type

            env_var_rows = (await session.execute(
                select(ProjectEnvVar).where(ProjectEnvVar.project_id == task.project_id)
            )).scalars().all()

        settings = self._settings

        project_env: dict[str, str] = {}
        for ev in env_var_rows:
            if ev.is_secret:
                try:
                    ev_value = decrypt_sm4(ev.value, settings.encryption_key)
                except Exception:
                    await self._mark_run_failed(run_id, f"环境变量 {ev.key} 解密失败")
                    return
            else:
                ev_value = ev.value
            project_env[ev.key] = ev_value

        project_dir = os.path.join(settings.data_dir, "tasks", str(task.id))
        os.makedirs(project_dir, exist_ok=True)

        config_path = os.path.join(project_dir, "config.yaml")
        with open(config_path, "w") as f:
            f.write(config_yaml)

        state_dir = os.path.join(project_dir, ".state")
        log_dir = os.path.join(project_dir, "runs")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, f"{run_id}.log")

        home_dir = mkdtemp(prefix="dws-home-")
        os.makedirs(os.path.join(home_dir, "Library", "Keychains"), exist_ok=True)
        try:
            if credential is not None:
                key_hex = settings.encryption_key
                auth_b64 = decrypt_sm4(credential.auth_blob.hex(), key_hex)

                auth_path = os.path.join(home_dir, "auth.b64")
                with open(auth_path, "w") as f:
                    f.write(auth_b64)

                import_proc = await asyncio.create_subprocess_exec(
                    settings.dws_cli_path, "auth", "import", "--base64", "-i", auth_path,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    env={**os.environ, **project_env, "HOME": home_dir},
                )
                await import_proc.communicate()

            cmd = [
                settings.docupipe_python, "-m", "docupipe",
                "--state-dir", state_dir,
                "--log-level", "INFO",
                "run",
                "--config", config_path,
                "--mode", mode,
            ]
            if pipeline_name:
                cmd.extend(["--pipeline", pipeline_name])
            command_text = " ".join(shlex.quote(c) for c in cmd)

            started_at = datetime.now(timezone.utc)
            async with self._session_factory() as session:
                await session.execute(
                    update(PipelineRun).where(PipelineRun.id == run_id).values(
                        status=RunStatus.running,
                        started_at=started_at,
                        log_path=log_path,
                        command_text=command_text,
                    )
                )
                await session.commit()

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                env={**os.environ, **project_env, "HOME": home_dir},
                cwd=project_dir,
            )

            async with self._session_factory() as session:
                await session.execute(
                    update(PipelineRun).where(PipelineRun.id == run_id).values(pid=proc.pid)
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
            completed_at = datetime.now(timezone.utc)
            status = RunStatus.succeeded if exit_code == 0 else RunStatus.failed

            error_message = None
            if exit_code != 0:
                try:
                    with open(log_path, "r") as f:
                        content = f.read()
                    error_message = content[-2048:] if len(content) > 2048 else content
                except OSError:
                    pass

            async with self._session_factory() as session:
                await session.execute(
                    update(PipelineRun).where(PipelineRun.id == run_id).values(
                        status=status,
                        exit_code=exit_code,
                        completed_at=completed_at,
                        error_message=error_message,
                        pid=None,
                    )
                )
                await session.commit()

            event = f"docupipe.run.{'success' if status == RunStatus.succeeded else 'fail'}"
            asyncio.create_task(self._platform_client.push_audit({
                "event": event,
                "run_id": str(run_id),
                "task_id": str(run.task_id),
                "exit_code": exit_code,
            }))

        finally:
            shutil.rmtree(home_dir, ignore_errors=True)

    async def _mark_run_failed(self, run_id: uuid.UUID, error_message: str) -> None:
        try:
            async with self._session_factory() as session:
                await session.execute(
                    update(PipelineRun).where(PipelineRun.id == run_id).values(
                        status=RunStatus.failed,
                        error_message=error_message[:2048],
                        completed_at=datetime.now(timezone.utc),
                    )
                )
                await session.commit()
        except Exception as e:
            logger.error("Failed to mark run %s as failed: %s", run_id, e)
