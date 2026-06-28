from fastapi import APIRouter, Depends

from docupipe_manager import deps
from docupipe_manager.auth.dependencies import require_admin

router = APIRouter(prefix="/api/schedules", tags=["schedules"])


@router.get("")
async def list_schedules(user: dict = Depends(require_admin)):
    scheduler = deps.get_scheduler()
    settings = deps.get_settings()
    items = await scheduler.list_schedules()
    items.sort(key=lambda x: (x["next_run_time"] is None, x["next_run_time"] or ""))
    return {
        "schedules": items,
        "count": len(items),
        "keepalive_cron": settings.credential_keepalive_cron,
        "keepalive_enabled": bool(settings.credential_keepalive_enabled),
    }
