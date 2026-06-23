import asyncio
import json
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from docupipe_manager.api.projects import _get_engine
from docupipe_manager.auth.dependencies import get_current_user

router = APIRouter(prefix="/api/runs", tags=["runs"])

MAX_TAIL_LINES = 1000


async def _verify_run_access(run_id: uuid.UUID, user: dict):
    from sqlalchemy import select, text
    from docupipe_manager.models.pipeline_run import PipelineRun

    engine = _get_engine()
    async with engine.begin() as conn:
        result = await conn.execute(
            select(PipelineRun).where(PipelineRun.id == run_id)
        )
        run = result.one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    if user.get("role") != "admin":
        async with engine.begin() as conn:
            row = await conn.execute(text("""
                SELECT 1 FROM docupipe_manager.tasks t
                JOIN docupipe_manager.projects p ON p.id = t.project_id
                WHERE t.id = :tid AND (
                    p.owner_id = :uid
                    OR p.id IN (
                        SELECT pm.project_id FROM docupipe_manager.project_members pm WHERE pm.user_id = :uid
                    )
                )
            """), {"tid": str(run.task_id), "uid": user["id"]})
            if not row.fetchone():
                raise HTTPException(status_code=404, detail="Run not found")

    return run


@router.get("")
async def list_runs(
    task_id: Optional[uuid.UUID] = None,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: dict = Depends(get_current_user),
):
    from sqlalchemy import func, select, text
    from docupipe_manager.models.pipeline_run import PipelineRun

    engine = _get_engine()

    conditions = []
    if task_id:
        conditions.append(PipelineRun.task_id == task_id)
    if status:
        conditions.append(PipelineRun.status == status)

    offset = (page - 1) * page_size

    async with engine.begin() as conn:
        if user.get("role") != "admin":
            visible_tasks = [
                r[0] for r in (await conn.execute(text("""
                    SELECT t.id FROM docupipe_manager.tasks t
                    WHERE t.project_id IN (
                        SELECT id FROM docupipe_manager.projects WHERE owner_id = :uid AND status != 'archived'
                        UNION
                        SELECT pm.project_id FROM docupipe_manager.project_members pm WHERE pm.user_id = :uid
                    )
                """), {"uid": user["id"]})).fetchall()
            ]
            if not visible_tasks:
                return {"total": 0, "page": page, "page_size": page_size, "runs": []}
            conditions.append(PipelineRun.task_id.in_(visible_tasks))

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
                "task_id": str(r.task_id),
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


async def _run_detail(run_id: uuid.UUID) -> dict:
    from sqlalchemy import select
    from docupipe_manager.models.pipeline_run import PipelineRun
    from docupipe_manager.models.task import Task

    engine = _get_engine()
    async with engine.begin() as conn:
        run = (await conn.execute(
            select(PipelineRun).where(PipelineRun.id == run_id)
        )).one_or_none()
        task = None
        if run is not None:
            task = (await conn.execute(
                select(Task).where(Task.id == run.task_id)
            )).one_or_none()

    if run is None:
        return {}

    def _v(x):
        return x.value if hasattr(x, "value") else x

    return {
        "id": str(run.id),
        "task_id": str(run.task_id),
        "task_name": task.name if task else None,
        "project_id": str(task.project_id) if task else None,
        "trigger_type": _v(run.trigger_type),
        "triggered_by": str(run.triggered_by) if run.triggered_by else None,
        "pipeline_name": run.pipeline_name,
        "mode": run.mode,
        "status": _v(run.status),
        "exit_code": run.exit_code,
        "command_text": run.command_text,
        "started_at": str(run.started_at) if run.started_at else None,
        "completed_at": str(run.completed_at) if run.completed_at else None,
        "error_message": run.error_message,
        "log_path": run.log_path,
        "created_at": str(run.created_at),
    }


@router.get("/{run_id}")
async def get_run(
    run_id: uuid.UUID,
    user: dict = Depends(get_current_user),
):
    await _verify_run_access(run_id, user)
    detail = await _run_detail(run_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Run not found")
    return detail


@router.get("/{run_id}/log")
async def get_run_log(
    run_id: uuid.UUID,
    tail: int = Query(200, ge=1, le=MAX_TAIL_LINES),
    user: dict = Depends(get_current_user),
):
    run = await _verify_run_access(run_id, user)

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


def _sse(event: str, payload) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


@router.get("/{run_id}/stream")
async def stream_run(run_id: uuid.UUID, user: dict = Depends(get_current_user)):
    from fastapi.responses import StreamingResponse
    from docupipe_manager.main import app

    await _verify_run_access(run_id, user)
    runner = app.state.runner

    async def event_stream():
        meta = await _run_detail(run_id)
        log_path = meta.get("log_path")
        yield _sse("meta", meta)

        if runner.is_active(run_id):
            history, queue = runner.subscribe(run_id)
            try:
                for line in history:
                    yield _sse("log", line)
                while True:
                    try:
                        line = await asyncio.wait_for(queue.get(), timeout=15)
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    if line is None:  # sentinel
                        break
                    yield _sse("log", line)
            finally:
                runner.unsubscribe(run_id, queue)
        elif log_path:
            try:
                with open(log_path) as f:
                    for line in f:
                        yield _sse("log", line.rstrip("\n"))
            except FileNotFoundError:
                pass

        final = await _run_detail(run_id)
        yield _sse("end", {
            "status": final.get("status"),
            "exit_code": final.get("exit_code"),
        })

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/{run_id}/download-log")
async def download_run_log(
    run_id: uuid.UUID,
    user: dict = Depends(get_current_user),
):
    from fastapi.responses import FileResponse

    run = await _verify_run_access(run_id, user)

    if not run.log_path:
        raise HTTPException(status_code=404, detail="Log file not found")

    return FileResponse(run.log_path, filename=f"run-{run_id}.log")


@router.post("/{run_id}/cancel")
async def cancel_run(
    run_id: uuid.UUID,
    user: dict = Depends(get_current_user),
):
    await _verify_run_access(run_id, user)

    from docupipe_manager.main import app
    try:
        await app.state.runner.cancel_run(run_id)
        return {"status": "cancelled"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
