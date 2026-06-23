import importlib.util
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader
from starlette.templating import _TemplateResponse

from docupipe_manager.auth.dependencies import get_current_user

router = APIRouter(prefix="/docupipe", tags=["pages"])

_xinyi_spec = importlib.util.find_spec("xinyi_platform")
_xinyi_ui_templates = str(Path(_xinyi_spec.origin).parent / "ui_common/templates") if _xinyi_spec else ""

_jinja_env = Environment(
    loader=FileSystemLoader(["docupipe_manager/templates", _xinyi_ui_templates] if _xinyi_ui_templates else "docupipe_manager/templates"),
    autoescape=True,
    cache_size=50,
)
templates = Jinja2Templates(env=_jinja_env)


@router.get("")
async def docupipe_root():
    return RedirectResponse(url="/docupipe/projects")


@router.get("/projects/new")
async def projects_new(request: Request, user: dict = Depends(get_current_user)):
    from docupipe_manager.main import app
    from sqlalchemy import text
    credentials = []
    try:
        async with app.state.engine.begin() as conn:
            rows = (await conn.execute(
                text("SELECT id, name FROM docupipe_manager.dws_credentials WHERE status = 'active' ORDER BY name")
            )).fetchall()
            credentials = [{"id": str(r.id), "name": r.name} for r in rows]
    except Exception:
        pass
    return _render(request, "docupipe/project_form.html", {
        "current_user": user, "project": None, "credentials": credentials,
    })


@router.get("/projects/{project_id}/edit")
async def projects_edit(request: Request, project_id: str, user: dict = Depends(get_current_user)):
    from docupipe_manager.main import app
    from sqlalchemy import text
    project = None
    credentials = []
    try:
        async with app.state.engine.begin() as conn:
            row = (await conn.execute(
                text("SELECT id, name, slug, description, config_yaml, dws_credential_id, "
                     "schedule_cron, schedule_enabled, schedule_pipeline, schedule_mode, status "
                     "FROM docupipe_manager.docupipe_projects WHERE id = :id"),
                {"id": project_id},
            )).fetchone()
            if row:
                project = dict(row._mapping)
                project["id"] = str(project["id"])
                project["dws_credential_id"] = str(project["dws_credential_id"])

            rows = (await conn.execute(
                text("SELECT id, name FROM docupipe_manager.dws_credentials WHERE status = 'active' ORDER BY name")
            )).fetchall()
            credentials = [{"id": str(r.id), "name": r.name} for r in rows]
    except Exception:
        pass
    if not project:
        return RedirectResponse(url="/docupipe/projects")
    return _render(request, "docupipe/project_form.html", {
        "current_user": user, "project": project, "credentials": credentials,
    })


@router.get("/projects")
async def projects_list(request: Request, user: dict = Depends(get_current_user)):
    from docupipe_manager.main import app
    from sqlalchemy import select
    from docupipe_manager.models.docupipe_project import DocupipeProject

    stats = None
    projects = []
    try:
        from docupipe_manager.api.stats import get_stats
        stats = await get_stats(user)
    except Exception:
        pass

    try:
        async with app.state.engine.begin() as conn:
            result = await conn.execute(
                select(DocupipeProject).order_by(DocupipeProject.created_at.desc())
            )
            projects = result.fetchall()
    except Exception:
        pass

    return _render(request, "docupipe/projects.html", {
        "current_user": user, "stats": stats, "projects": projects,
    })


@router.get("/credentials")
async def credentials_list(request: Request, user: dict = Depends(get_current_user)):
    return _render(request, "docupipe/credentials.html", {"current_user": user})


@router.get("/runs")
async def runs_list(request: Request, user: dict = Depends(get_current_user)):
    return _render(request, "docupipe/runs.html", {"current_user": user})


def _render(request: Request, template: str, context: dict) -> _TemplateResponse:
    ui_vars = _ui_vars(request)
    merged = {**ui_vars, **context}
    return templates.TemplateResponse(request, template, merged)


def _ui_vars(request: Request) -> dict:
    ui = getattr(request.app.state, "ui", {})
    return {
        "current_service": ui.get("current_service", ""),
        "nav_menu": ui.get("nav_menu", []),
        "brand": ui.get("brand", "DocuPipe"),
        "platform_url": ui.get("platform_url", ""),
        "manager_url": ui.get("manager_url", ""),
        "docupipe_url": ui.get("docupipe_url", ""),
        "products": ui.get("products", []),
    }
