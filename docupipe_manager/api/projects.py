import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from typing_extensions import Literal

from docupipe_manager.auth.dependencies import require_admin
from docupipe_manager.models.docupipe_project import ProjectStatus
from docupipe_manager.models.dws_credential import CredentialStatus

router = APIRouter(prefix="/admin/api/docupipe/projects", tags=["projects"])


class CreateProjectRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    slug: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-z0-9-]+$")
    description: Optional[str] = None
    config_yaml: str
    dws_credential_id: str
    schedule_cron: Optional[str] = None
    schedule_enabled: bool = True
    schedule_pipeline: Optional[str] = None
    schedule_mode: Literal["full", "incremental", "mirror"] = "incremental"

    @field_validator("config_yaml")
    @classmethod
    def validate_yaml(cls, v: str) -> str:
        import yaml
        if not v.strip():
            raise ValueError("config_yaml must not be empty")
        try:
            parsed = yaml.safe_load(v)
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML: {e}")
        if not isinstance(parsed, dict):
            raise ValueError("YAML must be a mapping (dict)")
        pipelines = parsed.get("pipelines")
        if pipelines is None or not isinstance(pipelines, list):
            raise ValueError("YAML must contain a 'pipelines' key with a list value")
        return v

    @field_validator("schedule_cron")
    @classmethod
    def validate_cron(cls, v: Optional[str]) -> Optional[str]:
        if v:
            from croniter import croniter
            if not croniter.is_valid(v):
                raise ValueError(f"Invalid cron expression: {v}")
        return v


class UpdateProjectRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    config_yaml: Optional[str] = None
    dws_credential_id: Optional[str] = None
    schedule_cron: Optional[str] = None
    schedule_enabled: Optional[bool] = None
    schedule_pipeline: Optional[str] = None
    schedule_mode: Optional[Literal["full", "incremental", "mirror"]] = None


class TriggerRunRequest(BaseModel):
    pipeline_name: Optional[str] = None
    mode: Optional[Literal["full", "incremental", "mirror"]] = None


ProjectStatusLiteral = Literal["active", "paused", "archived"]

@router.get("")
async def list_projects(
    user: dict = Depends(require_admin),
):
    from docupipe_manager.main import app
    from sqlalchemy import select, text

    async with app.state.engine.begin() as conn:
        projects = (await conn.execute(text("""
            SELECT p.id, p.name, p.slug, p.schedule_cron, p.schedule_enabled,
                   p.schedule_pipeline, p.schedule_mode, p.status, p.dws_credential_id,
                   c.name as credential_name,
                   (SELECT status FROM docupipe_manager.pipeline_runs
                    WHERE project_id = p.id ORDER BY created_at DESC LIMIT 1) as last_run_status
            FROM docupipe_manager.docupipe_projects p
            LEFT JOIN docupipe_manager.dws_credentials c ON c.id = p.dws_credential_id
            ORDER BY p.created_at DESC
        """))).fetchall()

    return [
        {
            "id": str(r.id), "name": r.name, "slug": r.slug,
            "schedule_cron": r.schedule_cron,
            "schedule_enabled": r.schedule_enabled,
            "schedule_pipeline": r.schedule_pipeline,
            "schedule_mode": r.schedule_mode,
            "status": r.status,
            "credential_name": r.credential_name,
            "last_run_status": r.last_run_status,
        }
        for r in projects
    ]


