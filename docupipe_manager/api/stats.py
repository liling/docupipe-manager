import uuid
from typing import Optional

from fastapi import APIRouter, Depends

from docupipe_manager.auth.dependencies import require_admin

router = APIRouter(prefix="/admin/api/docupipe", tags=["stats"])


@router.get("/stats")
async def get_stats(
    user: dict = Depends(require_admin),
):
    from docupipe_manager.main import app
    from sqlalchemy import select, func, text

    async with app.state.engine.begin() as conn:
        project_count = (await conn.execute(
            text("SELECT COUNT(*) FROM docupipe_manager.docupipe_projects WHERE status = 'active'")
        )).scalar() or 0

        credential_count = (await conn.execute(
            text("SELECT COUNT(*) FROM docupipe_manager.dws_credentials WHERE status = 'active'")
        )).scalar() or 0

        today_runs = (await conn.execute(
            text("SELECT COUNT(*) FROM docupipe_manager.pipeline_runs WHERE created_at::date = CURRENT_DATE")
        )).scalar() or 0

        failed_today = (await conn.execute(
            text("SELECT COUNT(*) FROM docupipe_manager.pipeline_runs "
                 "WHERE created_at::date = CURRENT_DATE AND status = 'failed'")
        )).scalar() or 0

        recent_failed = (await conn.execute(
            text("SELECT id, project_id, pipeline_name, created_at, error_message "
                 "FROM docupipe_manager.pipeline_runs "
                 "WHERE status = 'failed' AND completed_at > NOW() - INTERVAL '1 hour' "
                 "ORDER BY created_at DESC LIMIT 5")
        )).fetchall()

    return {
        "project_count": project_count,
        "active_credentials": credential_count,
        "today_runs": today_runs,
        "failed_today": failed_today,
        "recent_failures": [
            {
                "id": str(r.id),
                "project_id": str(r.project_id),
                "pipeline_name": r.pipeline_name,
                "created_at": str(r.created_at) if r.created_at else None,
                "error_message": r.error_message[:200] if r.error_message else None,
            }
            for r in recent_failed
        ],
    }
