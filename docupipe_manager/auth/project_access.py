import uuid

from docupipe_manager import deps


async def is_project_owner(project_id: uuid.UUID, user: dict) -> bool:
    if user.get("role") == "admin":
        return True
    from sqlalchemy import text
    engine = deps.get_engine()
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
    engine = deps.get_engine()
    async with engine.begin() as conn:
        row = (await conn.execute(
            text("SELECT 1 FROM docupipe_manager.project_members WHERE project_id = :pid AND user_id = :uid"),
            {"pid": str(project_id), "uid": str(user["id"])},
        )).fetchone()
    return row is not None
