"""Tests for castor.backup — create_backup() and restore_backup() (issue #485)."""

import os
import tarfile

import pytest

from castor.backup import create_backup, restore_backup


# ── create_backup() ───────────────────────────────────────────────────────────


def test_create_backup_produces_tgz(tmp_path):
    """create_backup() writes a .tar.gz archive in tmp_path."""
    # Create a minimal .rcan.yaml file so there is something to back up
    cfg = tmp_path / "robot.rcan.yaml"
    cfg.write_text("rcan_version: '1.2'\n")

    output = tmp_path / "backup.tar.gz"
    result = create_backup(output_path=str(output), work_dir=str(tmp_path))

    assert result == str(output)
    assert output.exists()
    assert tarfile.is_tarfile(str(output))


def test_create_backup_contains_rcan_yaml(tmp_path):
    """The archive contains the .rcan.yaml file."""
    cfg = tmp_path / "myrobot.rcan.yaml"
    cfg.write_text("rcan_version: '1.2'\n")

    output = tmp_path / "backup.tar.gz"
    create_backup(output_path=str(output), work_dir=str(tmp_path))

    with tarfile.open(str(output), "r:gz") as tar:
        names = tar.getnames()
    assert "myrobot.rcan.yaml" in names


def test_create_backup_includes_env_file(tmp_path):
    """The archive includes .env if present."""
    env_file = tmp_path / ".env"
    env_file.write_text("API_KEY=test\n")
    cfg = tmp_path / "robot.rcan.yaml"
    cfg.write_text("")

    output = tmp_path / "backup.tar.gz"
    create_backup(output_path=str(output), work_dir=str(tmp_path))

    with tarfile.open(str(output), "r:gz") as tar:
        names = tar.getnames()
    assert ".env" in names


def test_create_backup_no_files_returns_empty(tmp_path):
    """create_backup returns '' when there is nothing to back up."""
    result = create_backup(output_path=str(tmp_path / "empty.tar.gz"), work_dir=str(tmp_path))
    assert result == ""


# ── restore_backup() ─────────────────────────────────────────────────────────


def _make_simple_backup(tmp_path) -> str:
    """Helper: create a backup with one .rcan.yaml file."""
    cfg = tmp_path / "robot.rcan.yaml"
    cfg.write_text("rcan_version: '1.2'\n")
    archive = tmp_path / "backup.tar.gz"
    create_backup(output_path=str(archive), work_dir=str(tmp_path))
    return str(archive)


def test_restore_backup_extracts_files(tmp_path):
    """restore_backup() extracts files into target_dir."""
    src = tmp_path / "src"
    src.mkdir()
    dst = tmp_path / "dst"
    dst.mkdir()

    archive = _make_simple_backup(src)
    restored = restore_backup(archive, target_dir=str(dst))

    assert len(restored) > 0
    # At least one file was written
    files_in_dst = list(dst.iterdir())
    assert len(files_in_dst) > 0


def test_restore_backup_dry_run_returns_list_without_writing(tmp_path):
    """dry_run=True returns names without writing any files."""
    src = tmp_path / "src"
    src.mkdir()
    dst = tmp_path / "dst"
    dst.mkdir()

    archive = _make_simple_backup(src)
    result = restore_backup(archive, target_dir=str(dst), dry_run=True)

    assert isinstance(result, list)
    assert len(result) > 0
    # Nothing should be written to dst
    assert list(dst.iterdir()) == []


def test_restore_backup_missing_archive(tmp_path):
    """restore_backup returns [] for a missing archive path."""
    result = restore_backup("/nonexistent/backup.tar.gz", target_dir=str(tmp_path))
    assert result == []
