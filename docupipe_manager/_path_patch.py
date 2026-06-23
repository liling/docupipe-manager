"""Patch: add xinyi-platform to sys.path for docupipe-manager."""
import site
import sys
from pathlib import Path

_THIS = Path(__file__).resolve().parent
_XINYI = _THIS.parent / "xinyi-platform"
if _XINYI.exists():
    site.addsitedir(str(_XINYI))
