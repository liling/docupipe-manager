"""Isolated dws state environments.

Every dws-touching subprocess runs inside an isolated temp HOME/config/cache
with DWS_DISABLE_KEYCHAIN=1, so operations never share ~/.dws and can run
concurrently. The file-based DEK backend (forced by the flag) also makes
auth export/import blobs portable across machines.
"""
import os
import shutil
from contextlib import contextmanager
from tempfile import mkdtemp
from typing import Iterator


def make_dws_env(root: str) -> dict[str, str]:
    """Build an isolated dws env dict pointing all state under ``root``."""
    return {
        **os.environ,
        "HOME": root,
        "DWS_CONFIG_DIR": os.path.join(root, "dws-config"),
        "DWS_CACHE_DIR": os.path.join(root, "dws-cache"),
        "DWS_DISABLE_KEYCHAIN": "1",
    }


@contextmanager
def isolated_dws_env() -> Iterator[dict[str, str]]:
    """Allocate a one-shot isolated dws env; rmtree the root on exit.

    Use for short-lived operations (probe / refresh / single run). For
    long-lived sessions (device flow) call ``make_dws_env`` directly on a
    mkdtemp root and clean up manually.
    """
    root = mkdtemp(prefix="dws-env-")
    try:
        yield make_dws_env(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
