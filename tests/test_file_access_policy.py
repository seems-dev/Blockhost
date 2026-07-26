from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from blockhost_backend.api.files import (
    _file_access_policy,
    _validate_file_policy_path,
    list_files,
)
from blockhost_backend.database.schema import ServerFlavor


def _server(flavor: ServerFlavor):
    return SimpleNamespace(flavor=flavor)


def test_java_file_policy_allows_world_mods_plugins_and_config_files():
    policy = _file_access_policy(_server(ServerFlavor.PAPER))

    assert _validate_file_policy_path("world/level.dat", policy) == ("world", "level.dat")
    assert _validate_file_policy_path("mods/example.jar", policy) == ("mods", "example.jar")
    assert _validate_file_policy_path("plugins/LuckPerms.jar", policy) == ("plugins", "LuckPerms.jar")
    assert _validate_file_policy_path("server.properties", policy) == ("server.properties",)


def test_bedrock_file_policy_allows_worlds_packs_and_config_files():
    policy = _file_access_policy(_server(ServerFlavor.BEDROCK))

    assert _validate_file_policy_path("worlds/MyWorld/level.dat", policy) == ("worlds", "MyWorld", "level.dat")
    assert _validate_file_policy_path("development_behavior_packs/pack/manifest.json", policy) == (
        "development_behavior_packs",
        "pack",
        "manifest.json",
    )
    assert _validate_file_policy_path("allowlist.json", policy) == ("allowlist.json",)


def test_bedrock_file_policy_rejects_java_only_paths():
    policy = _file_access_policy(_server(ServerFlavor.BEDROCK))

    with pytest.raises(HTTPException) as exc:
        _validate_file_policy_path("plugins/LuckPerms.jar", policy)

    assert exc.value.status_code == 403


def test_file_policy_rejects_traversal_and_root_executables():
    policy = _file_access_policy(_server(ServerFlavor.PURPUR))

    with pytest.raises(HTTPException):
        _validate_file_policy_path("../other-server/world", policy)
    with pytest.raises(HTTPException):
        _validate_file_policy_path("server.jar", policy)


def test_local_java_root_listing_exposes_safe_server_files_only(tmp_path, monkeypatch):
    (tmp_path / "world").mkdir()
    (tmp_path / "mods").mkdir()
    (tmp_path / "server.properties").write_text("motd=test\n", encoding="utf-8")
    (tmp_path / "server.jar").write_bytes(b"jar")

    server = SimpleNamespace(
        id="server-id",
        node_id=None,
        flavor=ServerFlavor.PAPER,
        mc_config={"server_dir": str(tmp_path)},
    )
    user = SimpleNamespace(id="user-id")
    db = SimpleNamespace(get=lambda model, key: server)

    monkeypatch.setattr("blockhost_backend.api.files._get_server", lambda server_id, user, db: server)

    items = list_files("server-id", path="", user=user, db=db)
    paths = {item.path for item in items}

    assert {"world", "mods", "server.properties"} <= paths
    assert "server.jar" not in paths
