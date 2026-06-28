from datetime import datetime
from typing import List, Optional
from zoneinfo import ZoneInfo

from croniter import croniter
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from docupipe_manager.auth.dependencies import get_current_user

router = APIRouter(prefix="/api/cron", tags=["cron"])

_TZ = ZoneInfo("Asia/Shanghai")
_NEXT_COUNT = 5


class CronPreviewRequest(BaseModel):
    cron: str


@router.post("/preview")
async def preview_cron(body: CronPreviewRequest, user: dict = Depends(get_current_user)):
    cron = body.cron.strip()
    parts = cron.split()
    if len(parts) != 5 or not croniter.is_valid(cron):
        return {"valid": False, "error": "无效的 cron 表达式（需为 5 字段）"}
    now = datetime.now(_TZ)
    itr = croniter(cron, now)
    runs = [itr.get_next(datetime).isoformat() for _ in range(_NEXT_COUNT)]
    return {"valid": True, "next_runs": runs}
