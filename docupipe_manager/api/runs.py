import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from docupipe_manager.auth.dependencies import require_admin
from docupipe_manager.models.pipeline_run import RunStatus

router = APIRouter(prefix="/admin/api/docupipe/runs", tags=["runs"])

MAX_TAIL_LINES = 1000


@router.get("")
async def list_runs(
    project_id: Optional[uuid.UUID] = None,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: dict = Depends(require_admin),
):
    from docupipe_manager.main import app
    from sqlalchemy import select, func, text
    from docupipe_manager.models.pipeline_run import PipelineRun

    conditions = []
    if project_id:
        conditions.append(PipelineRun.project_id == project_id)
    if status:
        conditions.append(PipelineRun.status == status)

    offset = (page - 1) * page_size

    async with app.state.engine.begin() as conn:
        count_q = select(func.count()).select_from(PipelineRun)
        if conditions:
            count_q = count_q.where(*conditions)
        total = (await conn.execute(count_q)).scalar() or 0

        q = select(PipelineRun).order_by(PipelineRun.created_at.desc())
        if conditions:
            q = q.where(*conditions)
        q = q.offset(offset).limit(page_size)
        rows = (await conn.execute(q)).fetchall()

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "runs": [
            {
                "id": str(r.id),
                "project_id": str(r.project_id),
                "trigger_type": r.trigger_type.value if hasattr(r.trigger_type, "value") else r.trigger_type,
                "pipeline_name": r.pipeline_name,
                "mode": r.mode,
                "status": r.status.value if hasattr(r.status, "value") else r.status,
                "started_at": str(r.started_at) if r.started_at else None,
                "completed_at": str(r.completed_at) if r.completed_at else None,
                "created_at": str(r.created_at),
            }
            for r in rows
        ],
    }


@router.get("/{run_id}")
async def get_run(
    run_id: uuid.UUID,
    user: dict = Depends(require_admin),
):
    from docupipe_manager.main import app
    from sqlalchemy import select
    from docupipe_manager.models.pipeline_run import PipelineRun

    async with app.state.engine.begin() as conn:
        result = await conn.execute(
            select(PipelineRun).where(PipelineRun.id == run_id)
        )
        run = result.scalar_one_or_none()
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")

    return {
        "id": str(run.id),
        "project_id": str(run.project_id),
        "trigger_type": run.trigger_type.value if hasattr(run.trigger_type, "value") else run.trigger_type,
        "triggered_by": str(run.triggered_by) if run.triggered_by else None,
        "pipeline_name": run.pipeline_name,
        "mode": run.mode,
        "status": run.status.value if hasattr(run.status, "value") else run.status,
        "exit_code": run.exit_code,
        "started_at": str(run.started_at) if run.started_at else None,
        "completed_at": str(run.completed_at) if run.completed_at else None,
        "error_message": run.error_message,
        "log_path": run.log_path,
        "created_at": str(run.created_at),
    }


@router.get("/{run_id}/log")
async def get_run_log(
    run_id: uuid.UUID,
    tail: int = Query(200, ge=1, le=MAX_TAIL_LINES),
    user: dict = Depends(require_admin),
):
    from docupipe_manager.main import app
    from sqlalchemy import select
    from docupipe_manager.models.pipeline_run import PipelineRun

    async with app.state.engine.begin() as conn:
        result = await conn.execute(
            select(PipelineRun).where(PipelineRun.id == run_id)
        )
        run = result.scalar_one_or_none()
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")

    if not run.log_path:
        return {"lines": [], "truncated": False, "total_bytes": 0}

    try:
        with open(run.log_path, "r") as f:
            lines = f.readlines()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Log file not found")

    total_bytes = sum(len(l) for l in lines)
    tail_lines = lines[-tail:]
    return {
        "lines": [l.rstrip("\n") for l in tail_lines],
        "truncated": len(lines) > tail,
        "total_bytes": total_bytes,
    }


@router.get("/{run_id}/download-log")
async def download_run_log(
    run_id: uuid.UUID,
    user: dict = Depends(require_admin),
):
    from docupipe_manager.main import app
    from fastapi.responses import FileResponse
    from sqlalchemy import select
    from docupipe_manager.models.pipeline_run import PipelineRun

    async with app.state.engine.begin() as conn:
        result = await conn.execute(
            select(PipelineRun).where(PipelineRun.id == run_id)
        )
        run = result.scalar_one_or_none()
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")

    if not run.log_path:
        raise HTTPException(status_code=404, detail="Log file not found")

    return FileResponse(run.log_path, filename=f"run-{run_id}.log")


@router.post("/{run_id}/cancel")
async def cancel_run(
    run_id: uuid.UUID,
    user: dict = Depends(require_admin),
):
    from docupipe_manager.main import app
    try:
        await app.state.runner.cancel_run(run_id)
        return {"status": "cancelled"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
