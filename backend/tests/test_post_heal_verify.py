"""Tests for post-heal deploy verification.

Covers:
- Rebase-in-progress detection returns failed status
- Normal verify_deploy delegates to health check
- Rebase detection with rebase-merge directory
- Rebase detection with rebase-apply directory
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import post_heal_verify


def _setup(tmp: Path) -> None:
    """Point settings at a temp directory."""
    post_heal_verify.settings.artifacts_dir = str(tmp)


def test_rebase_merge_detected(tmp_path: Path) -> None:
    """verify_deploy returns failed when .git/rebase-merge exists."""
    _setup(tmp_path)

    project_dir = tmp_path / "projects" / "myproject"
    project_dir.mkdir(parents=True)
    git_dir = project_dir / ".git"
    git_dir.mkdir()
    (git_dir / "rebase-merge").mkdir()

    with (
        mock.patch(
            "post_heal_verify._get_project_dir",
            return_value=str(project_dir),
        ),
        mock.patch(
            "post_heal_verify.subprocess.run",
            return_value=mock.Mock(
                returncode=0,
                stdout=".git\n",
            ),
        ),
    ):
        result = post_heal_verify.verify_deploy("myproject", "session-100")

    assert result["status"] == "failed"
    assert "rebase paused" in result["reason"]
    assert "rebase-merge" in result["reason"]
    assert result["session_id"] == "session-100"


def test_rebase_apply_detected(tmp_path: Path) -> None:
    """verify_deploy returns failed when .git/rebase-apply exists."""
    _setup(tmp_path)

    project_dir = tmp_path / "projects" / "myproject"
    project_dir.mkdir(parents=True)
    git_dir = project_dir / ".git"
    git_dir.mkdir()
    (git_dir / "rebase-apply").mkdir()

    with (
        mock.patch(
            "post_heal_verify._get_project_dir",
            return_value=str(project_dir),
        ),
        mock.patch(
            "post_heal_verify.subprocess.run",
            return_value=mock.Mock(
                returncode=0,
                stdout=".git\n",
            ),
        ),
    ):
        result = post_heal_verify.verify_deploy("myproject", "session-100")

    assert result["status"] == "failed"
    assert "rebase paused" in result["reason"]
    assert "rebase-apply" in result["reason"]


def test_no_rebase_proceeds_to_health_check(tmp_path: Path) -> None:
    """verify_deploy proceeds to URL check when no rebase is in progress."""
    _setup(tmp_path)

    project_dir = tmp_path / "projects" / "myproject"
    project_dir.mkdir(parents=True)
    git_dir = project_dir / ".git"
    git_dir.mkdir()
    # No rebase-merge or rebase-apply

    with (
        mock.patch(
            "post_heal_verify._get_project_dir",
            return_value=str(project_dir),
        ),
        mock.patch(
            "post_heal_verify.subprocess.run",
            return_value=mock.Mock(
                returncode=0,
                stdout=".git\n",
            ),
        ),
        mock.patch(
            "post_heal_verify._get_project_url",
            return_value=None,
        ),
    ):
        result = post_heal_verify.verify_deploy("myproject", "session-100")

    # No URL configured → skipped (not failed from rebase)
    assert result["status"] == "skipped"
    assert "no health check URL" in result["reason"]


def test_git_rev_parse_failure_skips_rebase_check(tmp_path: Path) -> None:
    """If git rev-parse fails, rebase check is skipped and verify continues."""
    _setup(tmp_path)

    with (
        mock.patch(
            "post_heal_verify._get_project_dir",
            return_value=str(tmp_path / "nonexistent"),
        ),
        mock.patch(
            "post_heal_verify.subprocess.run",
            return_value=mock.Mock(
                returncode=128,
                stdout="",
            ),
        ),
        mock.patch(
            "post_heal_verify._get_project_url",
            return_value=None,
        ),
    ):
        result = post_heal_verify.verify_deploy("myproject", "session-100")

    # Should skip rebase check gracefully and proceed
    assert result["status"] == "skipped"
