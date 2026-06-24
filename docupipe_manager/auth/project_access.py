"""项目级权限依赖：access(Owner/Member/admin) 与 owner(admin/Owner)。"""
import uuid


def get_engine():
    from docupipe_manager.main import app
    return app.state.engine


async def is_project_owner(project_id: uuid.UUID, user: dict) -> bool:
    if user.get("role") == "admin":
        return True
    from sqlalchemy import text
    engine = get_engine()
    async with engine.begin() as conn:
        row = (await conn.execute(
            text("SELECT 1 FROM docupipe_manager.project_members "
                 "WHERE project_id = :pid AND user_id = :uid AND role = 'owner'"),
            {"pid": str(project_id), "uid": str(user["id"])},
        )).fetchone()
    return row is not None


async def is_project_member(project_id: uuid.UUID, user: dict) -> bool:
    if user.get("role") == "admin":
        return True
    from sqlalchemy import text
    engine = get_engine()
    async with engine.begin() as conn:
        row = (await conn.execute(
            text("SELECT 1 FROM docupipe_manager.project_members WHERE project_id = :pid AND user_id = :uid"),
            {"pid": str(project_id), "uid": str(user["id"])},
        )).fetchone()
    return row is not None
