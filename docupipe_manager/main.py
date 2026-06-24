from contextlib import asynccontextmanager
import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from alembic import command
from alembic.config import Config as AlembicConfig
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from docupipe_manager.config import Settings
from docupipe_manager.db import init_db, get_engine

settings = Settings()

logger = logging.getLogger(__name__)

_pool = ThreadPoolExecutor(max_workers=1)


def _run_migrations() -> None:
    alembic_cfg = AlembicConfig()
    alembic_cfg.set_main_option("script_location", "docupipe_manager/migrations")
    alembic_cfg.set_main_option("sqlalchemy.url", settings.database_url)
    alembic_cfg.set_main_option("version_table_schema", settings.manager_schema)
    command.upgrade(alembic_cfg, "head")
    logger.info("Database migrations applied")


@asynccontextmanager
async def lifespan(app: FastAPI):
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(_pool, _run_migrations)

    init_db(settings)
    engine = get_engine()

    async with engine.begin() as conn:
        await conn.execute(text(
            f"UPDATE {settings.manager_schema}.pipeline_runs "
            "SET status='failed', error_message='process restart' "
            "WHERE status IN ('pending', 'running')"
        ))

    from docupipe_manager.platform.client import XinyiPlatformClient
    from docupipe_manager.platform.config import PlatformSettings
    from docupipe_manager.platform.cache import UserLRUCache

    platform_client = XinyiPlatformClient(PlatformSettings.from_app_settings(settings))
    user_cache = UserLRUCache(ttl_seconds=settings.user_cache_ttl_seconds)

    from docupipe_manager.services.runner_service import RunnerService
    from docupipe_manager.services.scheduler_service import SchedulerService
    from docupipe_manager.services.credential_service import CredentialService

    runner = RunnerService(engine, settings, platform_client)
    scheduler = SchedulerService(runner, engine, settings)
    credential = CredentialService(engine, settings, platform_client)

    await scheduler.start()

    app.state.runner = runner
    app.state.scheduler = scheduler
    app.state.credential = credential
    app.state.platform_client = platform_client
    app.state.user_cache = user_cache
    app.state.settings = settings
    app.state.engine = engine

    session_cleanup_task = asyncio.create_task(_session_cleanup_loop(credential))

    yield

    session_cleanup_task.cancel()
    await scheduler.stop()
    await engine.dispose()


async def _session_cleanup_loop(credential) -> None:
    """Periodically clean expired device login sessions (every 60s)."""
    try:
        while True:
            await asyncio.sleep(60)
            await credential.cleanup_expired_sessions()
    except asyncio.CancelledError:
        pass


app = FastAPI(title="DocuPipe Manager", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.base_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def limit_request_body_size(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > 1_048_576:
        return JSONResponse(status_code=413, content={"detail": "Request body too large (max 1MB)"})
    return await call_next(request)


@app.exception_handler(HTTPException)
async def page_auth_redirect(request: Request, exc: HTTPException):
    if exc.status_code == 401 and request.url.path.startswith("/docupipe/") and not request.url.path.startswith(
        ("/docupipe/api/", "/docupipe/admin/api/")
    ):
        return RedirectResponse(
            url=f"/docupipe/auth/login-redirect?return_to={quote(request.url.path)}",
            status_code=302,
        )
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


from xinyi_platform.ui_common import install_ui  # noqa: E402


DOCUPIPE_NAV_MENU = [
    {
        "label": "DocuPipe",
        "items": [
            {"id": "projects", "label": "项目", "href": "/docupipe/projects"},
            {"id": "runs",     "label": "运行", "href": "/docupipe/runs"},
        ],
    },
]

install_ui(
    app,
    current_service="docupipe-manager",
    nav_menu=DOCUPIPE_NAV_MENU,
    brand="DocuPipe",
    platform_url=settings.platform_url,
    manager_url="",
    docupipe_url=settings.base_url,
    service_prefix="/docupipe",
)

app.mount("/docupipe/static", StaticFiles(directory="docupipe_manager/static"), name="static")

from docupipe_manager.api.auth import router as auth_router
from docupipe_manager.api.pages import router as pages_router
from docupipe_manager.api.projects import admin_router as projects_admin_router, router as projects_router
from docupipe_manager.api.credentials import router as credentials_router
from docupipe_manager.api.runs import router as runs_router
from docupipe_manager.api.members import router as members_router, users_router
from docupipe_manager.api.stats import router as stats_router
from docupipe_manager.api.tasks import router as tasks_router
from docupipe_manager.api.env_vars import router as env_vars_router

app.include_router(auth_router, prefix="/docupipe")
app.include_router(pages_router)
app.include_router(projects_router, prefix="/docupipe")
app.include_router(projects_admin_router, prefix="/docupipe")
app.include_router(credentials_router, prefix="/docupipe")
app.include_router(runs_router, prefix="/docupipe")
app.include_router(members_router, prefix="/docupipe")
app.include_router(users_router, prefix="/docupipe")
app.include_router(stats_router, prefix="/docupipe")
app.include_router(tasks_router, prefix="/docupipe")
app.include_router(env_vars_router, prefix="/docupipe")


@app.get("/")
async def root():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/docupipe/projects", status_code=302)


@app.get("/docupipe/health")
async def health():
    return {"status": "ok"}
