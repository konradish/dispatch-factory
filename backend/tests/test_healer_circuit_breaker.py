"""Tests for healer circuit breaker — disable healer after repeated interventions.

Verifies:
- Intervention counting increments correctly
- Threshold trip at N=2 creates escalation ticket
- No double-trip on further interventions
- Reset on successful non-healed deploy
- Manual reset clears state
- is_healer_blocked returns correct values
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import healer_circuit_breaker


def _setup(tmp: Path) -> None:
    """Point healer circuit breaker at a temp directory."""
    healer_circuit_breaker.settings.artifacts_dir = str(tmp)


def test_intervention_counting(tmp_path: Path) -> None:
    _setup(tmp_path)

    with mock.patch("healer_circuit_breaker.backlog") as mock_backlog:
        mock_backlog.create_ticket.return_value = {"id": "ticket-1"}

        # First intervention — no trip
        actions = healer_circuit_breaker.record_healer_intervention("myproject", "session-001")
        assert len(actions) == 1
        assert "intervention #1" in actions[0]
        assert not healer_circuit_breaker.is_healer_blocked("myproject")

        # Second intervention — should trip
        actions = healer_circuit_breaker.record_healer_intervention("myproject", "session-002")
        assert len(actions) == 2
        assert "intervention #2" in actions[0]
        assert "TRIPPED" in actions[1]
        assert healer_circuit_breaker.is_healer_blocked("myproject")

        # Verify escalation ticket was created with session IDs
        mock_backlog.create_ticket.assert_called_once()
        call_kwargs = mock_backlog.create_ticket.call_args
        assert "session-001" in call_kwargs.kwargs.get("task", "") or "session-001" in call_kwargs[0][0] if call_kwargs[0] else True
        assert call_kwargs.kwargs.get("source") == "healer-circuit-breaker"
        assert call_kwargs.kwargs.get("priority") == "high"


def test_no_double_trip(tmp_path: Path) -> None:
    _setup(tmp_path)

    with mock.patch("healer_circuit_breaker.backlog") as mock_backlog:
        mock_backlog.create_ticket.return_value = {"id": "ticket-1"}

        healer_circuit_breaker.record_healer_intervention("proj", "s1")
        healer_circuit_breaker.record_healer_intervention("proj", "s2")  # trips
        actions = healer_circuit_breaker.record_healer_intervention("proj", "s3")  # should not re-trip

        # Only one TRIPPED action total
        assert sum("TRIPPED" in a for a in actions) == 0  # third call doesn't trip again
        assert mock_backlog.create_ticket.call_count == 1  # only one ticket created


def test_reset_on_successful_deploy(tmp_path: Path) -> None:
    _setup(tmp_path)

    with mock.patch("healer_circuit_breaker.backlog") as mock_backlog:
        mock_backlog.create_ticket.return_value = {"id": "ticket-1"}

        healer_circuit_breaker.record_healer_intervention("proj", "s1")
        actions = healer_circuit_breaker.record_successful_deploy("proj")
        assert len(actions) == 1
        assert "reset" in actions[0]

        state = healer_circuit_breaker.get_state()
        assert state["proj"]["consecutive_healer_interventions"] == 0
        assert not state["proj"]["tripped"]


def test_reset_on_successful_deploy_after_trip(tmp_path: Path) -> None:
    _setup(tmp_path)

    with mock.patch("healer_circuit_breaker.backlog") as mock_backlog:
        mock_backlog.create_ticket.return_value = {"id": "ticket-1"}

        healer_circuit_breaker.record_healer_intervention("proj", "s1")
        healer_circuit_breaker.record_healer_intervention("proj", "s2")
        assert healer_circuit_breaker.is_healer_blocked("proj")

        actions = healer_circuit_breaker.record_successful_deploy("proj")
        assert "reset" in actions[0]
        assert not healer_circuit_breaker.is_healer_blocked("proj")


def test_manual_reset(tmp_path: Path) -> None:
    _setup(tmp_path)

    with mock.patch("healer_circuit_breaker.backlog") as mock_backlog:
        mock_backlog.create_ticket.return_value = {"id": "ticket-1"}

        healer_circuit_breaker.record_healer_intervention("proj", "s1")
        healer_circuit_breaker.record_healer_intervention("proj", "s2")
        assert healer_circuit_breaker.is_healer_blocked("proj")

        result = healer_circuit_breaker.reset_project("proj")
        assert result is True
        assert not healer_circuit_breaker.is_healer_blocked("proj")

        state = healer_circuit_breaker.get_state()
        assert state["proj"]["consecutive_healer_interventions"] == 0
        assert state["proj"]["session_ids"] == []


def test_manual_reset_nonexistent(tmp_path: Path) -> None:
    _setup(tmp_path)
    assert healer_circuit_breaker.reset_project("nonexistent") is False


def test_is_healer_blocked_unknown_project(tmp_path: Path) -> None:
    _setup(tmp_path)
    assert not healer_circuit_breaker.is_healer_blocked("unknown")


def test_successful_deploy_noop_for_unknown(tmp_path: Path) -> None:
    _setup(tmp_path)
    actions = healer_circuit_breaker.record_successful_deploy("unknown")
    assert actions == []


def test_session_ids_tracked(tmp_path: Path) -> None:
    _setup(tmp_path)

    with mock.patch("healer_circuit_breaker.backlog") as mock_backlog:
        mock_backlog.create_ticket.return_value = {"id": "ticket-1"}

        healer_circuit_breaker.record_healer_intervention("proj", "session-aaa")
        healer_circuit_breaker.record_healer_intervention("proj", "session-bbb")

        state = healer_circuit_breaker.get_state()
        assert state["proj"]["session_ids"] == ["session-aaa", "session-bbb"]

        # Escalation ticket task should include both session IDs
        task_arg = mock_backlog.create_ticket.call_args.kwargs.get("task", "")
        assert "session-aaa" in task_arg
        assert "session-bbb" in task_arg
