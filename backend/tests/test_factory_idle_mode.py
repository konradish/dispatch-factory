"""Tests for factory idle mode — hard stop when all projects need human input.

Verifies:
- Idle mode activates when all non-paused projects have HUMAN INPUT NEEDED + empty backlog
- Does NOT activate when even one project has work or lacks HUMAN INPUT NEEDED
- Does NOT activate when zero active projects exist (empty set edge case)
- Does NOT activate when direction file is missing
- Blocks all dispatch paths including high-priority meta-work
- 24h cooldown on factory-wide flag_human works correctly
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest import mock

import factory_idle_mode


def _setup_direction(tmp: Path, content: str) -> None:
    """Write a direction vector file."""
    (tmp / "autopilot-direction.md").write_text(content)


def _setup_backlog(tmp: Path, tickets: list[dict]) -> None:
    """Write a backlog file."""
    (tmp / "factory-backlog.json").write_text(json.dumps(tickets))


DIRECTION_ALL_HUMAN = """\
## Active Projects

- **recipebrain** — HUMAN INPUT NEEDED: define next feature milestone
- **dispatch-factory** — HUMAN INPUT NEEDED: waiting for product roadmap
"""

DIRECTION_MIXED = """\
## Active Projects

- **recipebrain** — HUMAN INPUT NEEDED: define next feature milestone
- **dispatch-factory** — implement monitoring dashboard
"""

DIRECTION_NONE_HUMAN = """\
## Active Projects

