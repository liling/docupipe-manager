
from fastapi import APIRouter, Depends

from docupipe_manager.auth.dependencies import get_current_user

router = APIRouter(prefix="/api", tags=["stats"])


@router.get("/stats")
async def get_stats(
    user: dict = Depends(get_current_user),
):
    from docupipe_manager.main import app
    from sqlalchemy import text as sqla_text

    engine = app.state.engine

    async with engine.begin() as conn:
        if user.get("role") == "admin":
            project_count = (await conn.execute(
                sqla_text("SELECT COUNT(*) FROM docupipe_manager.projects WHERE status = 'active'")
            )).scalar() or 0
        else:
            project_count = (await conn.execute(
                sqla_text("""
                    SELECT COUNT(*) FROM docupipe_manager.projects
                    WHERE status = 'active' AND id IN (
                        SELECT pm.project_id FROM docupipe_manager.project_members pm WHERE pm.user_id = :uid
                    )
                """),
                {"uid": user["id"]},
            )).scalar() or 0

        credential_count = (await conn.execute(
            sqla_text("SELECT COUNT(*) FROM docupipe_manager.dws_credentials WHERE status = 'active'")
        )).scalar() or 0

        today_runs = (await conn.execute(
            sqla_text("SELECT COUNT(*) FROM docupipe_manager.pipeline_runs WHERE created_at::date = CURRENT_DATE")
        )).scalar() or 0

        failed_today = (await conn.execute(
            sqla_text("SELECT COUNT(*) FROM docupipe_manager.pipeline_runs "
                      "WHERE created_at::date = CURRENT_DATE AND status = 'failed'")
        )).scalar() or 0

        recent_failed = (await conn.execute(
            sqla_text("""
                SELECT r.id, r.task_id, r.pipeline_name, r.created_at, r.error_message
                FROM docupipe_manager.pipeline_runs r
                WHERE r.status = 'failed' AND r.completed_at > NOW() - INTERVAL '1 hour'
                ORDER BY r.created_at DESC LIMIT 5
            """)
        )).fetchall()

    return {
        "project_count": project_count,
        "active_credentials": credential_count,
        "today_runs": today_runs,
        "failed_today": failed_today,
        "recent_failures": [
            {
                "id": str(r.id),
                "task_id": str(r.task_id),
                "pipeline_name": r.pipeline_name,
                "created_at": str(r.created_at) if r.created_at else None,
                "error_message": r.error_message[:200] if r.error_message else None,
            }
            for r in recent_failed
        ],
    }
