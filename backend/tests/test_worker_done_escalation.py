"""Tests for worker_done timeout escalation in heartbeat._reconcile_backlog.

Verifies:
- Sessions in worker_done for <5 min are deferred (existing behavior)
- Sessions in worker_done for >5 min trigger a forced process_worker_completion retry
- If the forced retry also fails, an error result.md is written and ticket is marked failed
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest import mock

import heartbeat


def _make_ticket(ticket_id: str = "T-1", session_id: str = "worker-test-0001", project: str = "testproj") -> dict:
    return {
        "id": ticket_id,
        "session_id": session_id,
        "project": project,
        "task": "test task",
        "status": "dispatched",
        "created_at": time.time() - 600,
    }


def _make_session(state: str = "worker_done") -> dict:
    return {"state": state, "artifacts": {}}


def test_worker_done_fresh_defers(tmp_path: Path) -> None:
    """worker_done session <5 min old should be deferred, not escalated."""
    ticket = _make_ticket()
    sid = ticket["session_id"]

    # Create a fresh worker-done.json (mtime = now)
    worker_done_file = tmp_path / f"{sid}-worker-done.json"
    worker_done_file.write_text(json.dumps({"project": "testproj"}))

    with (
        mock.patch("backlog.list_tickets") as mock_list,
        mock.patch("artifacts.get_session", return_value=_make_session()),
        mock.patch("artifacts._artifacts_path", return_value=tmp_path),
    ):
        # Return dispatching=[] then dispatched=[ticket]
        mock_list.side_effect = lambda status=None: (
            [] if status == "dispatching" else [ticket] if status == "dispatched" else []
        )
        actions = heartbeat._reconcile_backlog()

    # Should not contain escalation actions
    assert not any("escalation" in a for a in actions)


def test_worker_done_stale_retries(tmp_path: Path) -> None:
    """worker_done session >5 min old should trigger forced completion retry."""
    ticket = _make_ticket()
    sid = ticket["session_id"]

    # Create a stale worker-done.json (mtime = 10 min ago)
    worker_done_file = tmp_path / f"{sid}-worker-done.json"
    worker_done_file.write_text(json.dumps({"project": "testproj"}))
    import os
    old_time = time.time() - 600
    os.utime(worker_done_file, (old_time, old_time))

    with (
        mock.patch("backlog.list_tickets") as mock_list,
        mock.patch("artifacts.get_session", return_value=_make_session()),
        mock.patch("artifacts._artifacts_path", return_value=tmp_path),
        mock.patch("pipeline_runner.process_worker_completion", return_value=["pipeline: done"]) as mock_process,
    ):
        mock_list.side_effect = lambda status=None: (
            [] if status == "dispatching" else [ticket] if status == "dispatched" else []
        )
        actions = heartbeat._reconcile_backlog()

    mock_process.assert_called_once()
    assert any("escalation" in a and "retried" in a for a in actions)


def test_worker_done_stale_retry_fails_marks_failed(tmp_path: Path) -> None:
    """If forced retry raises, write error result and mark ticket failed."""
    ticket = _make_ticket()
    sid = ticket["session_id"]

    worker_done_file = tmp_path / f"{sid}-worker-done.json"
    worker_done_file.write_text(json.dumps({"project": "testproj"}))
    import os
    old_time = time.time() - 600
    os.utime(worker_done_file, (old_time, old_time))

    with (
        mock.patch("backlog.list_tickets") as mock_list,
        mock.patch("artifacts.get_session", return_value=_make_session()),
        mock.patch("artifacts._artifacts_path", return_value=tmp_path),
        mock.patch("pipeline_runner.process_worker_completion", side_effect=RuntimeError("boom")),
        mock.patch("pipeline_runner._write_result") as mock_write,
        mock.patch("backlog.mark_completed") as mock_mark,
    ):
        mock_list.side_effect = lambda status=None: (
            [] if status == "dispatching" else [ticket] if status == "dispatched" else []
        )
        actions = heartbeat._reconcile_backlog()

    mock_write.assert_called_once()
    assert "FAILED" in mock_write.call_args[0][1]
    mock_mark.assert_called_once_with(ticket["id"], "failed")
    assert any("failed" in a for a in actions)
