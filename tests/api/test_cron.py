"""Tests for cron preview API."""
import uuid
from unittest.mock import patch

import pytest

from tests.conftest import override_get_current_user, clear_overrides


@pytest.mark.asyncio
async def test_preview_valid_cron(async_client):
    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    r = await async_client.post("/docupipe/api/cron/preview", json={"cron": "0 3 * * *"})
    assert r.status_code == 200
    data = r.json()
    assert data["valid"] is True
    assert len(data["next_runs"]) == 5
    runs = data["next_runs"]
    assert runs == sorted(runs)
    assert "+08:00" in runs[0]
    clear_overrides()


@pytest.mark.asyncio
async def test_preview_invalid_cron(async_client):
    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    r = await async_client.post("/docupipe/api/cron/preview", json={"cron": "99 * * * *"})
    assert r.status_code == 200
    data = r.json()
    assert data["valid"] is False
    assert "error" in data
    clear_overrides()


@pytest.mark.asyncio
async def test_preview_rejects_six_fields(async_client):
    override_get_current_user({"id": str(uuid.uuid4()), "role": "admin"})
    r = await async_client.post("/docupipe/api/cron/preview", json={"cron": "0 0 3 * * *"})
    assert r.status_code == 200
    assert r.json()["valid"] is False
    clear_overrides()


@pytest.mark.asyncio
async def test_preview_requires_auth(async_client):
    r = await async_client.post("/docupipe/api/cron/preview", json={"cron": "0 3 * * *"})
    assert r.status_code == 401
