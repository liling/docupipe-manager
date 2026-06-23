import asyncio
import logging
import os
import shutil
import signal
import uuid
from datetime import datetime, timezone
from tempfile import mkdtemp

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from docupipe_manager.config import Settings
from docupipe_manager.crypto import decrypt_sm4
from docupipe_manager.models.dws_credential import DwsCredential
from docupipe_manager.models.docupipe_project import DocupipeProject
from docupipe_manager.models.pipeline_run import PipelineRun, RunStatus
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

    async def start_run(
        self,
        project_id: uuid.UUID,
        trigger_type: str,
        triggered_by: uuid.UUID | None,
        pipeline_name: str | None = None,
        mode: str = "incremental",
    ) -> PipelineRun:
        """Create a run record and start execution in background."""
        run = PipelineRun(
            project_id=project_id,
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

    async def _do_execute(self, run_id: uuid.UUID) -> None:
        async with self._session_factory() as session:
            run = await session.get(PipelineRun, run_id)
            if run is None:
                return
            project = await session.get(DocupipeProject, run.project_id)
            credential = await session.get(DwsCredential, project.dws_credential_id)
            if project is None or credential is None:
                await self._mark_run_failed(run_id, "Project or credential not found")
                return

            config_yaml = project.config_yaml
            slug = project.slug
            mode = run.mode
            pipeline_name = run.pipeline_name

        settings = self._settings
        project_dir = os.path.join(settings.data_dir, "projects", slug)
        os.makedirs(project_dir, exist_ok=True)

        config_path = os.path.join(project_dir, "config.yaml")
        with open(config_path, "w") as f:
            f.write(config_yaml)

        state_dir = os.path.join(project_dir, ".state")
        log_dir = os.path.join(project_dir, "runs")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, f"{run_id}.log")

        home_dir = mkdtemp(prefix="dws-home-")
        try:
            key_hex = settings.encryption_key
            auth_b64 = decrypt_sm4(credential.auth_blob.hex(), key_hex)

            auth_path = os.path.join(home_dir, "auth.b64")
            with open(auth_path, "w") as f:
                f.write(auth_b64)

            import_proc = await asyncio.create_subprocess_exec(
                settings.dws_cli_path, "auth", "import", "-i", auth_path, "--base64",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "HOME": home_dir},
            )
            await import_proc.communicate()

            cmd = [
                settings.docupipe_python, "-m", "docupipe", "run",
                "--config", config_path,
                "--state-dir", state_dir,
                "--mode", mode,
                "--log-level", "INFO",
            ]
            if pipeline_name:
                cmd.extend(["--pipeline", pipeline_name])

            started_at = datetime.now(timezone.utc)
            async with self._session_factory() as session:
                await session.execute(
                    update(PipelineRun).where(PipelineRun.id == run_id).values(
                        status=RunStatus.running,
                        started_at=started_at,
                        log_path=log_path,
                    )
                )
                await session.commit()

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                env={**os.environ, "HOME": home_dir},
                cwd=project_dir,
            )

            async with self._session_factory() as session:
                await session.execute(
                    update(PipelineRun).where(PipelineRun.id == run_id).values(pid=proc.pid)
                )
                await session.commit()

            max_bytes = settings.run_log_max_bytes
            with open(log_path, "w") as log_file:
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        break
                    log_file.write(line.decode("utf-8", errors="replace"))
                    if log_file.tell() > max_bytes:
                        log_file.truncate(max_bytes // 2)
                        log_file.seek(0, 2)

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
                "project_id": str(run.project_id if run else project_id),
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
