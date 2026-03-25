"""Tests for meta-work ratio circuit breaker.

Verifies that the breaker blocks dispatch-factory work when the ratio
of recent factory sessions exceeds the threshold, and that only urgent
(human-escalated) tickets bypass the block.
"""

from __future__ import annotations

from unittest import mock

import foreman
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

    def test_blocked_at_exact_threshold(self):
        """6/10 dispatch-factory sessions (exactly 60%) must trip the breaker.

        Regression: previously used `>` instead of `>=`, so exactly-at-threshold
        ratios (6/10 = 0.6) would sneak through, making the effective threshold ~70%.
        """
        projects = ["dispatch-factory"] * 6 + ["other-project"] * 4
        sessions = _mock_sessions(projects)
        with mock.patch("artifacts.list_sessions_with_timestamps", return_value=sessions):
            assert meta_work_ratio.is_blocked() is True

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

    def test_exactly_at_threshold_is_blocked(self):
        """6/10 = 0.6 ratio should be blocked (>= threshold, not just >)."""
        projects = ["dispatch-factory"] * 6 + ["other-project"] * 4
        sessions = _mock_sessions(projects)
        with mock.patch("artifacts.list_sessions_with_timestamps", return_value=sessions):
            info = meta_work_ratio.get_ratio()
            assert info["ratio"] == 0.6
            assert info["blocked"] is True


class TestForemanPriorityEscalation:
    """Foreman cannot escalate dispatch-factory tickets to urgent priority.

    This prevents the LLM from gaming the meta-work breaker by self-escalating
    factory tickets to 'urgent', which bypasses the ratio check.
    """

    def _factory_ticket(self, ticket_id: str = "T-001", priority: str = "normal") -> dict:
        return {"id": ticket_id, "project": "dispatch-factory", "priority": priority, "task": "test"}

    def _product_ticket(self, ticket_id: str = "T-002", priority: str = "normal") -> dict:
        return {"id": ticket_id, "project": "other-project", "priority": priority, "task": "test"}

    def test_reprioritize_blocks_factory_urgent(self):
        """Foreman cannot reprioritize dispatch-factory tickets to urgent."""
        with mock.patch("backlog.list_tickets", return_value=[self._factory_ticket()]):
            result = foreman._execute_action({
                "type": "reprioritize",
                "ticket_id": "T-001",
                "priority": "urgent",
            })
            assert result["status"] == "blocked"
            assert "human escalation only" in result["detail"]

    def test_reprioritize_allows_factory_high(self):
        """Foreman can reprioritize dispatch-factory tickets to high (still subject to breaker)."""
        with (
            mock.patch("backlog.list_tickets", return_value=[self._factory_ticket()]),
            mock.patch("backlog.update_ticket", return_value=True),
        ):
            result = foreman._execute_action({
                "type": "reprioritize",
                "ticket_id": "T-001",
                "priority": "high",
            })
            assert result["status"] == "ok"

    def test_reprioritize_allows_product_urgent(self):
        """Foreman can escalate non-factory tickets to urgent."""
        with (
            mock.patch("backlog.list_tickets", return_value=[self._product_ticket()]),
            mock.patch("backlog.update_ticket", return_value=True),
        ):
            result = foreman._execute_action({
                "type": "reprioritize",
                "ticket_id": "T-002",
                "priority": "urgent",
            })
            assert result["status"] == "ok"

    def test_update_ticket_blocks_factory_urgent(self):
        """Foreman cannot set dispatch-factory ticket priority to urgent via update_ticket."""
        with mock.patch("backlog.list_tickets", return_value=[self._factory_ticket()]):
            result = foreman._execute_action({
                "type": "update_ticket",
                "ticket_id": "T-001",
                "updates": {"priority": "urgent"},
            })
            assert result["status"] == "blocked"
            assert "human escalation only" in result["detail"]

    def test_update_ticket_allows_product_urgent(self):
        """Foreman can set non-factory ticket priority to urgent via update_ticket."""
        with (
            mock.patch("backlog.list_tickets", return_value=[self._product_ticket()]),
            mock.patch("backlog.update_ticket", return_value=True),
        ):
            result = foreman._execute_action({
                "type": "update_ticket",
                "ticket_id": "T-002",
                "updates": {"priority": "urgent"},
            })
            assert result["status"] == "ok"