@router.post("")
async def create_project(
    body: CreateProjectRequest,
    user: dict = Depends(require_admin),
):
    from docupipe_manager.main import app
    from docupipe_manager.models.docupipe_project import DocupipeProject
    from docupipe_manager.models.dws_credential import DwsCredential

    async with app.state.engine.begin() as conn:
        from sqlalchemy import select, text
        result = await conn.execute(
            select(DwsCredential).where(
                DwsCredential.id == uuid.UUID(body.dws_credential_id),
                DwsCredential.status == CredentialStatus.active,
            )
        )
        cred = result.scalar_one_or_none()
        if cred is None:
            raise HTTPException(status_code=400, detail="DWS credential not found or not active")

        project = DocupipeProject(
            name=body.name,
            slug=body.slug,
            description=body.description,
            config_yaml=body.config_yaml,
            dws_credential_id=uuid.UUID(body.dws_credential_id),
            schedule_cron=body.schedule_cron,
            schedule_enabled=body.schedule_enabled,
            schedule_pipeline=body.schedule_pipeline,
            schedule_mode=body.schedule_mode,
            created_by=uuid.UUID(user["id"]),
        )
        conn.add(project)
        await conn.flush()
        project_id = project.id

    if body.schedule_cron:
        await app.state.scheduler.schedule_project(project_id)

    return {"id": str(project_id)}


@router.get("/{project_id}")
async def get_project(
    project_id: uuid.UUID,
    user: dict = Depends(require_admin),
):
    from docupipe_manager.main import app
    from sqlalchemy import select
    from docupipe_manager.models.docupipe_project import DocupipeProject

    async with app.state.engine.begin() as conn:
        result = await conn.execute(
            select(DocupipeProject).where(DocupipeProject.id == project_id)
        )
        project = result.scalar_one_or_none()
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
    return {
        "id": str(project.id),
        "name": project.name,
        "slug": project.slug,
        "description": project.description,
        "config_yaml": project.config_yaml,
        "dws_credential_id": str(project.dws_credential_id),
        "schedule_cron": project.schedule_cron,
        "schedule_enabled": project.schedule_enabled,
        "schedule_pipeline": project.schedule_pipeline,
        "schedule_mode": project.schedule_mode,
        "status": project.status.value,
    }


@router.put("/{project_id}")
async def update_project(
    project_id: uuid.UUID,
    body: UpdateProjectRequest,
    user: dict = Depends(require_admin),
):
    from docupipe_manager.main import app
    from docupipe_manager.models.docupipe_project import DocupipeProject
    from sqlalchemy import select

    async with app.state.engine.begin() as session:
        result = await session.execute(
            select(DocupipeProject).where(DocupipeProject.id == project_id)
        )
        project = result.scalar_one_or_none()
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")

        update_data = body.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(project, key, value)
        await session.flush()

    if any(k in update_data for k in ("schedule_cron", "schedule_enabled", "status")):
        if update_data.get("schedule_cron"):
            await app.state.scheduler.schedule_project(project_id)
        else:
            await app.state.scheduler.unschedule_project(project_id)

    return {"status": "updated"}


@router.delete("/{project_id}")
async def archive_project(
    project_id: uuid.UUID,
    user: dict = Depends(require_admin),
):
    from docupipe_manager.main import app
    from docupipe_manager.models.docupipe_project import DocupipeProject
    from sqlalchemy import select

    async with app.state.engine.begin() as session:
        result = await session.execute(
            select(DocupipeProject).where(DocupipeProject.id == project_id)
        )
        project = result.scalar_one_or_none()
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        project.status = ProjectStatus.archived
        await session.flush()

    await app.state.scheduler.unschedule_project(project_id)
    return {"status": "archived"}


@router.post("/{project_id}/trigger")
async def trigger_run(
    project_id: uuid.UUID,
    body: TriggerRunRequest,
    user: dict = Depends(require_admin),
):
    from docupipe_manager.main import app
    from docupipe_manager.models.docupipe_project import DocupipeProject
    from sqlalchemy import select

    async with app.state.engine.begin() as session:
        result = await session.execute(
            select(DocupipeProject).where(DocupipeProject.id == project_id)
        )
        project = result.scalar_one_or_none()
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")

    run = await app.state.runner.start_run(
        project_id=project_id,
        trigger_type="manual",
        triggered_by=uuid.UUID(user["id"]),
        pipeline_name=body.pipeline_name or project.schedule_pipeline,
        mode=body.mode or project.schedule_mode,
    )
    return {"run_id": str(run.id), "status": run.status.value}