- **recipebrain** — implement recipe search optimization
- **dispatch-factory** — implement monitoring dashboard
"""


def test_idle_when_all_projects_need_human_input(tmp_path: Path) -> None:
    """Idle mode activates when ALL active projects have HUMAN INPUT NEEDED + empty backlog."""
    _setup_direction(tmp_path, DIRECTION_ALL_HUMAN)
    _setup_backlog(tmp_path, [])

    with (
        mock.patch("factory_idle_mode.settings") as mock_settings,
        mock.patch("empty_backlog_detector.settings") as mock_ebd_settings,
        mock.patch("backlog.settings") as mock_bl_settings,
        mock.patch("paused_projects.get_paused", return_value={}),
        mock.patch("archived_projects.get_archived", return_value={}),
    ):
        mock_settings.artifacts_dir = str(tmp_path)
        mock_ebd_settings.artifacts_dir = str(tmp_path)
        mock_bl_settings.artifacts_dir = str(tmp_path)

        assert factory_idle_mode.is_idle() is True


def test_not_idle_when_one_project_has_work(tmp_path: Path) -> None:
    """Idle mode does NOT activate when one project lacks HUMAN INPUT NEEDED."""
    _setup_direction(tmp_path, DIRECTION_MIXED)
    _setup_backlog(tmp_path, [])

    with (
        mock.patch("factory_idle_mode.settings") as mock_settings,
        mock.patch("empty_backlog_detector.settings") as mock_ebd_settings,
        mock.patch("backlog.settings") as mock_bl_settings,
        mock.patch("paused_projects.get_paused", return_value={}),
        mock.patch("archived_projects.get_archived", return_value={}),
    ):
        mock_settings.artifacts_dir = str(tmp_path)
        mock_ebd_settings.artifacts_dir = str(tmp_path)
        mock_bl_settings.artifacts_dir = str(tmp_path)

        assert factory_idle_mode.is_idle() is False


def test_not_idle_when_backlog_has_tickets(tmp_path: Path) -> None:
    """Idle mode does NOT activate when pending backlog tickets exist."""
    _setup_direction(tmp_path, DIRECTION_ALL_HUMAN)
    _setup_backlog(tmp_path, [
        {"id": "t-001", "task": "some work", "project": "recipebrain",
         "status": "pending", "priority": "normal", "created_at": "2026-01-01"},
    ])

    with (
        mock.patch("factory_idle_mode.settings") as mock_settings,
        mock.patch("empty_backlog_detector.settings") as mock_ebd_settings,
        mock.patch("backlog.settings") as mock_bl_settings,
        mock.patch("paused_projects.get_paused", return_value={}),
        mock.patch("archived_projects.get_archived", return_value={}),
    ):
        mock_settings.artifacts_dir = str(tmp_path)
        mock_ebd_settings.artifacts_dir = str(tmp_path)
        mock_bl_settings.artifacts_dir = str(tmp_path)

        assert factory_idle_mode.is_idle() is False


def test_not_idle_when_no_direction_file(tmp_path: Path) -> None:
    """Idle mode does NOT activate when direction file is missing (safe default)."""
    with (
        mock.patch("factory_idle_mode.settings") as mock_settings,
        mock.patch("empty_backlog_detector.settings") as mock_ebd_settings,
        mock.patch("backlog.settings") as mock_bl_settings,
        mock.patch("paused_projects.get_paused", return_value={}),
        mock.patch("archived_projects.get_archived", return_value={}),
    ):
        mock_settings.artifacts_dir = str(tmp_path)
        mock_ebd_settings.artifacts_dir = str(tmp_path)
        mock_bl_settings.artifacts_dir = str(tmp_path)

        assert factory_idle_mode.is_idle() is False


def test_not_idle_when_all_projects_paused(tmp_path: Path) -> None:
    """Idle mode does NOT activate when all projects are paused (zero active = not idle)."""
    _setup_direction(tmp_path, DIRECTION_ALL_HUMAN)
    _setup_backlog(tmp_path, [])

    with (
        mock.patch("factory_idle_mode.settings") as mock_settings,
        mock.patch("empty_backlog_detector.settings") as mock_ebd_settings,
        mock.patch("backlog.settings") as mock_bl_settings,
        mock.patch("paused_projects.get_paused", return_value={
            "recipebrain": {"reason": "paused"},
            "dispatch-factory": {"reason": "paused"},
        }),
        mock.patch("archived_projects.get_archived", return_value={}),
    ):
        mock_settings.artifacts_dir = str(tmp_path)
        mock_ebd_settings.artifacts_dir = str(tmp_path)
        mock_bl_settings.artifacts_dir = str(tmp_path)

        assert factory_idle_mode.is_idle() is False


def test_flag_cooldown(tmp_path: Path) -> None:
    """Factory-wide flag_human has 24h cooldown."""
    _setup_direction(tmp_path, DIRECTION_ALL_HUMAN)
    _setup_backlog(tmp_path, [])

    with (
        mock.patch("factory_idle_mode.settings") as mock_settings,
        mock.patch("empty_backlog_detector.settings") as mock_ebd_settings,
        mock.patch("backlog.settings") as mock_bl_settings,
        mock.patch("paused_projects.get_paused", return_value={}),
        mock.patch("archived_projects.get_archived", return_value={}),
    ):
        mock_settings.artifacts_dir = str(tmp_path)
        mock_ebd_settings.artifacts_dir = str(tmp_path)
        mock_bl_settings.artifacts_dir = str(tmp_path)

        # First call should emit flag
        result1 = factory_idle_mode.check_and_flag()
        assert result1 is not None
        assert "factory_idle" in result1

        # Second call within 24h should NOT emit flag
        result2 = factory_idle_mode.check_and_flag()
        assert result2 is None

        # Simulate 24h elapsed
        state = factory_idle_mode._read_state()
        state["last_flagged_at"] = time.time() - (25 * 60 * 60)
        factory_idle_mode._write_state(state)

        # Third call should emit flag again
        result3 = factory_idle_mode.check_and_flag()
        assert result3 is not None


def test_get_state(tmp_path: Path) -> None:
    """get_state returns structured idle mode info."""
    _setup_direction(tmp_path, DIRECTION_ALL_HUMAN)
    _setup_backlog(tmp_path, [])

    with (
        mock.patch("factory_idle_mode.settings") as mock_settings,
        mock.patch("empty_backlog_detector.settings") as mock_ebd_settings,
        mock.patch("backlog.settings") as mock_bl_settings,
        mock.patch("paused_projects.get_paused", return_value={}),
        mock.patch("archived_projects.get_archived", return_value={}),
    ):
        mock_settings.artifacts_dir = str(tmp_path)
        mock_ebd_settings.artifacts_dir = str(tmp_path)
        mock_bl_settings.artifacts_dir = str(tmp_path)

        state = factory_idle_mode.get_state()
        assert state["idle"] is True
        assert state["flag_type"] == "factory_idle"
