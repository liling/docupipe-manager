import logging
import uuid

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from croniter import croniter
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from docupipe_manager.config import Settings
from docupipe_manager.models.docupipe_project import DocupipeProject, ProjectStatus
from docupipe_manager.models.pipeline_run import PipelineRun, RunStatus
from docupipe_manager.services.runner_service import RunnerService

logger = logging.getLogger(__name__)


class SchedulerService:
    """Manage APScheduler cron jobs for docupipe projects."""

    def __init__(self, runner: RunnerService, engine: AsyncEngine, settings: Settings):
        self._runner = runner
        self._engine = engine
        self._settings = settings
        self._session_factory = async_sessionmaker(engine, expire_on_commit=False)
        self._scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")

    async def start(self) -> None:
        """Start scheduler and load all active projects."""
        await self._reload_all()
        self._scheduler.start()

    async def stop(self) -> None:
        """Shutdown scheduler (non-blocking)."""
        self._scheduler.shutdown(wait=True)

    async def schedule_project(self, project_id: uuid.UUID) -> None:
        """Register or update cron job for a project."""
        job_id = f"project-{project_id}"
        try:
            self._scheduler.remove_job(job_id)
        except Exception:
            pass

        async with self._session_factory() as session:
            project = await session.get(DocupipeProject, project_id)
            if project is None:
                return
            if project.status != ProjectStatus.active or not project.schedule_enabled or not project.schedule_cron:
                return

        if not croniter.is_valid(project.schedule_cron):
            logger.warning("Invalid cron expression for project %s: %s", project_id, project.schedule_cron)
            return

        trigger = CronTrigger.from_crontab(project.schedule_cron)
        self._scheduler.add_job(
            self._scheduled_run,
            trigger,
            args=[project_id],
            id=job_id,
            replace_existing=True,
            name=f"project-{project.slug}",
        )
        logger.info("Scheduled project %s (%s)", project_id, project.slug)

    async def unschedule_project(self, project_id: uuid.UUID) -> None:
        """Remove cron job for a project."""
        job_id = f"project-{project_id}"
        try:
            self._scheduler.remove_job(job_id)
        except Exception:
            pass
        logger.info("Unscheduled project %s", project_id)

    async def _reload_all(self) -> None:
        """Scan DB and register jobs for all active + schedule_enabled projects."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(DocupipeProject).where(
                    DocupipeProject.status == ProjectStatus.active,
                    DocupipeProject.schedule_enabled.is_(True),
                    DocupipeProject.schedule_cron.isnot(None),
                )
            )
            projects = list(result.scalars().all())

        for project in projects:
            await self.schedule_project(project.id)

        logger.info("Loaded %d scheduled projects", len(projects))

    async def _scheduled_run(self, project_id: uuid.UUID) -> None:
        """APScheduler job function — guard check then trigger run."""
        async with self._session_factory() as session:
            project = await session.get(DocupipeProject, project_id)
            if project is None:
                return
            if project.status != ProjectStatus.active or not project.schedule_enabled:
                return

        await self._runner.start_run(
            project_id=project_id,
            trigger_type="scheduled",
            triggered_by=None,
            pipeline_name=project.schedule_pipeline,
            mode=project.schedule_mode,
        )
