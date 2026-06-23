import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from docupipe_manager.auth.dependencies import SESSION_COOKIE, get_current_user_or_none
from docupipe_manager.auth.oauth_state import generate_state, verify_state
from docupipe_manager.config import Settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE = "docupipe_refresh"
STATE_COOKIE = "docupipe_oauth_state"


def _get_settings() -> Settings:
    return Settings()


@router.get("/login-redirect")
async def login_redirect(
    request: Request,
    return_to: str = "/docupipe/projects",
    settings: Settings = Depends(_get_settings),
):
    state = generate_state()
    params = (
        f"response_type=code"
        f"&client_id={settings.oauth_client_id}"
        f"&redirect_uri={settings.oauth_redirect_uri}"
        f"&state={state}"
        f"&return_to={return_to}"
    )
    redirect_url = f"{settings.platform_url}/oauth/authorize?{params}"
    response = RedirectResponse(url=redirect_url, status_code=302)
    max_age = 600
    response.set_cookie(
        key=STATE_COOKIE,
        value=state,
        max_age=max_age,
        httponly=True,
        secure=settings.base_url.startswith("https"),
        samesite="lax",
    )
    return response


@router.get("/callback")
async def auth_callback(
    request: Request,
    response: Response,
    code: str = "",
    state: str = "",
    return_to: str = "/docupipe/projects",
    oauth_state: Optional[str] = Cookie(default=None, alias=STATE_COOKIE),
    settings: Settings = Depends(_get_settings),
):
    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code")
    if not verify_state(oauth_state or "", state):
        raise HTTPException(status_code=400, detail="Invalid state parameter")

    response.delete_cookie(key=STATE_COOKIE)

    from docupipe_manager.main import app
    client = app.state.platform_client

    token_result = await client.exchange_oauth_code(code, settings.oauth_redirect_uri)
    if token_result is None:
        raise HTTPException(status_code=401, detail="Failed to exchange authorization code")

    access_token = token_result.get("access_token", "")
    refresh_token = token_result.get("refresh_token", "")

    redirect = RedirectResponse(url=return_to, status_code=302)
    secure = settings.base_url.startswith("https")

    redirect.set_cookie(
        key=SESSION_COOKIE,
        value=access_token,
        max_age=settings.access_token_ttl_seconds,
        httponly=True,
        secure=secure,
        samesite="lax",
    )
    if refresh_token:
        redirect.set_cookie(
            key=REFRESH_COOKIE,
            value=refresh_token,
            max_age=settings.refresh_token_ttl_days * 86400,
            httponly=True,
            secure=secure,
            samesite="lax",
        )

    return redirect


@router.post("/refresh")
async def auth_refresh(
    request: Request,
    response: Response,
    docupipe_refresh: Optional[str] = Cookie(default=None, alias=REFRESH_COOKIE),
    settings: Settings = Depends(_get_settings),
):
    if not docupipe_refresh:
        return RedirectResponse(url="/auth/login-redirect")

    from docupipe_manager.main import app
    client = app.state.platform_client

    token_result = await client.refresh_token(docupipe_refresh)
    if token_result is None:
        response.delete_cookie(key=SESSION_COOKIE)
        response.delete_cookie(key=REFRESH_COOKIE)
        raise HTTPException(status_code=401, detail="Refresh token expired or revoked")

    new_access = token_result.get("access_token", "")
    new_refresh = token_result.get("refresh_token", "")

    secure = settings.base_url.startswith("https")
    response.set_cookie(
        key=SESSION_COOKIE,
        value=new_access,
        max_age=settings.access_token_ttl_seconds,
        httponly=True,
        secure=secure,
        samesite="lax",
    )
    if new_refresh:
        response.set_cookie(
            key=REFRESH_COOKIE,
            value=new_refresh,
            max_age=settings.refresh_token_ttl_days * 86400,
            httponly=True,
            secure=secure,
            samesite="lax",
        )

    return {"status": "ok"}


@router.post("/logout")
async def auth_logout(
    request: Request,
    response: Response,
    docupipe_session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE),
    docupipe_refresh: Optional[str] = Cookie(default=None, alias=REFRESH_COOKIE),
    settings: Settings = Depends(_get_settings),
):
    from docupipe_manager.main import app
    client = app.state.platform_client

    if docupipe_refresh:
        await client.revoke_token(docupipe_refresh)

    response.delete_cookie(key=SESSION_COOKIE)
    response.delete_cookie(key=REFRESH_COOKIE)

    return {"status": "ok"}


# ── Development helper (not for production) ──────────────────────────────


@router.get("/dev-login")
async def dev_login(
    request: Request,
    username: str = "admin",
    role: str = "admin",
    settings: Settings = Depends(_get_settings),
):
    """Set a fake session cookie for local development (no xinyi-platform required)."""
    from jose import jwt as jose_jwt
    from datetime import datetime, timedelta, timezone

    payload = {
        "sub": "00000000-0000-0000-0000-000000000001",
        "username": username,
        "role": role,
        "type": "access",
        "aud": "docupipe-prod",
        "iss": "xinyi-platform",
        "exp": datetime.now(timezone.utc) + timedelta(hours=24),
    }
    token = jose_jwt.encode(payload, settings.jwt_secret, algorithm="HS256")

    redirect = RedirectResponse(url="/docupipe/projects", status_code=302)
    secure = settings.base_url.startswith("https")
    redirect.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=86400,
        httponly=True,
        secure=secure,
        samesite="lax",
    )
    return redirect
