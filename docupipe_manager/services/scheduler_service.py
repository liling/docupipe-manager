import logging
import uuid

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from croniter import croniter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from docupipe_manager.config import Settings
from docupipe_manager.models.task import Task, TaskStatus
from docupipe_manager.services.runner_service import RunnerService

logger = logging.getLogger(__name__)


class SchedulerService:
    """Manage APScheduler cron jobs for tasks."""

    def __init__(self, runner: RunnerService, engine: AsyncEngine, settings: Settings):
        self._runner = runner
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

    async def _reload_all(self) -> None:
        """Scan DB and register jobs for all active + schedule_enabled tasks."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(Task).where(
                    Task.status == TaskStatus.active,
                    Task.schedule_enabled.is_(True),
                    Task.schedule_cron.isnot(None),
                )
            )
            tasks = list(result.scalars().all())

        for t in tasks:
            await self.schedule_task(t.id)

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
