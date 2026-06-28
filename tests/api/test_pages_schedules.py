import uuid
from pathlib import Path

import pytest

from tests.conftest import clear_overrides, override_get_current_user


def test_schedules_page_route_and_template():
    from docupipe_manager.main import app

    url = app.url_path_for("schedules_list")
    assert url == "/docupipe/schedules"

    template = (Path(__file__).resolve().parents[2]
                / "docupipe_manager" / "templates" / "docupipe" / "schedules.html")
    assert template.is_file(), f"missing template: {template}"
    src = template.read_text(encoding="utf-8")
    assert '{% extends "base.html" %}' in src
    assert "DP." in src
    assert "escapeHtml" not in src


@pytest.mark.asyncio
async def test_schedules_page_requires_admin(async_client):
    override_get_current_user({"id": str(uuid.uuid4()), "role": "user"})
    r = await async_client.get("/docupipe/schedules")
    assert r.status_code == 403
    clear_overrides()
