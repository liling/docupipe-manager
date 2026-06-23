"""项目级权限依赖：access(Owner/Member/admin) 与 owner(admin/Owner)。"""
import uuid

from fastapi import Depends, HTTPException, status

from docupipe_manager.auth.dependencies import get_current_user


def get_engine():
    from docupipe_manager.main import app
    return app.state.engine


async def is_project_owner(project_id: uuid.UUID, user: dict) -> bool:
    if user.get("role") == "admin":
        return True
    from sqlalchemy import select, text
    engine = get_engine()
    async with engine.begin() as conn:
        row = (await conn.execute(
            text("SELECT owner_id FROM docupipe_manager.projects WHERE id = :pid AND status != 'archived'"),
            {"pid": str(project_id)},
        )).fetchone()
    if row is None:
        return False
    return str(row.owner_id) == str(user["id"])


async def is_project_member(project_id: uuid.UUID, user: dict) -> bool:
    if user.get("role") == "admin":
        return True
    if await is_project_owner(project_id, user):
        return True
    from sqlalchemy import text
    engine = get_engine()
    async with engine.begin() as conn:
        row = (await conn.execute(
            text("SELECT 1 FROM docupipe_manager.project_members WHERE project_id = :pid AND user_id = :uid"),
            {"pid": str(project_id), "uid": str(user["id"])},
        )).fetchone()
    return row is not None


def require_project_access(project_id: uuid.UUID):
    """依赖工厂：admin 或 Owner 或 Member 通过，否则 403；项目不存在或归档返回 404。"""
    async def _dep(user: dict = Depends(get_current_user)) -> dict:
        if user.get("role") == "admin":
            return user
        if await is_project_owner(project_id, user):
            return user
        if await is_project_member(project_id, user):
            return user
        from sqlalchemy import text
        engine = get_engine()
        async with engine.begin() as conn:
            exists = (await conn.execute(
                text("SELECT 1 FROM docupipe_manager.projects WHERE id = :pid AND status != 'archived'"),
                {"pid": str(project_id)},
            )).fetchone()
        if exists is None:
            raise HTTPException(status_code=404, detail="Project not found")
        raise HTTPException(status_code=403, detail="Not a project member")
    return _dep


def require_project_owner(project_id: uuid.UUID):
    """依赖工厂：admin 或 Owner 通过，否则 403/404。"""
    async def _dep(user: dict = Depends(get_current_user)) -> dict:
        if await is_project_owner(project_id, user):
            return user
        from sqlalchemy import text
        engine = get_engine()
        async with engine.begin() as conn:
            exists = (await conn.execute(
                text("SELECT 1 FROM docupipe_manager.projects WHERE id = :pid AND status != 'archived'"),
                {"pid": str(project_id)},
            )).fetchone()
        if exists is None:
            raise HTTPException(status_code=404, detail="Project not found")
        raise HTTPException(status_code=403, detail="Project owner required")
    return _dep
