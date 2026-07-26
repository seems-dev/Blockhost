import hashlib
import uuid
from types import SimpleNamespace

import pytest

from blockhost_backend.database.schema import ServerFlavor
from blockhost_backend.orchestrator import backup as backup_module
from blockhost_backend.orchestrator.backup import (
    BackupRestoreError,
    _commit_local_backup_artifact,
    _included_backup_paths,
    backup_object_path,
    backup_storage_root,
    verify_backup_artifact,
)


def test_java_backup_uses_world_directory_when_world_folder_is_named_after_server_world(tmp_path):
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    world_dir = server_dir / "paper test 120.1"
    world_dir.mkdir()
    (server_dir / "server.properties").write_text("server-port=25565\n", encoding="utf-8")

    server = SimpleNamespace(
        flavor=ServerFlavor.PURPUR,
        mc_config={"server_dir": str(server_dir)},
        world_name="paper test 120.1",
    )

    includes = _included_backup_paths(server, server_dir, "paper test 120.1")

    assert any(source == world_dir and arcname.startswith("java/") for source, arcname in includes)


def test_backup_storage_root_resolves_relative_to_project_root_not_cwd(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    process_cwd = tmp_path / "worker-cwd"
    process_cwd.mkdir()

    def resolve_data_path(path: str):
        return (project_root / path).resolve()

    monkeypatch.setattr(backup_module, "resolve_data_path", resolve_data_path)
    monkeypatch.setattr(
        backup_module,
        "get_settings",
        lambda: SimpleNamespace(backup_storage_dir="backups", backup_temp_dir="backups/tmp"),
    )
    monkeypatch.chdir(process_cwd)

    assert backup_storage_root() == project_root / "backups"


def test_commit_local_backup_artifact_is_atomic_and_verifiable(tmp_path, monkeypatch):
    monkeypatch.setattr(
        backup_module,
        "get_settings",
        lambda: SimpleNamespace(backup_storage_dir=str(tmp_path / "backups"), backup_temp_dir=str(tmp_path / "tmp")),
    )

    server_id = uuid.uuid4()
    backup_id = uuid.uuid4()
    final_archive = backup_object_path(server_id, backup_id)
    temp_archive = final_archive.with_name(f".{final_archive.name}.tmp")
    temp_archive.parent.mkdir(parents=True, exist_ok=True)
    temp_archive.write_bytes(b"backup payload")

    artifact = _commit_local_backup_artifact(temp_archive, final_archive)

    expected_checksum = hashlib.sha256(b"backup payload").hexdigest()
    assert artifact.path == final_archive
    assert artifact.size_bytes == len(b"backup payload")
    assert artifact.checksum_sha256 == expected_checksum
    assert final_archive.read_bytes() == b"backup payload"
    assert final_archive.with_suffix(final_archive.suffix + ".sha256").read_text(encoding="utf-8").strip() == expected_checksum
    assert not temp_archive.exists()


def test_verify_backup_artifact_rejects_completed_metadata_without_file(tmp_path, monkeypatch):
    monkeypatch.setattr(
        backup_module,
        "get_settings",
        lambda: SimpleNamespace(backup_storage_dir=str(tmp_path / "backups"), backup_temp_dir=str(tmp_path / "tmp")),
    )

    backup = SimpleNamespace(
        server_id=uuid.uuid4(),
        id=uuid.uuid4(),
        size_bytes=10,
        checksum_sha256="0" * 64,
    )

    with pytest.raises(BackupRestoreError, match="missing from storage"):
        verify_backup_artifact(backup)
