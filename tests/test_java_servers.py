import uuid
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest
from fastapi import BackgroundTasks

from blockhost_backend.api.schemas import CreateServerRequest
from blockhost_backend.api.servers import create_server
from blockhost_backend.database.schema import Server, ServerFlavor, ServerState, User, VMProvider
from blockhost_backend.minecraft.java_compat import (
    get_protocol_version,
    is_java_flavor,
    is_supported_java_flavor,
    required_java_version,
)
from blockhost_backend.runtime.interface import RuntimeStartRequest


def test_create_java_server_logic():
    db = Mock()
    user = Mock(spec=User)
    user.id = uuid.uuid4()

    payload = CreateServerRequest(
        world_name="My Java World",
        flavor=ServerFlavor.JAVA_VANILLA,
        mc_version="1.21.0",
    )

    bg_tasks = Mock(spec=BackgroundTasks)

    with patch("blockhost_backend.api.servers._allocate_port") as mock_alloc, \
         patch("blockhost_backend.api.servers.select_best_node") as mock_select, \
         patch("blockhost_backend.api.servers._server_dirs") as mock_dirs, \
         patch("blockhost_backend.api.servers._invalidate_server_read_cache"), \
         patch("blockhost_backend.api.servers._server_to_out"), \
         patch("blockhost_backend.minecraft.software_provider.resolve_jar_url") as mock_resolve, \
         patch("blockhost_backend.api.servers._guard_disk_for_operation"):

        mock_alloc.return_value = 25565
        mock_select.return_value = None
        mock_dirs.return_value = (Path("/tmp/v"), Path("/tmp/s"), Path("/tmp/l"))
        mock_resolve.return_value = "http://example.com/server.jar"

        create_server(payload=payload, background_tasks=bg_tasks, user=user, db=db)

        mock_alloc.assert_called_once()
        kwargs = mock_alloc.call_args.kwargs
        assert kwargs.get("flavor") == ServerFlavor.JAVA_VANILLA

        bg_tasks.add_task.assert_called_once()
        task_func = bg_tasks.add_task.call_args.args[0]
        assert "provision_java_server" in task_func.__name__


def test_java_create_requires_mc_version():
    with pytest.raises(ValueError, match="mc_version is required"):
        CreateServerRequest(world_name="Test", flavor=ServerFlavor.PAPER)


def test_unsupported_java_flavor_rejected():
    with pytest.raises(ValueError, match="not supported yet"):
        CreateServerRequest(
            world_name="Test",
            flavor=ServerFlavor.FABRIC,
            mc_version="1.21.0",
        )


def test_required_java_version_mapping():
    assert required_java_version("1.21.4") == 21
    assert required_java_version("1.20.4") == 17
    assert required_java_version("1.17.1") == 16


def test_runtime_start_request_accepts_jdk_path(tmp_path: Path):
    req = RuntimeStartRequest(
        server_id="abc",
        server_dir=tmp_path,
        port=25565,
        requested_version="1.21.0",
        executable_name="server.jar",
        ram_mb=1024,
        cpu_quota_pct=100,
        jdk_path=tmp_path / "jdk",
    )
    assert req.jdk_path == tmp_path / "jdk"


def test_java_flavor_helpers():
    assert is_java_flavor(ServerFlavor.PAPER) is True
    assert is_java_flavor(ServerFlavor.BEDROCK) is False
    assert is_supported_java_flavor(ServerFlavor.PURPUR) is True
    assert is_supported_java_flavor(ServerFlavor.FORGE) is False


def test_get_protocol_version_uses_local_table():
    assert get_protocol_version("1.20.1") == 763
    assert get_protocol_version("1.21.4") == 769


def test_get_protocol_version_offline_fallback():
    with patch("blockhost_backend.minecraft.java_compat._protocol_from_remote", side_effect=OSError("offline")):
        assert get_protocol_version("9.9.9") == 765


def test_provision_java_server_uses_uuid_lookup(tmp_path: Path):
    from blockhost_backend.api.servers import _provision_java_server

    server_id = str(uuid.uuid4())
    server_uuid = uuid.UUID(server_id)
    server = Mock()
    server.mc_config = {}
    server.world_name = "Test"
    server.node_id = None
    server.state = ServerState.provisioning

    db = MagicMock()
    db.get.return_value = server
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    jar_path = server_dir / "server.jar"
    jar_path.write_bytes(b"jar")

    with patch("blockhost_backend.database.db.SessionLocal") as mock_session_local, \
         patch("blockhost_backend.minecraft.software_provider.download_file"), \
         patch("blockhost_backend.api.servers.write_java_server_properties"), \
         patch("blockhost_backend.api.servers._java_server_properties_from_config", return_value=Mock()):
        mock_session_local.return_value.__enter__.return_value = db
        _provision_java_server(
            server_id,
            "http://example.com/server.jar",
            str(jar_path),
            str(server_dir),
            25565,
        )

    db.get.assert_called_with(Server, server_uuid)
    assert server.state == ServerState.created
    db.commit.assert_called()


def test_java_server_properties_default_online_mode_is_false():
    from blockhost_backend.api.servers import _java_server_properties_from_config

    server = Mock()
    server.mc_config = {}
    server.world_name = "Test World"

    props = _java_server_properties_from_config(server=server, port=25565)

    assert props.online_mode is False


def test_set_java_server_property_updates_existing_server_properties(tmp_path: Path):
    from blockhost_backend.minecraft.java_properties import set_java_server_property

    props_path = tmp_path / "server.properties"
    props_path.write_text("online-mode=true\nmotd=Hello\n", encoding="utf-8")

    set_java_server_property(props_path, "online-mode", False)

    contents = props_path.read_text(encoding="utf-8")
    assert "online-mode=false" in contents
    assert "motd=Hello" in contents


def test_java_port_allocation_ignores_bedrock_servers():
    from blockhost_backend.api.servers import _allocate_port

    db = MagicMock()
    bind = MagicMock()
    bind.dialect.name = "sqlite"
    db.get_bind.return_value = bind

    bedrock_row = MagicMock()
    bedrock_row.id = "bedrock-1"
    bedrock_row.vm_port = 19132
    bedrock_row.flavor = ServerFlavor.BEDROCK

    db.execute.return_value.all.return_value = [bedrock_row]

    with patch("blockhost_backend.api.servers.get_settings") as mock_settings, \
         patch("blockhost_backend.api.servers.pick_free_tcp_port", return_value=25565) as mock_pick:
        settings = MagicMock()
        settings.java_port_range_start = 25565
        settings.java_port_range_end = 25665
        mock_settings.return_value = settings

        port = _allocate_port(db=db, flavor=ServerFlavor.JAVA_VANILLA, node_id=None)

    assert port == 25565
    mock_pick.assert_called_once()
    used_ports = mock_pick.call_args.kwargs["used_ports"]
    assert 19132 not in used_ports
