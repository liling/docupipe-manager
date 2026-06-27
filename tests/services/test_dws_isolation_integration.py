"""Integration: real dws CLI in an isolated env (skipped by default).

Run with: pytest -m integration
Requires: a portable auth blob exported with DWS_DISABLE_KEYCHAIN=1, path in
env DOCUPIPE_TEST_DWS_BLOB. Skips if not provided.
"""
import os
import shutil
import uuid

import pytest

from docupipe_manager.services.dws_env import isolated_dws_env, make_dws_env

pytestmark = [pytest.mark.integration]

DWS = os.environ.get("DWS_CLI_PATH", "dws")
BLOB_ENV = "DOCUPIPE_TEST_DWS_BLOB"


def _require_blob():
    path = os.environ.get(BLOB_ENV)
    if not path or not os.path.isfile(path):
        pytest.skip(f"set {BLOB_ENV} to a flag-exported auth blob to run this test")
    with open(path) as f:
        return f.read().strip()


def _real_dws_files():
    real = os.path.join(os.path.expanduser("~"), ".dws")
    if not os.path.isdir(real):
        return set()
    return {os.path.relpath(os.path.join(r, fn), real) for r, _, fs in os.walk(real) for fn in fs}


async def _run(args, env):
    import asyncio
    proc = await asyncio.create_subprocess_exec(
        DWS, *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
    return proc.returncode, stdout, stderr


@pytest.mark.asyncio
async def test_isolated_dws_cycle_does_not_touch_real_home():
    blob = _require_blob()
    before = _real_dws_files()

    with isolated_dws_env() as env:
        import_path = os.path.join(env["HOME"], "auth.b64")
        with open(import_path, "w") as f:
            f.write(blob)

        rc, out, err = await _run(["auth", "import", "--base64", "-i", import_path], env)
        assert rc == 0, err

        rc, out, _ = await _run(["auth", "status", "--format", "json"], env)
        assert rc == 0
        assert b'"authenticated": true' in out or b'"authenticated":true' in out

        rc, _, err = await _run(["wiki", "space", "list"], env)
        assert rc == 0, err

        export_path = os.path.join(env["HOME"], "export.b64")
        rc, _, err = await _run(["auth", "export", "--base64", "-o", export_path], env)
        assert rc == 0 and os.path.isfile(export_path), err

        # 导出的 blob 应能再次 import（可移植）
        rc2, _, err2 = await _run(["auth", "import", "--base64", "-i", export_path, "--force"], env)
        assert rc2 == 0, err2

    after = _real_dws_files()
    assert before == after, f"real ~/.dws changed during isolated cycle: {before ^ after}"
