import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import insert, select, update
from typing_extensions import Literal

from docupipe_manager import deps
from docupipe_manager.api.projects import _require_access_async
from docupipe_manager.models.task import Task, TaskStatus

router = APIRouter(prefix="/api/projects/{project_id}/tasks", tags=["tasks"])


def _validate_yaml(v: str) -> str:
    import yaml
    if not v.strip():
        raise ValueError("config_yaml must not be empty")
    try:
        parsed = yaml.safe_load(v)
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML: {e}")
    if not isinstance(parsed, dict):
        raise ValueError("YAML must be a mapping")
    if not isinstance(parsed.get("pipelines"), list):
        raise ValueError("YAML must contain a 'pipelines' list")
    return v


def _validate_cron(v: Optional[str]) -> Optional[str]:
    if v:
        from croniter import croniter
        if not croniter.is_valid(v):
            raise ValueError(f"Invalid cron: {v}")
    return v


class CreateTaskRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    slug: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-z0-9-]+$")
    description: Optional[str] = None
    config_yaml: str
    credential_id: Optional[str] = None
    credential_type: Optional[Literal["dws"]] = None
    schedule_cron: Optional[str] = None
    schedule_enabled: bool = True
    schedule_pipeline: Optional[str] = None
    schedule_mode: Literal["full", "incremental", "mirror"] = "incremental"

    @field_validator("config_yaml")
    @classmethod
    def _v_yaml(cls, v): return _validate_yaml(v)

    @field_validator("schedule_cron")
    @classmethod
    def _v_cron(cls, v): return _validate_cron(v)

    @model_validator(mode="after")
    def _validate_credential_pair(self):
        if (self.credential_id is None) != (self.credential_type is None):
            raise ValueError("credential_id and credential_type must be both set or both empty")
        return self


class UpdateTaskRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    config_yaml: Optional[str] = None
    credential_id: Optional[str] = None
    credential_type: Optional[Literal["dws"]] = None
    schedule_cron: Optional[str] = None
    schedule_enabled: Optional[bool] = None
    schedule_pipeline: Optional[str] = None
    schedule_mode: Optional[Literal["full", "incremental", "mirror"]] = None

    @field_validator("config_yaml")
    @classmethod
    def _v_yaml(cls, v): return _validate_yaml(v) if v else v

    @field_validator("schedule_cron")
    @classmethod
    def _v_cron(cls, v): return _validate_cron(v)

    @model_validator(mode="after")
    def _validate_credential_pair(self):
        if (self.credential_id is None) != (self.credential_type is None):
            raise ValueError("credential_id and credential_type must be both set or both empty")
        return self


class TriggerRequest(BaseModel):
    pipeline_name: Optional[str] = None
    mode: Optional[Literal["full", "incremental", "mirror"]] = None


@router.get("")
async def list_tasks(project_id: uuid.UUID, user: dict = Depends(_require_access_async)):
    from sqlalchemy import text
    engine = deps.get_engine()
    async with engine.begin() as conn:
        rows = (await conn.execute(text("""
            SELECT t.id, t.name, t.slug, t.schedule_cron, t.schedule_enabled,
                   t.schedule_pipeline, t.schedule_mode, t.status, t.created_at,
                   (SELECT status FROM docupipe_manager.pipeline_runs
                    WHERE task_id = t.id ORDER BY created_at DESC LIMIT 1) as last_run_status
            FROM docupipe_manager.tasks t
            WHERE t.project_id = :pid AND t.status != 'archived'
            ORDER BY t.created_at DESC
        """), {"pid": str(project_id)})).fetchall()
    return [_task_summary(r) for r in rows]


@router.post("")
async def create_task(project_id: uuid.UUID, body: CreateTaskRequest,
                      user: dict = Depends(_require_access_async)):
    engine = deps.get_engine()
    task_id = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            insert(Task).values(
                id=task_id, project_id=project_id, name=body.name, slug=body.slug,
                description=body.description, config_yaml=body.config_yaml,
                credential_id=uuid.UUID(body.credential_id) if body.credential_id else None,
                credential_type=body.credential_type,
                schedule_cron=body.schedule_cron, schedule_enabled=body.schedule_enabled,
                schedule_pipeline=body.schedule_pipeline, schedule_mode=body.schedule_mode,
                created_by=uuid.UUID(user["id"]),
            )
        )
    if body.schedule_cron:
        await deps.get_scheduler().schedule_task(task_id)
    return {"id": str(task_id)}


