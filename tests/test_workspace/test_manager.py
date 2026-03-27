from __future__ import annotations

import gzip

import pytest

from orchcore.workspace import WorkspaceManager


def test_ensure_dirs_creates_workspace_directory(tmp_path) -> None:
    manager = WorkspaceManager(tmp_path)

    manager.ensure_dirs()

    assert manager.workspace_dir.exists()
    assert manager.workspace_dir.name == ".orchcore-workspace"


def test_write_and_read_file_round_trip(tmp_path) -> None:
    manager = WorkspaceManager(tmp_path)
    manager.ensure_dirs()

    path = manager.write_file("notes.md", "hello world")

    assert path == manager.workspace_dir / "notes.md"
    assert manager.read_file("notes.md") == "hello world"


def test_read_file_returns_none_for_missing_file(tmp_path) -> None:
    manager = WorkspaceManager(tmp_path)
    manager.ensure_dirs()

    assert manager.read_file("missing.txt") is None


def test_archive_compresses_stream_files_and_creates_latest_symlink(tmp_path) -> None:
    manager = WorkspaceManager(tmp_path)
    manager.ensure_dirs()
    manager.set_task_slug("My task for archival")
    manager.write_file("run.stream", "line 1\nline 2\n")
    manager.write_file("summary.md", "# Summary")

    archive = manager.archive()

    assert archive.exists()
    assert (archive / "summary.md").read_text(encoding="utf-8") == "# Summary"
    with gzip.open(archive / "run.stream.gz", "rt", encoding="utf-8") as fh:
        assert fh.read() == "line 1\nline 2\n"

    latest = archive.parent / "latest"
    assert latest.is_symlink()
    assert latest.resolve() == archive
    assert not (manager.workspace_dir / "run.stream").exists()


def test_archive_preserves_nested_output_directory_structure(tmp_path) -> None:
    manager = WorkspaceManager(tmp_path)
    manager.ensure_dirs()
    manager.set_task_slug("nested outputs test")

    nested_dir = manager.workspace_dir / "outputs" / "phase1"
    nested_dir.mkdir(parents=True)
    (nested_dir / "agent.md").write_text("# Agent output", encoding="utf-8")
    (nested_dir / "agent.stream").write_bytes(b"stream data\n")

    archive = manager.archive()

    # Nested .md preserved with directory structure
    archived_md = archive / "outputs" / "phase1" / "agent.md"
    assert archived_md.exists()
    assert archived_md.read_text(encoding="utf-8") == "# Agent output"

    # Nested .stream compressed with directory structure
    archived_stream_gz = archive / "outputs" / "phase1" / "agent.stream.gz"
    assert archived_stream_gz.exists()
    with gzip.open(archived_stream_gz, "rb") as fh:
        assert fh.read() == b"stream data\n"

    # Nested .stream file removed from workspace after archival
    assert not (nested_dir / "agent.stream").exists()


def test_archive_creates_unique_directories_for_repeated_calls(tmp_path) -> None:
    manager = WorkspaceManager(tmp_path)
    manager.ensure_dirs()
    manager.set_task_slug("My task for archival")
    manager.write_file("summary.md", "first archive")

    first_archive = manager.archive()

    manager.write_file("summary.md", "second archive")
    second_archive = manager.archive()

    assert first_archive != second_archive
    assert first_archive.parent == second_archive.parent
    assert (first_archive / "summary.md").read_text(encoding="utf-8") == "first archive"
    assert (second_archive / "summary.md").read_text(encoding="utf-8") == "second archive"
    assert (first_archive.parent / "latest").resolve() == second_archive


def test_cleanup_removes_workspace_directory(tmp_path) -> None:
    manager = WorkspaceManager(tmp_path)
    manager.ensure_dirs()

    manager.cleanup()

    assert not manager.workspace_dir.exists()


def test_context_manager_preserves_workspace_on_error(tmp_path) -> None:
    manager = WorkspaceManager(tmp_path)

    with pytest.raises(RuntimeError), manager as workspace:
        workspace.ensure_dirs()
        workspace.write_file("keep.txt", "keep me")
        raise RuntimeError("boom")

    assert manager.workspace_dir.exists()


def test_workspace_name_parameter_is_configurable(tmp_path) -> None:
    manager = WorkspaceManager(tmp_path, workspace_name=".custom-workspace")

    assert manager.workspace_dir == tmp_path / ".custom-workspace"
