import logging
import uuid

import httpx

from docupipe_manager.platform.config import PlatformSettings

logger = logging.getLogger(__name__)


class XinyiPlatformClient:
    """Async client for xinyi-platform internal + OAuth2 endpoints."""

    def __init__(self, settings: PlatformSettings, http_client: httpx.AsyncClient | None = None):
        self._settings = settings
        self._http = http_client or httpx.AsyncClient(timeout=settings.request_timeout_seconds)
        self._client_secret = settings.oauth_client_secret

    async def _post_json(self, path: str, body: dict, *, with_client_auth: bool = True) -> dict | None:
        url = f"{self._settings.platform_url}{path}"
        headers = {"Content-Type": "application/json"}
        if with_client_auth:
            headers["X-Client-Id"] = self._settings.oauth_client_id
            headers["X-Client-Secret"] = self._client_secret
        try:
            resp = await self._http.post(url, json=body, headers=headers)
            if resp.status_code >= 400:
                logger.warning("platform %s returned %s: %s", path, resp.status_code, resp.text[:200])
                return None
            return resp.json()
        except Exception as e:
            logger.warning("platform %s failed: %s", path, e)
            return None

    async def _get_json(self, path: str, *, with_client_auth: bool = True) -> dict | None:
        url = f"{self._settings.platform_url}{path}"
        headers = {}
        if with_client_auth:
            headers["X-Client-Id"] = self._settings.oauth_client_id
            headers["X-Client-Secret"] = self._client_secret
        try:
            resp = await self._http.get(url, headers=headers)
            if resp.status_code >= 400:
                return None
            return resp.json()
        except Exception as e:
            logger.warning("platform GET %s failed: %s", path, e)
            return None

    async def exchange_oauth_code(self, code: str, redirect_uri: str) -> dict | None:
        body = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": self._settings.oauth_client_id,
            "client_secret": self._client_secret,
            "redirect_uri": redirect_uri,
        }
        return await self._post_json("/oauth/token", body, with_client_auth=False)

    async def refresh_token(self, raw_refresh: str) -> dict | None:
        body = {
            "grant_type": "refresh_token",
            "refresh_token": raw_refresh,
            "client_id": self._settings.oauth_client_id,
            "client_secret": self._client_secret,
        }
        return await self._post_json("/oauth/token", body, with_client_auth=False)

    async def revoke_token(self, raw_token: str) -> None:
        await self._post_json("/oauth/revoke", {"token": raw_token}, with_client_auth=False)

    async def batch_get_users(self, user_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict | None]:
        if not user_ids:
            return {}
        body = {"ids": [str(u) for u in user_ids], "fields": ["username", "display_name", "email", "role"]}
        result = await self._post_json("/internal/users/batch-get", body)
        if result is None:
            return {uid: None for uid in user_ids}
        raw = result.get("users", {})
        out: dict[uuid.UUID, dict | None] = {}
        for uid in user_ids:
            v = raw.get(str(uid))
            out[uid] = v
        return out

    async def push_audit(self, event: dict) -> None:
        try:
            await self._post_json("/internal/audit", event)
        except Exception as e:
            logger.warning("push_audit failed (non-blocking): %s", e)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.aclose()