@router.get("/{task_id}")
async def get_task(project_id: uuid.UUID, task_id: uuid.UUID,
                   user: dict = Depends(_require_access_async)):
    engine = deps.get_engine()
    async with engine.begin() as conn:
        t = (await conn.execute(select(Task).where(Task.id == task_id, Task.project_id == project_id))).fetchone()
    if t is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return _task_detail(t)


@router.put("/{task_id}")
async def update_task(project_id: uuid.UUID, task_id: uuid.UUID, body: UpdateTaskRequest,
                      user: dict = Depends(_require_access_async)):
    engine = deps.get_engine()
    async with engine.begin() as conn:
        t = (await conn.execute(select(Task).where(Task.id == task_id, Task.project_id == project_id))).fetchone()
        if t is None:
            raise HTTPException(status_code=404, detail="Task not found")
        data = body.model_dump(exclude_unset=True)
        if data.get("credential_id"):
            data["credential_id"] = uuid.UUID(data["credential_id"])
        await conn.execute(update(Task).where(Task.id == task_id).values(**data))
    scheduler = deps.get_scheduler()
    if data.get("schedule_cron"):
        await scheduler.schedule_task(task_id)
    elif "schedule_cron" in data and data["schedule_cron"] is None:
        await scheduler.unschedule_task(task_id)
    elif data.get("schedule_enabled") is False:
        await scheduler.unschedule_task(task_id)
    return {"status": "updated"}


@router.delete("/{task_id}")
async def archive_task(project_id: uuid.UUID, task_id: uuid.UUID,
                       user: dict = Depends(_require_access_async)):
    engine = deps.get_engine()
    async with engine.begin() as conn:
        t = (await conn.execute(select(Task).where(Task.id == task_id, Task.project_id == project_id))).fetchone()
        if t is None:
            raise HTTPException(status_code=404, detail="Task not found")
        await conn.execute(update(Task).where(Task.id == task_id).values(status=TaskStatus.archived))
    await deps.get_scheduler().unschedule_task(task_id)
    return {"status": "archived"}


@router.post("/{task_id}/trigger")
async def trigger_task(project_id: uuid.UUID, task_id: uuid.UUID, body: TriggerRequest,
                       user: dict = Depends(_require_access_async)):
    engine = deps.get_engine()
    async with engine.begin() as conn:
        t = (await conn.execute(select(Task).where(Task.id == task_id, Task.project_id == project_id))).fetchone()
        if t is None:
            raise HTTPException(status_code=404, detail="Task not found")
    run, job = await deps.get_runner().start_run(
        task_id=task_id, trigger_type="manual", triggered_by=uuid.UUID(user["id"]),
        pipeline_name=body.pipeline_name or t.schedule_pipeline,
        mode=body.mode or t.schedule_mode,
    )
    return {"run_id": str(run.id), "status": job.status.value}


def _task_summary(r) -> dict:
    return {
        "id": str(r.id), "name": r.name, "slug": r.slug,
        "schedule_cron": r.schedule_cron, "schedule_enabled": r.schedule_enabled,
        "schedule_pipeline": r.schedule_pipeline, "schedule_mode": r.schedule_mode,
        "status": r.status.value if hasattr(r.status, "value") else r.status,
        "last_run_status": r.last_run_status,
        "created_at": str(r.created_at),
    }


def _task_detail(t) -> dict:
    return {
        "id": str(t.id), "name": t.name, "slug": t.slug, "description": t.description,
        "config_yaml": t.config_yaml,
        "credential_id": str(t.credential_id) if t.credential_id else None,
        "credential_type": t.credential_type.value if t.credential_type else None,
        "schedule_cron": t.schedule_cron, "schedule_enabled": t.schedule_enabled,
        "schedule_pipeline": t.schedule_pipeline, "schedule_mode": t.schedule_mode,
        "status": t.status.value if hasattr(t.status, "value") else t.status,
    }
