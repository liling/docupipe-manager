import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from docupipe_manager import deps
from docupipe_manager.api.projects import _require_access_async

router = APIRouter(prefix="/api/projects/{project_id}/credentials", tags=["credentials"])


class FinalizeRequest(BaseModel):
    session_key: str
    name: str = Field(..., min_length=1, max_length=255)


class ImportRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    auth_blob: str = Field(..., min_length=1)


class RenameRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)


@router.get("")
async def list_credentials(project_id: uuid.UUID, user: dict = Depends(_require_access_async)):
    creds = await deps.get_credential().list_credentials(project_id)
    return [
        {"id": str(c.id), "name": c.name, "corp_id": c.corp_id,
         "credential_type": c.credential_type.value,
         "status": c.status.value,
         "token_expires_at": str(c.token_expires_at) if c.token_expires_at else None,
         "refresh_token_expires_at": str(c.refresh_token_expires_at) if c.refresh_token_expires_at else None,
         "created_at": str(c.created_at)}
        for c in creds
    ]


@router.post("/import")
async def import_credential(project_id: uuid.UUID, body: ImportRequest,
                            user: dict = Depends(_require_access_async)):
    try:
        cred = await deps.get_credential().create_from_import(
            project_id, body.name, body.auth_blob, uuid.UUID(user["id"])
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except IntegrityError:
        raise HTTPException(status_code=409, detail=f"凭证名「{body.name}」已存在")
    return {"id": str(cred.id), "status": "active"}


@router.post("/device-login/start")
async def start_device_login(project_id: uuid.UUID, name: str,
                              user: dict = Depends(_require_access_async)):
    return await deps.get_credential().start_device_login(project_id, name)


@router.get("/device-login/poll")
async def poll_device_login(project_id: uuid.UUID, session_key: str,
                            user: dict = Depends(_require_access_async)):
    return await deps.get_credential().poll_device_login(session_key)


@router.post("/device-login/finalize")
async def finalize_device_login(project_id: uuid.UUID, body: FinalizeRequest,
                                user: dict = Depends(_require_access_async)):
    cred = await deps.get_credential().finalize_login(
        body.session_key, body.name, uuid.UUID(user["id"]), project_id
    )
    return {"id": str(cred.id), "status": "active"}


@router.post("/{credential_id}/test")
async def test_credential(project_id: uuid.UUID, credential_id: uuid.UUID,
                          user: dict = Depends(_require_access_async)):
    try:
        return await deps.get_credential().check_status(credential_id, project_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/{credential_id}")
async def revoke_credential(project_id: uuid.UUID, credential_id: uuid.UUID,
                            user: dict = Depends(_require_access_async)):
    try:
        await deps.get_credential().revoke(credential_id, uuid.UUID(user["id"]), project_id)
        return {"status": "revoked"}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.put("/{credential_id}")
async def rename_credential(project_id: uuid.UUID, credential_id: uuid.UUID,
                            body: RenameRequest, user: dict = Depends(_require_access_async)):
    try:
        cred = await deps.get_credential().rename_credential(
            credential_id, body.name, project_id
        )
        return {"id": str(cred.id), "name": cred.name}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except IntegrityError:
        raise HTTPException(status_code=409, detail=f"凭证名「{body.name}」已存在")
