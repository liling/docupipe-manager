from typing import Optional

from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from jose import JWTError

from docupipe_manager.auth.session import SELF_AUDIENCE, decode_access_token
from docupipe_manager.config import Settings

SESSION_COOKIE = "docupipe_session"


def _get_settings() -> Settings:
    return Settings()


def _extract_token(cookie_token: Optional[str], authorization: Optional[str]) -> Optional[str]:
    if cookie_token:
        return cookie_token
    if authorization and authorization.startswith("Bearer "):
        return authorization[7:]
    return None


async def get_current_user(
    request: Request,
    docupipe_session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE),
    authorization: Optional[str] = Header(default=None),
    settings: Settings = Depends(_get_settings),
) -> dict:
    token = _extract_token(docupipe_session, authorization)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"Location": "/auth/login-redirect"},
        )
    try:
        payload = decode_access_token(token, settings.jwt_secret, audience=SELF_AUDIENCE)
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session",
            headers={"Location": "/auth/login-redirect"},
        )
    if payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")

    return {
        "id": payload["sub"],
        "username": payload.get("username", ""),
        "role": payload.get("role", "user"),
    }


async def get_current_user_or_none(
    request: Request,
    docupipe_session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE),
    authorization: Optional[str] = Header(default=None),
    settings: Settings = Depends(_get_settings),
) -> dict | None:
    try:
        return await get_current_user(request, docupipe_session, authorization, settings)
    except HTTPException:
        return None


async def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required")
    return user
