import logging
import uuid

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from croniter import croniter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from docupipe_manager.config import Settings
from docupipe_manager.models.dws_credential import CredentialStatus, DwsCredential
from docupipe_manager.models.task import Task, TaskStatus
from docupipe_manager.services.runner_service import RunnerService

logger = logging.getLogger(__name__)


class SchedulerService:
    """Manage APScheduler cron jobs for tasks."""

    def __init__(self, runner: RunnerService, credential_service, engine: AsyncEngine, settings: Settings):
        self._runner = runner
        self._credential = credential_service
        self._engine = engine
        self._settings = settings
        self._session_factory = async_sessionmaker(engine, expire_on_commit=False)
        self._scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")

    async def start(self) -> None:
        """Start scheduler and load all active tasks."""
        await self._reload_all()
        self._scheduler.start()

    async def stop(self) -> None:
        """Shutdown scheduler (non-blocking)."""
        self._scheduler.shutdown(wait=True)

    async def schedule_task(self, task_id: uuid.UUID) -> None:
        """Register or update cron job for a task."""
        job_id = f"task-{task_id}"
        try:
            self._scheduler.remove_job(job_id)
        except Exception:
            pass

        async with self._session_factory() as session:
            task = await session.get(Task, task_id)
            if task is None:
                return
            if task.status != TaskStatus.active or not task.schedule_enabled or not task.schedule_cron:
                return

        if not croniter.is_valid(task.schedule_cron):
            logger.warning("Invalid cron expression for task %s: %s", task_id, task.schedule_cron)
            return

        trigger = CronTrigger.from_crontab(task.schedule_cron)
        self._scheduler.add_job(
            self._scheduled_run,
            trigger,
            args=[task_id],
            id=job_id,
            replace_existing=True,
            name=f"task-{task.slug}",
        )
        logger.info("Scheduled task %s (%s)", task_id, task.slug)

    async def unschedule_task(self, task_id: uuid.UUID) -> None:
        """Remove cron job for a task."""
        job_id = f"task-{task_id}"
        try:
            self._scheduler.remove_job(job_id)
        except Exception:
            pass
        logger.info("Unscheduled task %s", task_id)

    async def schedule_keepalive(self, credential_id: uuid.UUID) -> None:
        if not self._settings.credential_keepalive_enabled:
            return
        job_id = f"keepalive-{credential_id}"
        try:
            self._scheduler.remove_job(job_id)
        except Exception:
            pass
        cron = self._settings.credential_keepalive_cron
        if not croniter.is_valid(cron):
            logger.warning("Invalid keepalive cron: %s", cron)
            return
        trigger = CronTrigger.from_crontab(cron)
        self._scheduler.add_job(
            self._scheduled_keepalive,
            trigger,
            args=[credential_id],
            id=job_id,
            replace_existing=True,
            name=f"keepalive-{credential_id}",
        )
        logger.info("Scheduled keepalive for credential %s", credential_id)

    async def unschedule_keepalive(self, credential_id: uuid.UUID) -> None:
        job_id = f"keepalive-{credential_id}"
        try:
            self._scheduler.remove_job(job_id)
        except Exception:
            pass

    async def _scheduled_keepalive(self, credential_id: uuid.UUID) -> None:
        async with self._session_factory() as session:
            cred = await session.get(DwsCredential, credential_id)
            if cred is None or cred.status != CredentialStatus.active:
                return
        await self._credential.refresh_credential(credential_id)

    async def _reload_all(self) -> None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(Task).where(
                    Task.status == TaskStatus.active,
                    Task.schedule_enabled.is_(True),
                    Task.schedule_cron.isnot(None),
                )
            )
            tasks = list(result.scalars().all())

            keepalive_creds = []
            if self._settings.credential_keepalive_enabled:
                ka_result = await session.execute(
                    select(DwsCredential).where(DwsCredential.status == CredentialStatus.active)
                )
                keepalive_creds = list(ka_result.scalars().all())

        for t in tasks:
            await self.schedule_task(t.id)

        for cred in keepalive_creds:
            await self.schedule_keepalive(cred.id)

        logger.info("Loaded %d scheduled tasks", len(tasks))

    async def _scheduled_run(self, task_id: uuid.UUID) -> None:
        """APScheduler job function — guard check then trigger run."""
        async with self._session_factory() as session:
            task = await session.get(Task, task_id)
            if task is None:
                return
            if task.status != TaskStatus.active or not task.schedule_enabled:
                return

        await self._runner.start_run(
            task_id=task_id,
            trigger_type="scheduled",
            triggered_by=None,
            pipeline_name=task.schedule_pipeline,
            mode=task.schedule_mode,
        )
