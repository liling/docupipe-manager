import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from docupipe_manager.api.projects import _require_access_async, _require_owner_async, _get_engine
from docupipe_manager.models.project_member import ProjectMember

router = APIRouter(prefix="/api/projects/{project_id}/members", tags=["members"])


class AddMemberRequest(BaseModel):
    user_id: str
    username: str | None = None


@router.get("")
async def list_members(project_id: uuid.UUID, user: dict = Depends(_require_access_async)):
    from sqlalchemy import text
    engine = _get_engine()
    async with engine.begin() as conn:
        owner = (await conn.execute(
            text("SELECT owner_id FROM docupipe_manager.projects WHERE id = :pid"),
            {"pid": str(project_id)},
        )).fetchone()
        members = (await conn.execute(text("""
            SELECT user_id, added_by, created_at FROM docupipe_manager.project_members
            WHERE project_id = :pid ORDER BY created_at
        """), {"pid": str(project_id)})).fetchall()
    all_ids = {str(owner.owner_id)} | {str(m.user_id) for m in members}
    from docupipe_manager.main import app
    names = {}
    try:
        names = await app.state.platform_client.batch_get_users(list(all_ids))
    except Exception:
        pass
    return {
        "owner": {"user_id": str(owner.owner_id), "username": names.get(str(owner.owner_id), ""), "is_owner": True},
        "members": [
            {"user_id": str(m.user_id), "username": names.get(str(m.user_id), ""),
             "added_by": str(m.added_by), "created_at": str(m.created_at)}
            for m in members
        ],
    }


@router.post("")
async def add_member(project_id: uuid.UUID, body: AddMemberRequest,
                     user: dict = Depends(_require_owner_async)):
    from sqlalchemy import insert, select, text
    engine = _get_engine()
    async with engine.begin() as conn:
        owner = (await conn.execute(
            text("SELECT owner_id FROM docupipe_manager.projects WHERE id = :pid"),
            {"pid": str(project_id)},
        )).fetchone()
        if str(owner.owner_id) == body.user_id:
            raise HTTPException(status_code=400, detail="Owner is already in project")
        existing = (await conn.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project_id,
                ProjectMember.user_id == uuid.UUID(body.user_id),
            )
        )).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="User is already a member")
        await conn.execute(
            insert(ProjectMember).values(
                project_id=project_id,
                user_id=uuid.UUID(body.user_id),
                added_by=uuid.UUID(user["id"]),
            )
        )
    return {"status": "added", "user_id": body.user_id}


@router.delete("/{user_id}")
async def remove_member(project_id: uuid.UUID, user_id: uuid.UUID,
                        user: dict = Depends(_require_owner_async)):
    from sqlalchemy import delete, text
    engine = _get_engine()
    async with engine.begin() as conn:
        owner = (await conn.execute(
            text("SELECT owner_id FROM docupipe_manager.projects WHERE id = :pid"),
            {"pid": str(project_id)},
        )).fetchone()
        if str(owner.owner_id) == str(user_id):
            raise HTTPException(status_code=400, detail="Cannot remove owner")
        await conn.execute(
            delete(ProjectMember).where(
                ProjectMember.project_id == project_id,
                ProjectMember.user_id == user_id,
            )
        )
    return {"status": "removed"}
