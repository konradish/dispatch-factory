"""Tests for artifact prefix collision boundary check.

Verifies that session ID prefix matching correctly distinguishes between:
- 'worker-foo-1' and 'worker-foo-10' (collision without boundary check)
- Exact matches with '-' or '.' suffixes (valid artifacts)
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import artifacts


def _make_artifact_dir(tmp_path: Path, filenames: list[str]) -> Path:
    """Create a fake artifacts directory with the given filenames."""
    d = tmp_path / "artifacts"
    d.mkdir()
    for name in filenames:
        p = d / name
        if name.endswith(".json"):
            p.write_text(json.dumps({"test": True}))
        elif name.endswith(".md"):
            p.write_text("# Result\nDone.")
        else:
            p.write_text("log content")
    return d


class TestZombieDetectionPrefixCollision:
    """Bug: entry.name.startswith(session_id) matches across session boundaries.

    E.g., 'worker-foo-1' matches 'worker-foo-10-planner.json', picking up
    activity from a different session and preventing zombie detection.
    """

    def test_does_not_match_longer_session(self, tmp_path: Path) -> None:
        """Files from 'worker-foo-10' must NOT be matched for 'worker-foo-1'."""
        d = _make_artifact_dir(tmp_path, [
            "worker-foo-10-planner.json",
            "worker-foo-10.log",
            "worker-foo-10-result.md",
        ])
        with mock.patch.object(artifacts, "_artifacts_path", return_value=d):
            result = artifacts.get_session("worker-foo-1")
        assert result is None, "Should not match files from a different session"

    def test_matches_own_artifacts(self, tmp_path: Path) -> None:
        """Files from 'worker-foo-1' with valid suffixes must be matched."""
        d = _make_artifact_dir(tmp_path, [
            "worker-foo-1-planner.json",
            "worker-foo-1.log",
            "worker-foo-1-result.md",
        ])
        with mock.patch.object(artifacts, "_artifacts_path", return_value=d):
            result = artifacts.get_session("worker-foo-1")
        assert result is not None
        assert "result" in result["artifacts"]

    def test_coexisting_sessions(self, tmp_path: Path) -> None:
        """When both 'worker-foo-1' and 'worker-foo-10' artifacts exist,
        each session should only see its own files."""
        d = _make_artifact_dir(tmp_path, [
            "worker-foo-1-planner.json",
            "worker-foo-1.log",
            "worker-foo-10-planner.json",
            "worker-foo-10.log",
            "worker-foo-10-result.md",
        ])
        with mock.patch.object(artifacts, "_artifacts_path", return_value=d):
            s1 = artifacts.get_session("worker-foo-1")
            s10 = artifacts.get_session("worker-foo-10")
        # worker-foo-1 should NOT have result (that belongs to worker-foo-10)
        assert s1 is not None
        assert "result" not in s1["artifacts"]
        # worker-foo-10 should have result
        assert s10 is not None
        assert "result" in s10["artifacts"]

    def test_timeline_prefix_collision(self, tmp_path: Path) -> None:
        """get_session_timeline must also respect the boundary check."""
        d = _make_artifact_dir(tmp_path, [
            "worker-foo-1-planner.json",
            "worker-foo-10-result.md",
        ])
        with mock.patch.object(artifacts, "_artifacts_path", return_value=d):
            events = artifacts.get_session_timeline("worker-foo-1")
        type_names = [e["type"] for e in events]
        assert "result" not in type_names, "Should not pick up worker-foo-10's result"

    def test_exact_session_id_file(self, tmp_path: Path) -> None:
        """A file named exactly the session_id (no suffix) should be skipped
        since it doesn't match any artifact type."""
        d = _make_artifact_dir(tmp_path, [
            "worker-foo-1",  # no suffix at all
            "worker-foo-1-planner.json",
        ])
        # Overwrite the bare file with empty content
        (d / "worker-foo-1").write_text("")
        with mock.patch.object(artifacts, "_artifacts_path", return_value=d):
            result = artifacts.get_session("worker-foo-1")
        # Should still find planner but not crash on the bare filename
        assert result is not None
