import asyncio
import json
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from docupipe_manager import deps
from docupipe_manager.auth.dependencies import get_current_user

router = APIRouter(prefix="/api/runs", tags=["runs"])

MAX_TAIL_LINES = 1000


async def _verify_run_access(run_id: uuid.UUID, user: dict):
    from sqlalchemy import select, text
    from docupipe_manager.models.pipeline_run import PipelineRun
    from docupipe_manager.models.job import Job

    engine = deps.get_engine()
    async with engine.begin() as conn:
        row = (await conn.execute(
            select(PipelineRun, Job).join(Job, PipelineRun.job_id == Job.id)
            .where(PipelineRun.id == run_id)
        )).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Run not found")
    run, job = row

    if user.get("role") != "admin":
        async with engine.begin() as conn:
            m = await conn.execute(text("""
                SELECT 1 FROM docupipe_manager.tasks t
                JOIN docupipe_manager.projects p ON p.id = t.project_id
                WHERE t.id = :tid AND p.id IN (
                    SELECT pm.project_id FROM docupipe_manager.project_members pm WHERE pm.user_id = :uid
                )
            """), {"tid": str(run.task_id), "uid": user["id"]})
            if not m.fetchone():
                raise HTTPException(status_code=404, detail="Run not found")

    return run, job


@router.get("")
async def list_runs(
    task_id: Optional[uuid.UUID] = None,
    project_id: Optional[uuid.UUID] = None,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: dict = Depends(get_current_user),
):
    from sqlalchemy import func, select, text
    from docupipe_manager.models.pipeline_run import PipelineRun
    from docupipe_manager.models.job import Job
    from docupipe_manager.models.task import Task
    from docupipe_manager.models.project import Project

    engine = deps.get_engine()

    conditions = []
    if task_id:
        conditions.append(PipelineRun.task_id == task_id)
    if project_id:
        conditions.append(
            PipelineRun.task_id.in_(
                select(Task.id).where(Task.project_id == project_id)
            )
        )
    if status:
        conditions.append(Job.status == status)

    offset = (page - 1) * page_size

    async with engine.begin() as conn:
        if user.get("role") != "admin":
            visible_tasks = [
                r[0] for r in (await conn.execute(text("""
                    SELECT t.id FROM docupipe_manager.tasks t
                    WHERE t.project_id IN (
                        SELECT pm.project_id FROM docupipe_manager.project_members pm
                        JOIN docupipe_manager.projects p ON p.id = pm.project_id
                        WHERE pm.user_id = :uid AND p.status != 'archived'
                    )
                """), {"uid": user["id"]})).fetchall()
            ]
            if not visible_tasks:
                return {"total": 0, "page": page, "page_size": page_size, "runs": []}
            conditions.append(PipelineRun.task_id.in_(visible_tasks))

        count_q = select(func.count()).select_from(PipelineRun).join(
            Job, PipelineRun.job_id == Job.id
        )
        if conditions:
            count_q = count_q.where(*conditions)
        total = (await conn.execute(count_q)).scalar() or 0

        q = select(
            PipelineRun.id.label("id"),
            PipelineRun.task_id.label("task_id"),
            Task.name.label("task_name"),
            Project.id.label("proj_id"),
            Project.name.label("project_name"),
            PipelineRun.pipeline_name.label("pipeline_name"),
            PipelineRun.mode.label("mode"),
            Job.trigger_type.label("trigger_type"),
            Job.status.label("status"),
            Job.started_at.label("started_at"),
            Job.completed_at.label("completed_at"),
            Job.created_at.label("created_at"),
        ).select_from(PipelineRun).join(
            Job, PipelineRun.job_id == Job.id
        ).join(
            Task, PipelineRun.task_id == Task.id, isouter=not bool(project_id)
        ).join(
            Project, Task.project_id == Project.id, isouter=True
        ).order_by(Job.created_at.desc())
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
                "task_name": r.task_name,
                "project_id": str(r.proj_id) if r.proj_id else None,
                "project_name": r.project_name,
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
    from docupipe_manager.models.job import Job
    from docupipe_manager.models.task import Task

    engine = deps.get_engine()
    async with engine.begin() as conn:
        row = (await conn.execute(
            select(PipelineRun, Job).join(Job, PipelineRun.job_id == Job.id)
            .where(PipelineRun.id == run_id)
        )).one_or_none()
        task = None
        if row is not None:
            run, job = row
            task = (await conn.execute(
                select(Task).where(Task.id == run.task_id)
            )).one_or_none()

    if row is None:
        return {}
    run, job = row

    def _v(x):
        return x.value if hasattr(x, "value") else x

    return {
        "id": str(run.id),
        "task_id": str(run.task_id),
        "task_name": task.name if task else None,
        "project_id": str(task.project_id) if task else None,
        "trigger_type": _v(job.trigger_type),
        "triggered_by": str(job.triggered_by) if job.triggered_by else None,
        "pipeline_name": run.pipeline_name,
        "mode": run.mode,
        "status": _v(job.status),
        "exit_code": job.exit_code,
        "command_text": job.command_text,
        "started_at": str(job.started_at) if job.started_at else None,
        "completed_at": str(job.completed_at) if job.completed_at else None,
        "error_message": job.error_message,
        "log_path": job.log_path,
        "created_at": str(job.created_at),
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
    run, job = await _verify_run_access(run_id, user)

    if not job.log_path:
        return {"lines": [], "truncated": False, "total_bytes": 0}

    try:
        with open(job.log_path, "r") as f:
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

    await _verify_run_access(run_id, user)
    runner = deps.get_runner()

    async def event_stream():
        meta = await _run_detail(run_id)
        log_path = meta.get("log_path")
        yield _sse("meta", meta)

        had_logs = False
        if runner.is_active(run_id):
            history, queue = runner.subscribe(run_id)
            try:
                if history:
                    had_logs = True
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
                    had_logs = True
                    yield _sse("log", line)
            finally:
                runner.unsubscribe(run_id, queue)

        if log_path and not had_logs:
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
            "command_text": final.get("command_text"),
            "started_at": final.get("started_at"),
            "completed_at": final.get("completed_at"),
        })

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/{run_id}/download-log")
async def download_run_log(
    run_id: uuid.UUID,
    user: dict = Depends(get_current_user),
):
    from fastapi.responses import FileResponse

    run, job = await _verify_run_access(run_id, user)

    if not job.log_path:
        raise HTTPException(status_code=404, detail="Log file not found")

    return FileResponse(job.log_path, filename=f"run-{run_id}.log")


@router.post("/{run_id}/cancel")
async def cancel_run(
    run_id: uuid.UUID,
    user: dict = Depends(get_current_user),
):
    await _verify_run_access(run_id, user)

    runner = deps.get_runner()
    try:
        await runner.cancel_run(run_id)
        return {"status": "cancelled"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
