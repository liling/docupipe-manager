"""pytest configuration for docupipe-manager.

Sets required env vars before app import, and provides mock fixtures
for API and service layer tests.
"""
import os
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Set test env vars before any app imports
os.environ.setdefault("DOCUPIPE_MANAGER_DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("DOCUPIPE_MANAGER_JWT_SECRET", "test-jwt-secret-thirty-two-chars!!")
os.environ.setdefault("DOCUPIPE_MANAGER_ENCRYPTION_KEY", "a" * 32)
os.environ.setdefault("DOCUPIPE_MANAGER_PLATFORM_URL", "http://platform:8000")
os.environ.setdefault("DOCUPIPE_MANAGER_OAUTH_CLIENT_ID", "dm-prod")
os.environ.setdefault("DOCUPIPE_MANAGER_OAUTH_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("DOCUPIPE_MANAGER_OAUTH_REDIRECT_URI", "/auth/callback")
os.environ.setdefault("DOCUPIPE_MANAGER_BASE_URL", "http://localhost:8002")
os.environ.setdefault("DOCUPIPE_MANAGER_DATA_DIR", "/tmp/docupipe-test")


def _make_test_app(state: dict | None = None):
    """Create a FastAPI app instance for tests — lifespan disabled, app.state pre-set."""
    from docupipe_manager.main import app
    from docupipe_manager.platform.cache import UserLRUCache
    app.router.lifespan_context = None
    app.state.platform_client = AsyncMock()
    app.state.platform_client.exchange_oauth_code = AsyncMock(return_value={
        "access_token": "test-access-token",
        "refresh_token": "test-refresh-token",
    })
    app.state.platform_client.revoke_token = AsyncMock(return_value=None)
    app.state.platform_client.refresh_token = AsyncMock(return_value={
        "access_token": "new-access-token",
        "refresh_token": "new-refresh-token",
    })
    app.state.user_cache = UserLRUCache(ttl_seconds=30)
    if state:
        for k, v in state.items():
            setattr(app.state, k, v)
    return app


@pytest_asyncio.fixture
async def async_client():
    """Return an async test client with lifespan disabled and mocks set up."""
    app = _make_test_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
def mock_session():
    """Return a mock AsyncSession."""
    session = AsyncMock()
    session.__aenter__.return_value = session
    session.__aexit__.return_value = None
    return session


@pytest.fixture
def mock_platform_client():
    """Return a mock XinyiPlatformClient."""
    client = MagicMock()
    client.exchange_oauth_code = AsyncMock(return_value={
        "access_token": "test-access-token",
        "refresh_token": "test-refresh-token",
    })
    client.refresh_token = AsyncMock(return_value={
        "access_token": "new-access-token",
        "refresh_token": "new-refresh-token",
    })
    client.revoke_token = AsyncMock(return_value=None)
    client.push_audit = AsyncMock(return_value=None)
    client.batch_get_users = AsyncMock(return_value={})
    return client


@pytest.fixture
def test_user() -> dict:
    return {
        "id": str(uuid.uuid4()),
        "username": "admin",
        "role": "admin",
    }


def override_get_current_user(user: dict | None = None):
    from docupipe_manager.auth.dependencies import get_current_user
    from docupipe_manager.main import app
    if user is None:
        user = {"id": str(uuid.uuid4()), "username": "admin", "role": "admin"}
    app.dependency_overrides[get_current_user] = lambda: user


def clear_overrides():
    from docupipe_manager.main import app
    app.dependency_overrides.clear()
