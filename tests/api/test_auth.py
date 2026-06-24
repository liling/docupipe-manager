from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from docupipe_manager.auth.dependencies import get_current_user, require_admin
from docupipe_manager.auth.oauth_state import generate_state, verify_state


def test_generate_state_returns_hex():
    state = generate_state()
    assert isinstance(state, str)
    assert len(state) == 64
    assert all(c in "0123456789abcdef" for c in state)


def test_verify_state_valid():
    state = generate_state()
    assert verify_state(state, state) is True


def test_verify_state_invalid():
    state1 = generate_state()
    state2 = generate_state()
    assert verify_state(state1, state2) is False


def test_verify_state_empty():
    assert verify_state("", "something") is False
    assert verify_state("something", "") is False


@pytest.mark.asyncio
async def test_require_admin_allows_admin():
    user = {"role": "admin", "id": "1"}
    result = await require_admin(user)
    assert result == user


@pytest.mark.asyncio
async def test_require_admin_rejects_non_admin():
    user = {"role": "user", "id": "1"}
    with pytest.raises(HTTPException) as exc:
        await require_admin(user)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_auth_login_redirect(async_client):
    resp = await async_client.get("/auth/login-redirect", follow_redirects=False)
    assert resp.status_code == 302
    location = resp.headers.get("location", "")
    assert "/oauth/authorize" in location
    assert "response_type=code" in location
    assert "client_id=docupipe-prod" in location


@pytest.mark.asyncio
async def test_auth_login_redirect_sets_state_cookie(async_client):
    resp = await async_client.get("/auth/login-redirect", follow_redirects=False)
    assert resp.status_code == 302
    cookies = resp.cookies
    assert "docupipe_oauth_state" in cookies
    assert len(cookies["docupipe_oauth_state"]) == 64


@pytest.mark.asyncio
async def test_auth_login_redirect_custom_return_to(async_client):
    resp = await async_client.get(
        "/auth/login-redirect?return_to=/docupipe/projects/123",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "return_to=/docupipe/projects/123" in resp.headers.get("location", "")


@pytest.mark.asyncio
async def test_callback_missing_code(async_client):
    resp = await async_client.get("/auth/callback?state=abc")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Missing authorization code"


@pytest.mark.asyncio
async def test_callback_invalid_state(async_client):
    resp = await async_client.get(
        "/auth/callback?code=abc&state=wrong",
        cookies={"docupipe_oauth_state": "different"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Invalid state parameter"


@pytest.mark.asyncio
async def test_logout_clears_cookies(async_client):
    resp = await async_client.post(
        "/auth/logout",
        cookies={
            "docupipe_session": "test-session",
            "docupipe_refresh": "test-refresh",
        },
    )
    assert resp.status_code == 303
    location = resp.headers.get("location", "")
    assert "logout" in location
    assert "platform" in location
    set_cookie = resp.headers.get("set-cookie", "")
    assert "docupipe_session=" in set_cookie or "Max-Age=0" in set_cookie


@pytest.mark.asyncio
async def test_health(async_client):
    resp = await async_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
