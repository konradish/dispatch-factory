"""Tests for healed_deploy_unverified alert lifecycle.

Verifies that clearing a healed session via cleared_healed_sessions
actually removes the healed_deploy_unverified alert from the project
health output.  This guards against the persistence bug where alerts
survived batch-clear operations (sessions 2253, 2315).
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import mock

import cleared_healed_sessions
import project_health


def _make_artifacts(tmp: Path, sessions: list[dict]) -> None:
    """Write minimal artifact files so list_sessions_with_timestamps picks them up.

    Each session dict should have: id, project, healed (bool), state.
    For healed sessions we write a healer JSON; for deployed sessions
    we write a verifier JSON with status=DEPLOYED.
    """
    for s in sessions:
        sid = s["id"]
        # Every session needs a .log file to be discovered
        (tmp / f"{sid}.log").write_text("started\n")
        # Every session needs a result to reach "completed" or "deployed"
        (tmp / f"{sid}-result.md").write_text("done\n")

        if s.get("healed"):
            (tmp / f"{sid}-healer.json").write_text(
                json.dumps({"action": "retry_same", "diagnosis": "test"})
            )

        if s.get("state") == "deployed":
            (tmp / f"{sid}-verifier.json").write_text(
                json.dumps({"status": "DEPLOYED", "stages": {}})
            )


def _health_alerts(tmp: Path) -> dict[str, list[str]]:
    """Run get_project_health and return {project: alerts} mapping."""
    # Patch out subprocess calls (gh pr list, dispatch --projects)
    with (
        mock.patch("project_health._count_open_prs", return_value=None),
        mock.patch("artifacts.get_known_projects") as mock_projects,
        mock.patch("empty_backlog_detector.detect", return_value=[]),
        mock.patch("paused_projects.get_paused", return_value={}),
    ):
        # Derive project list from artifact files
        import artifacts
        projects = set()
        for entry in tmp.iterdir():
            m = artifacts.SESSION_RE.match(entry.name)
            if m:
                parts = artifacts.SESSION_PARTS_RE.match(m.group(1))
                if parts:
                    projects.add(parts.group(1))
        mock_projects.return_value = sorted(projects)

        return {
            entry["project"]: entry["alerts"]
            for entry in project_health.get_project_health()
        }


def test_clear_session_removes_alert_from_health():
    """Clearing a healed session must remove healed_deploy_unverified from health."""
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)

        # Create one healed-but-unverified session
        _make_artifacts(tmp, [
            {"id": "worker-testproj-0001", "project": "testproj", "healed": True, "state": "completed"},
        ])

        with (
            mock.patch("config.settings.artifacts_dir", tmp_str),
            mock.patch("circuit_breaker.get_state", return_value={}),
        ):
            # Before clearing: alert should be present
            alerts_before = _health_alerts(tmp)
            assert "healed_deploy_unverified" in alerts_before.get("testproj", []), (
                "healed_deploy_unverified alert should be present before clearing"
            )

            # Clear the session
            result = cleared_healed_sessions.clear_session(
                "worker-testproj-0001", reason="test clear", source="test",
            )
            assert result is True, "clear_session should return True for new clear"

            # After clearing: alert must be gone
            alerts_after = _health_alerts(tmp)
            assert "healed_deploy_unverified" not in alerts_after.get("testproj", []), (
                "healed_deploy_unverified alert must not persist after clearing"
            )


def test_batch_clear_removes_alerts_across_projects():
    """Batch-clearing all sessions must remove alerts from every project."""
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)

        # Multiple projects with healed-unverified sessions
        _make_artifacts(tmp, [
            {"id": "worker-alpha-0010", "project": "alpha", "healed": True, "state": "completed"},
            {"id": "worker-beta-0020", "project": "beta", "healed": True, "state": "completed"},
            {"id": "worker-gamma-0030", "project": "gamma", "healed": False, "state": "deployed"},
        ])

        with (
            mock.patch("config.settings.artifacts_dir", tmp_str),
            mock.patch("circuit_breaker.get_state", return_value={}),
        ):
            # Before: alpha and beta should have the alert
            alerts_before = _health_alerts(tmp)
            assert "healed_deploy_unverified" in alerts_before.get("alpha", [])
            assert "healed_deploy_unverified" in alerts_before.get("beta", [])
            assert "healed_deploy_unverified" not in alerts_before.get("gamma", [])

            # Batch clear — same flow as the _batch API endpoint
            import artifacts
            sessions = artifacts.list_sessions_with_timestamps()
            cleared_ids = cleared_healed_sessions.get_cleared_ids()
            healed_unverified = [
                s for s in sessions
                if s.get("summary", {}).get("healed", False)
                and s["state"] == "completed"
                and s["id"] not in cleared_ids
            ]
            by_project: dict[str, list[str]] = {}
            for s in healed_unverified:
                by_project.setdefault(s["project"], []).append(s["id"])
            for proj, sids in by_project.items():
                cleared_healed_sessions.clear_project_sessions(
                    proj, sids, reason="batch test", source="test",
                )

            # After: NO project should have the alert
            alerts_after = _health_alerts(tmp)
            for proj in ("alpha", "beta"):
                assert "healed_deploy_unverified" not in alerts_after.get(proj, []), (
                    f"{proj} still has healed_deploy_unverified after batch clear"
                )


def test_clear_is_persisted_to_disk():
    """Cleared state must survive a fresh read (no in-memory-only state)."""
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)

        with mock.patch("config.settings.artifacts_dir", tmp_str):
            cleared_healed_sessions.clear_session(
                "worker-persist-0001", reason="persist test", source="test",
            )

            # Verify the file exists and contains the session
            cleared_file = tmp / "cleared-healed-sessions.json"
            assert cleared_file.is_file(), "cleared state file must exist on disk"

            data = json.loads(cleared_file.read_text())
            assert "worker-persist-0001" in data, (
                "cleared session ID must be in the persisted file"
            )

            # Fresh read must see the cleared ID
            ids = cleared_healed_sessions.get_cleared_ids()
            assert "worker-persist-0001" in ids


def test_already_cleared_sessions_filtered_from_per_project_clear():
    """Per-project clear endpoint should not re-find already-cleared sessions."""
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)

        _make_artifacts(tmp, [
            {"id": "worker-proj-0001", "project": "proj", "healed": True, "state": "completed"},
            {"id": "worker-proj-0002", "project": "proj", "healed": True, "state": "completed"},
        ])

        with (
            mock.patch("config.settings.artifacts_dir", tmp_str),
            mock.patch("circuit_breaker.get_state", return_value={}),
        ):
            import artifacts

            # Clear one session manually
            cleared_healed_sessions.clear_session(
                "worker-proj-0001", reason="first clear", source="test",
            )

            # Simulate what the per-project endpoint does (post-fix)
            sessions = artifacts.list_sessions_with_timestamps()
            already_cleared = cleared_healed_sessions.get_cleared_ids()
            healed_unverified = [
                s for s in sessions
                if s["project"] == "proj"
                and s.get("summary", {}).get("healed", False)
                and s["state"] == "completed"
                and s["id"] not in already_cleared
            ]

            # Only the uncleaned session should be found
            session_ids = [s["id"] for s in healed_unverified]
            assert "worker-proj-0001" not in session_ids, (
                "already-cleared session must not appear in per-project clear candidates"
            )
            assert "worker-proj-0002" in session_ids
