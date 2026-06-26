import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, insert, select, update

from docupipe_manager import deps
from docupipe_manager.api.projects import _require_access_async
from docupipe_manager.config import Settings
from docupipe_manager.crypto import encrypt_sm4
from docupipe_manager.models.project_env_var import ProjectEnvVar

router = APIRouter(prefix="/api/projects/{project_id}/env-vars", tags=["env-vars"])

_settings = Settings()

_KEY_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*$"


class CreateEnvVarRequest(BaseModel):
    key: str = Field(..., min_length=1, max_length=255, pattern=_KEY_PATTERN)
    value: str = Field(..., min_length=0)
    is_secret: bool = False
    description: Optional[str] = Field(None, max_length=255)


class UpdateEnvVarRequest(BaseModel):
    key: Optional[str] = Field(None, min_length=1, max_length=255, pattern=_KEY_PATTERN)
    value: Optional[str] = None
    description: Optional[str] = Field(None, max_length=255)


def _serialize(row, mask_secret: bool) -> dict:
    return {
        "id": str(row.id),
        "key": row.key,
        "value": None if (row.is_secret and mask_secret) else row.value,
        "is_secret": row.is_secret,
        "description": row.description,
        "created_at": str(row.created_at),
    }


@router.get("")
async def list_env_vars(project_id: uuid.UUID, user: dict = Depends(_require_access_async)):
    engine = deps.get_engine()
    async with engine.begin() as conn:
        rows = (await conn.execute(
            select(ProjectEnvVar).where(ProjectEnvVar.project_id == project_id)
            .order_by(ProjectEnvVar.key)
        )).fetchall()
    return [_serialize(r, mask_secret=True) for r in rows]


@router.post("")
async def create_env_var(project_id: uuid.UUID, body: CreateEnvVarRequest,
                         user: dict = Depends(_require_access_async)):
    engine = deps.get_engine()
    async with engine.begin() as conn:
        existing = (await conn.execute(
            select(ProjectEnvVar).where(
                ProjectEnvVar.project_id == project_id, ProjectEnvVar.key == body.key
            )
        )).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="变量名已存在")
        value = encrypt_sm4(body.value, _settings.encryption_key) if body.is_secret else body.value
        var_id = uuid.uuid4()
        await conn.execute(
            insert(ProjectEnvVar).values(
                id=var_id, project_id=project_id, key=body.key, value=value,
                is_secret=body.is_secret, description=body.description,
                created_by=uuid.UUID(user["id"]),
            )
        )
    return {"id": str(var_id)}


@router.put("/{var_id}")
async def update_env_var(project_id: uuid.UUID, var_id: uuid.UUID, body: UpdateEnvVarRequest,
                         user: dict = Depends(_require_access_async)):
    engine = deps.get_engine()
    async with engine.begin() as conn:
        current = (await conn.execute(
            select(ProjectEnvVar).where(
                ProjectEnvVar.id == var_id, ProjectEnvVar.project_id == project_id
            )
        )).fetchone()
        if current is None:
            raise HTTPException(status_code=404, detail="变量不存在")

        data = body.model_dump(exclude_unset=True)

        if "key" in data and data["key"] is not None and data["key"] != current.key:
            dup = (await conn.execute(
                select(ProjectEnvVar).where(
                    ProjectEnvVar.project_id == project_id,
                    ProjectEnvVar.key == data["key"],
                    ProjectEnvVar.id != var_id,
                )
            )).fetchone()
            if dup:
                raise HTTPException(status_code=409, detail="变量名已存在")

        if "value" in data:
            if current.is_secret:
                if data["value"]:
                    data["value"] = encrypt_sm4(data["value"], _settings.encryption_key)
                else:
                    data.pop("value")  # secret + 空/null = 保持原值
            elif data["value"] is None:
                data.pop("value")  # 非 secret + null = 保持原值，避免 NOT NULL 违约

        if data:
            await conn.execute(
                update(ProjectEnvVar).where(ProjectEnvVar.id == var_id).values(**data)
            )
    return {"status": "updated"}


@router.delete("/{var_id}")
async def delete_env_var(project_id: uuid.UUID, var_id: uuid.UUID,
                         user: dict = Depends(_require_access_async)):
    engine = deps.get_engine()
    async with engine.begin() as conn:
        existing = (await conn.execute(
            select(ProjectEnvVar).where(
                ProjectEnvVar.id == var_id, ProjectEnvVar.project_id == project_id
            )
        )).fetchone()
        if existing is None:
            raise HTTPException(status_code=404, detail="变量不存在")
        await conn.execute(
            delete(ProjectEnvVar).where(ProjectEnvVar.id == var_id)
        )
    return {"status": "deleted"}
