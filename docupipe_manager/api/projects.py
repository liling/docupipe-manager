import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from sqlalchemy import insert

from docupipe_manager.auth.dependencies import get_current_user, require_admin
from docupipe_manager.models.project import Project, ProjectStatus

admin_router = APIRouter(prefix="/admin/api/projects", tags=["projects"])
router = APIRouter(prefix="/api/projects", tags=["projects"])


class CreateProjectRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    slug: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-z0-9-]+$")
    description: Optional[str] = None


class UpdateProjectRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    status: Optional[str] = None


def _get_engine():
    from docupipe_manager.main import app
    return app.state.engine


async def _require_access_async(project_id: uuid.UUID, user: dict = Depends(get_current_user)) -> dict:
    from docupipe_manager.auth.project_access import is_project_member, is_project_owner
    if user.get("role") == "admin":
        return user
    if await is_project_owner(project_id, user) or await is_project_member(project_id, user):
        return user
    from sqlalchemy import text
    async with _get_engine().begin() as conn:
        exists = (await conn.execute(
            text("SELECT 1 FROM docupipe_manager.projects WHERE id = :pid AND status != 'archived'"),
            {"pid": str(project_id)},
        )).fetchone()
    raise HTTPException(status_code=404 if exists is None else 403,
                        detail="Project not found" if exists is None else "Not a project member")


async def _require_owner_async(project_id: uuid.UUID, user: dict = Depends(get_current_user)) -> dict:
    from docupipe_manager.auth.project_access import is_project_owner
    if await is_project_owner(project_id, user):
        return user
    from sqlalchemy import text
    async with _get_engine().begin() as conn:
        exists = (await conn.execute(
            text("SELECT 1 FROM docupipe_manager.projects WHERE id = :pid AND status != 'archived'"),
            {"pid": str(project_id)},
        )).fetchone()
    raise HTTPException(status_code=404 if exists is None else 403,
                        detail="Project not found" if exists is None else "Project owner required")


def _project_dict(row, include_owner=False, current_user=None) -> dict:
    d = {
        "id": str(row.id), "name": row.name, "slug": row.slug,
        "description": row.description,
        "status": row.status.value if hasattr(row.status, "value") else row.status,
        "created_at": str(row.created_at),
    }
    if include_owner and current_user is not None:
        d["is_owner"] = (str(row.owner_id) == current_user["id"]) or current_user.get("role") == "admin"
        d["can_manage_members"] = bool(d["is_owner"])
    return d


@admin_router.post("")
async def create_project(body: CreateProjectRequest, user: dict = Depends(require_admin)):
    from sqlalchemy import select
    engine = _get_engine()
    async with engine.begin() as conn:
        existing = (await conn.execute(select(Project).where(
            (Project.slug == body.slug) | (Project.name == body.name)
        ))).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="Project name or slug already exists")
        project_id = uuid.uuid4()
        await conn.execute(
            insert(Project).values(
                id=project_id, name=body.name, slug=body.slug,
                description=body.description,
                owner_id=uuid.UUID(user["id"]),
            )
        )
    return {"id": str(project_id)}


@router.get("")
async def list_projects(user: dict = Depends(get_current_user)):
    """admin 看全部；普通用户看自己 Member 的项目（未归档）。"""
    from sqlalchemy import select, text
    engine = _get_engine()
    async with engine.begin() as conn:
        if user.get("role") == "admin":
            rows = (await conn.execute(
                select(Project).where(Project.status != ProjectStatus.archived)
                .order_by(Project.created_at.desc())
            )).fetchall()
        else:
            rows = (await conn.execute(text("""
                SELECT p.* FROM docupipe_manager.projects p
                JOIN docupipe_manager.project_members m ON m.project_id = p.id
                WHERE m.user_id = :uid AND p.status != 'archived'
                ORDER BY p.created_at DESC
            """), {"uid": user["id"]})).fetchall()
    return [_project_dict(r) for r in rows]


@router.get("/{project_id}")
async def get_project(project_id: uuid.UUID, user: dict = Depends(_require_access_async)):
    from sqlalchemy import select
    engine = _get_engine()
    async with engine.begin() as conn:
        row = (await conn.execute(select(Project).where(Project.id == project_id))).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return _project_dict(row, include_owner=True, current_user=user)


@router.put("/{project_id}")
async def update_project(project_id: uuid.UUID, body: UpdateProjectRequest,
                         user: dict = Depends(_require_access_async)):
    from sqlalchemy import select
    engine = _get_engine()
    async with engine.begin() as conn:
        row = (await conn.execute(select(Project).where(Project.id == project_id))).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Project not found")
        data = body.model_dump(exclude_unset=True)
        if "status" in data and data["status"] not in ("active", "paused"):
            raise HTTPException(status_code=400, detail="status must be active or paused")
        await conn.execute(
            Project.__table__.update().where(Project.id == project_id).values(**data)
        )
    return {"status": "updated"}


@admin_router.delete("/{project_id}")
async def archive_project(project_id: uuid.UUID, user: dict = Depends(_require_owner_async)):
    """归档项目（owner/admin）+ 取消所有任务调度。"""
    from sqlalchemy import select, update, text
    engine = _get_engine()
    async with engine.begin() as conn:
        row = (await conn.execute(select(Project).where(Project.id == project_id))).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Project not found")
        await conn.execute(update(Project).where(Project.id == project_id).values(status=ProjectStatus.archived))
        task_ids = [r.id for r in (await conn.execute(
            text("SELECT id FROM docupipe_manager.tasks WHERE project_id = :pid"), {"pid": str(project_id)}
        )).fetchall()]
    from docupipe_manager.main import app
    for tid in task_ids:
        await app.state.scheduler.unschedule_task(tid)
    return {"status": "archived"}
