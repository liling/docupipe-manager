import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from docupipe_manager.auth.dependencies import require_admin
from docupipe_manager.models.dws_credential import DwsCredential

router = APIRouter(prefix="/admin/api/docupipe/credentials", tags=["credentials"])


class DeviceLoginStartResponse(BaseModel):
    session_key: str
    verification_url: str
    user_code: str


class PollResponse(BaseModel):
    status: str
    error: Optional[str] = None


class FinalizeRequest(BaseModel):
    session_key: str
    name: str = Field(..., min_length=1, max_length=255)


class CredentialResponse(BaseModel):
    id: str
    name: str
    corp_id: str
    status: str
    token_expires_at: Optional[str] = None
    refresh_token_expires_at: Optional[str] = None
    last_refreshed_at: Optional[str] = None
    created_at: str


@router.get("")
async def list_credentials(
    user: dict = Depends(require_admin),
):
    from docupipe_manager.main import app
    credentials = await app.state.credential.list_credentials()
    return [
        CredentialResponse(
            id=str(c.id), name=c.name, corp_id=c.corp_id,
            status=c.status.value,
            token_expires_at=str(c.token_expires_at) if c.token_expires_at else None,
            refresh_token_expires_at=str(c.refresh_token_expires_at) if c.refresh_token_expires_at else None,
            last_refreshed_at=str(c.last_refreshed_at) if c.last_refreshed_at else None,
            created_at=str(c.created_at),
        )
        for c in credentials
    ]


@router.post("/device-login/start")
async def start_device_login(
    name: str,
    user: dict = Depends(require_admin),
):
    from docupipe_manager.main import app
    result = await app.state.credential.start_device_login(name)
    return result


@router.get("/device-login/poll")
async def poll_device_login(
    session_key: str,
    user: dict = Depends(require_admin),
):
    from docupipe_manager.main import app
    return await app.state.credential.poll_device_login(session_key)


@router.post("/device-login/finalize")
async def finalize_device_login(
    body: FinalizeRequest,
    user: dict = Depends(require_admin),
):
    from docupipe_manager.main import app
    credential = await app.state.credential.finalize_login(
        body.session_key, body.name, uuid.UUID(user["id"]),
    )
    return {"id": str(credential.id), "status": "active"}


@router.get("/{credential_id}/status")
async def check_credential_status(
    credential_id: uuid.UUID,
    user: dict = Depends(require_admin),
):
    from docupipe_manager.main import app
    try:
        status = await app.state.credential.check_status(credential_id)
        return status
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{credential_id}/revoke")
async def revoke_credential(
    credential_id: uuid.UUID,
    user: dict = Depends(require_admin),
):
    from docupipe_manager.main import app
    try:
        await app.state.credential.revoke(credential_id, uuid.UUID(user["id"]))
        return {"status": "revoked"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
