import importlib.util
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader
from starlette.templating import _TemplateResponse

from docupipe_manager.auth.dependencies import get_current_user, require_admin

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


@router.get("/projects")
async def projects_list(request: Request, user: dict = Depends(get_current_user)):
    return _render(request, "docupipe/projects.html", {"current_user": user})


@router.get("/projects/new")
async def projects_new(request: Request, user: dict = Depends(require_admin)):
    return _render(request, "docupipe/project_detail.html",
                   {"current_user": user, "mode": "new", "project": None})


@router.get("/projects/{project_id}")
async def project_detail(request: Request, project_id: str, user: dict = Depends(get_current_user)):
    return _render(request, "docupipe/project_detail.html",
                   {"current_user": user, "mode": "view", "project_id": project_id, "project": None})


@router.get("/projects/{project_id}/tasks/new")
async def task_new(request: Request, project_id: str, user: dict = Depends(get_current_user)):
    return _render(request, "docupipe/task_form.html",
                   {"current_user": user, "project_id": project_id, "task": None})


@router.get("/projects/{project_id}/tasks/{task_id}/edit")
async def task_edit(request: Request, project_id: str, task_id: str, user: dict = Depends(get_current_user)):
    return _render(request, "docupipe/task_form.html",
                   {"current_user": user, "project_id": project_id, "task_id": task_id, "task": None})


@router.get("/credentials")
async def credentials_list(request: Request, user: dict = Depends(get_current_user)):
    return _render(request, "docupipe/credentials.html", {"current_user": user})


@router.get("/runs")
async def runs_list(request: Request, user: dict = Depends(get_current_user)):
    return _render(request, "docupipe/runs.html", {"current_user": user})


@router.get("/runs/{run_id}")
async def run_detail(request: Request, run_id: str, user: dict = Depends(get_current_user)):
    return _render(request, "docupipe/runs/detail.html",
                   {"current_user": user, "run_id": run_id})


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
