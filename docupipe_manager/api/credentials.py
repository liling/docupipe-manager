import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from docupipe_manager.api.projects import _require_access_async

router = APIRouter(prefix="/api/projects/{project_id}/credentials", tags=["credentials"])


class FinalizeRequest(BaseModel):
    session_key: str
    name: str = Field(..., min_length=1, max_length=255)


@router.get("")
async def list_credentials(project_id: uuid.UUID, user: dict = Depends(_require_access_async)):
    from docupipe_manager.main import app
    creds = await app.state.credential.list_credentials(project_id)
    return [
        {"id": str(c.id), "name": c.name, "corp_id": c.corp_id, "status": c.status.value,
         "token_expires_at": str(c.token_expires_at) if c.token_expires_at else None,
         "created_at": str(c.created_at)}
        for c in creds if c.status != "revoked"
    ]


@router.post("/device-login/start")
async def start_device_login(project_id: uuid.UUID, name: str,
                             user: dict = Depends(_require_access_async)):
    from docupipe_manager.main import app
    return await app.state.credential.start_device_login(project_id, name)


@router.get("/device-login/poll")
async def poll_device_login(project_id: uuid.UUID, session_key: str,
                            user: dict = Depends(_require_access_async)):
    from docupipe_manager.main import app
    return await app.state.credential.poll_device_login(session_key)


@router.post("/device-login/finalize")
async def finalize_device_login(project_id: uuid.UUID, body: FinalizeRequest,
                                user: dict = Depends(_require_access_async)):
    from docupipe_manager.main import app
    cred = await app.state.credential.finalize_login(
        body.session_key, body.name, uuid.UUID(user["id"]), project_id
    )
    return {"id": str(cred.id), "status": "active"}


@router.get("/{credential_id}/status")
async def check_status(project_id: uuid.UUID, credential_id: uuid.UUID,
                       user: dict = Depends(_require_access_async)):
    from docupipe_manager.main import app
    try:
        return await app.state.credential.check_status(credential_id, project_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/{credential_id}")
async def revoke_credential(project_id: uuid.UUID, credential_id: uuid.UUID,
                            user: dict = Depends(_require_access_async)):
    from docupipe_manager.main import app
    try:
        await app.state.credential.revoke(credential_id, uuid.UUID(user["id"]), project_id)
        return {"status": "revoked"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
