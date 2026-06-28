"""Regression tests for XSS fix — DOM API rewrite."""
from pathlib import Path

import pytest

from tests.conftest import clear_overrides, override_get_current_user

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_JS_DIR = _PROJECT_ROOT / "docupipe_manager" / "static" / "js"
_TPL_DIR = _PROJECT_ROOT / "docupipe_manager" / "templates" / "docupipe"


def _src(name):
    return (_JS_DIR / name).read_text(encoding="utf-8") if (_JS_DIR / name).exists() else ""


def _tpl_src(name):
    return (_TPL_DIR / name).read_text(encoding="utf-8") if (_TPL_DIR / name).exists() else ""


class TestDomHelperLoaded:
    def test_dom_js_exists(self):
        assert (_JS_DIR / "dom.js").is_file()

    def test_base_html_loads_dom_js(self):
        base = (_PROJECT_ROOT / "docupipe_manager" / "templates" / "base.html").read_text(encoding="utf-8")
        assert "dom.js" in base
        assert 'src="/docupipe/static/js/dom.js"' in base

    def test_dom_js_exports_DP(self):
        src = _src("dom.js")
        assert "window.DP" in src
        assert "el(" in src
        assert "fill(" in src
        assert "clear(" in src


class TestProjectsListXSSFixed:
    def test_no_innerHTML_with_user_data(self):
        src = _tpl_src("projects.html")
        assert "innerHTML" not in src, "projects.html still uses innerHTML — XSS vector"

    def test_uses_DP_helpers(self):
        src = _tpl_src("projects.html")
        assert "DP." in src, "projects.html should use DP helpers for safe rendering"

    def test_no_dollar_interpolation_in_text_nodes(self):
        src = _tpl_src("projects.html")
        assert "${p.name}" not in src, "projects.html still interpolates p.name — XSS vector"
        assert "${p.description}" not in src, "projects.html still interpolates p.description — XSS vector"


class TestRunsListXSSFixed:
    def test_no_innerHTML_with_user_data(self):
        src = _tpl_src("runs.html")
        assert "innerHTML" not in src, "runs.html still uses innerHTML — XSS vector"

    def test_uses_DP_helpers(self):
        src = _tpl_src("runs.html")
        assert "DP." in src, "runs.html should use DP helpers for safe rendering"


class TestProjectDetailXSSFixed:
    def test_no_innerHTML(self):
        src = _src("project_detail.js")
        assert "innerHTML" not in src, "project_detail.js still uses innerHTML — XSS vector"

    def test_uses_DP_helpers(self):
        src = _src("project_detail.js")
        assert "DP." in src, "project_detail.js should use DP helpers"

    def test_no_inline_onchange_with_user_id(self):
        src = _src("project_detail.js")
        assert 'onchange="changeMemberRole' not in src, \
            "inline onchange handler with user data — attribute XSS vector"

    def test_no_template_literal_user_data_in_attrs(self):
        src = _src("project_detail.js")
        assert 'data-name="${' not in src, "attribute injection via template literal"
        assert 'value="${' not in src, "value attribute injection via template literal"


class TestSchedulesUsesSameDOMPattern:
    def test_no_innerHTML(self):
        src = _tpl_src("schedules.html")
        assert "innerHTML" not in src, "schedules.html should not use innerHTML — use DP helpers"

    def test_no_escapeHtml(self):
        src = _tpl_src("schedules.html")
        assert "escapeHtml" not in src, "schedules.html should use DP.text instead of escapeHtml"

    def test_uses_DP_helpers(self):
        src = _tpl_src("schedules.html")
        assert "DP." in src, "schedules.html should use DP helpers for consistency"


class TestProjectDetailDialogNoInlineHandlers:
    def test_no_onclick_in_dialog(self):
        tpl = _tpl_src("project_detail.html")
        assert 'onclick="lookupMember' not in tpl
        assert 'onclick="hideMemberAddDialog' not in tpl
        assert 'oninput="onMemberLookupInput' not in tpl
        assert 'onsubmit="confirmAddMember' not in tpl

    def test_addEventListener_bindings(self):
        tpl = _tpl_src("project_detail.html")
        assert "addEventListener" in tpl


class TestNoInnerHTMLInAnyFrontendFile:
    _DP_REQUIRED_JS = {"project_detail.js"}

    def test_all_js_files_no_innerHTML(self):
        for js in _JS_DIR.glob("*.js"):
            src = js.read_text(encoding="utf-8")
            assert "innerHTML" not in src or js.name == "dom.js", \
                f"{js.name} still uses innerHTML — XSS vector"

    def test_all_html_templates_no_innerHTML(self):
        for tpl in _TPL_DIR.glob("*.html"):
            src = tpl.read_text(encoding="utf-8")
            assert "innerHTML" not in src, f"{tpl.name} still uses innerHTML — XSS vector"

    def test_dp_required_js_use_DP(self):
        for js in _JS_DIR.glob("*.js"):
            if js.name in self._DP_REQUIRED_JS:
                src = js.read_text(encoding="utf-8")
                assert "DP." in src, f"{js.name} should use DP helpers"
