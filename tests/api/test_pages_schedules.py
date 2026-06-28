from pathlib import Path


def test_schedules_page_route_and_template():
    from docupipe_manager.main import app

    url = app.url_path_for("schedules_list")
    assert url == "/docupipe/schedules"

    template = (Path(__file__).resolve().parents[2]
                / "docupipe_manager" / "templates" / "docupipe" / "schedules.html")
    assert template.is_file(), f"missing template: {template}"
    src = template.read_text(encoding="utf-8")
    assert '{% extends "base.html" %}' in src
    assert "function escapeHtml" in src
    assert "escapeHtml(" in src
