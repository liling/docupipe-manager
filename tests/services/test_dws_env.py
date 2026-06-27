import os

from docupipe_manager.services.dws_env import isolated_dws_env, make_dws_env


def test_make_dws_env_has_required_keys():
    env = make_dws_env("/tmp/fake-root")
    assert env["HOME"] == "/tmp/fake-root"
    assert env["DWS_CONFIG_DIR"] == "/tmp/fake-root/dws-config"
    assert env["DWS_CACHE_DIR"] == "/tmp/fake-root/dws-cache"
    assert env["DWS_DISABLE_KEYCHAIN"] == "1"
    # 继承当前进程 env（PATH 等）
    assert "PATH" in env


def test_isolated_dws_env_creates_and_cleans_up():
    created = {}
    with isolated_dws_env() as env:
        root = env["HOME"]
        created["root"] = root
        created["exists_during"] = os.path.isdir(root)
        created["env"] = env
    # 退出后目录被清理
    assert created["exists_during"] is True
    assert not os.path.exists(created["root"])
    assert created["env"]["DWS_DISABLE_KEYCHAIN"] == "1"
