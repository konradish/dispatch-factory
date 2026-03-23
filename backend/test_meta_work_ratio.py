"""Tests for meta-work ratio circuit breaker.

Verifies that the breaker blocks dispatch-factory work when the ratio
of recent factory sessions exceeds the threshold, and that only urgent
(human-escalated) tickets bypass the block.
"""

from __future__ import annotations

from unittest import mock

import meta_work_ratio


def _mock_sessions(projects: list[str]) -> list[dict]:
    """Build minimal session dicts for list_sessions_with_timestamps."""
    return [{"project": p, "id": f"worker-{p}-{i:04d}"} for i, p in enumerate(projects)]


class TestIsBlocked:
    """is_blocked should respect ratio threshold and priority rules."""

    def test_blocked_when_ratio_exceeded(self):
        """7/10 dispatch-factory sessions (70%) exceeds 60% threshold."""
        projects = ["dispatch-factory"] * 7 + ["other-project"] * 3
        sessions = _mock_sessions(projects)
        with mock.patch("artifacts.list_sessions_with_timestamps", return_value=sessions):
            assert meta_work_ratio.is_blocked() is True

    def test_not_blocked_when_ratio_below_threshold(self):
        """5/10 dispatch-factory sessions (50%) is below 60% threshold."""
        projects = ["dispatch-factory"] * 5 + ["other-project"] * 5
        sessions = _mock_sessions(projects)
        with mock.patch("artifacts.list_sessions_with_timestamps", return_value=sessions):
            assert meta_work_ratio.is_blocked() is False

    def test_high_priority_does_not_bypass(self):
        """High-priority tickets must NOT bypass the breaker (regression)."""
        projects = ["dispatch-factory"] * 7 + ["other-project"] * 3
        sessions = _mock_sessions(projects)
        with mock.patch("artifacts.list_sessions_with_timestamps", return_value=sessions):
            assert meta_work_ratio.is_blocked(priority="high") is True

    def test_urgent_bypasses(self):
        """Urgent (human-escalated) tickets bypass the breaker."""
        projects = ["dispatch-factory"] * 7 + ["other-project"] * 3
        sessions = _mock_sessions(projects)
        with mock.patch("artifacts.list_sessions_with_timestamps", return_value=sessions):
            assert meta_work_ratio.is_blocked(priority="urgent") is False

    def test_empty_sessions_not_blocked(self):
        """No sessions at all should not block."""
        with mock.patch("artifacts.list_sessions_with_timestamps", return_value=[]):
            assert meta_work_ratio.is_blocked() is False


class TestGetRatio:
    """get_ratio should compute correct ratio from session window."""

    def test_ratio_computation(self):
        """7/10 factory sessions = 0.7 ratio, blocked."""
        projects = ["dispatch-factory"] * 7 + ["other-project"] * 3
        sessions = _mock_sessions(projects)
        with mock.patch("artifacts.list_sessions_with_timestamps", return_value=sessions):
            info = meta_work_ratio.get_ratio()
            assert info["factory_count"] == 7
            assert info["total"] == 10
            assert info["ratio"] == 0.7
            assert info["blocked"] is True

    def test_window_respects_size(self):
        """Only the first WINDOW_SIZE sessions are counted."""
        # 20 sessions, but only first 10 should be used
        projects = ["dispatch-factory"] * 5 + ["other-project"] * 5 + ["dispatch-factory"] * 10
        sessions = _mock_sessions(projects)
        with mock.patch("artifacts.list_sessions_with_timestamps", return_value=sessions):
            info = meta_work_ratio.get_ratio()
            assert info["total"] == 10
            assert info["factory_count"] == 5
            assert info["blocked"] is False
