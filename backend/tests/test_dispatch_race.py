"""Tests for duplicate-dispatch race condition prevention.

Verifies:
- Per-ticket lock blocks concurrent dispatch of the same ticket
- Compare-and-swap rejects dispatch when ticket is already dispatching/dispatched
- Lock is cleaned up after dispatch completes (no unbounded growth)
- Independent tickets can still dispatch concurrently
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest import mock

import pytest


@pytest.fixture(autouse=True)
def _setup(tmp_path: Path, monkeypatch):
    """Set up test environment with temp artifacts dir and mock subprocess."""
    import db
    from config import settings

    settings.artifacts_dir = str(tmp_path)
    db._db_path = tmp_path / "factory.db"
    db.init_db()

    # Reset foreman lock state between tests
    import foreman
    foreman._dispatch_locks.clear()


def test_concurrent_dispatch_same_ticket_blocked():
    """Two concurrent dispatches of the same ticket: only one should proceed."""
    import backlog
    import foreman

    ticket = backlog.create_ticket("test task", "test-project")
    ticket_id = ticket["id"]

    results = [None, None]
    barrier = threading.Barrier(2, timeout=5)

    original_popen = foreman.subprocess.Popen

    def slow_popen(*args, **kwargs):
        """Popen mock that blocks briefly to widen the race window."""
        time.sleep(0.1)
        return original_popen(*args, **kwargs)

    with mock.patch.object(foreman.subprocess, "Popen", side_effect=FileNotFoundError("test")):
        def dispatch(idx):
            barrier.wait()
            results[idx] = foreman._dispatch_async(["echo", "test"], ticket_id)

        t1 = threading.Thread(target=dispatch, args=(0,))
        t2 = threading.Thread(target=dispatch, args=(1,))
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

    statuses = [r["status"] for r in results]
    # One should succeed (error from FileNotFoundError mock), one should be blocked
    assert "already_dispatching" in statuses, f"Expected one blocked dispatch, got: {results}"
    # Exactly one of each
    assert statuses.count("already_dispatching") == 1
    assert statuses.count("error") == 1


def test_dispatch_rejects_non_pending_ticket():
    """Dispatch should reject a ticket that is already dispatching."""
    import backlog
    import foreman

    ticket = backlog.create_ticket("test task", "test-project")
    ticket_id = ticket["id"]

    # Manually set ticket to dispatching (simulating a prior dispatch)
    backlog.update_ticket(ticket_id, {"status": "dispatching"})

    result = foreman._dispatch_async(["echo", "test"], ticket_id)
    assert result["status"] == "already_dispatching"
    assert "not pending/ready" in result["detail"]


def test_dispatch_rejects_dispatched_ticket():
    """Dispatch should reject a ticket that is already dispatched."""
    import backlog
    import foreman

    ticket = backlog.create_ticket("test task", "test-project")
    ticket_id = ticket["id"]

    backlog.update_ticket(ticket_id, {"status": "dispatched", "session_id": "worker-test-1234"})

    result = foreman._dispatch_async(["echo", "test"], ticket_id)
    assert result["status"] == "already_dispatching"


def test_lock_cleanup_after_dispatch():
    """Lock should be cleaned up after dispatch completes or errors."""
    import foreman

    # After a failed dispatch (FileNotFoundError), lock should be released
    import backlog
    ticket = backlog.create_ticket("test task", "test-project")
    ticket_id = ticket["id"]

    with mock.patch.object(foreman.subprocess, "Popen", side_effect=FileNotFoundError("test")):
        result = foreman._dispatch_async(["echo", "test"], ticket_id)

    assert result["status"] == "error"
    # Lock should be cleaned up
    assert ticket_id not in foreman._dispatch_locks


def test_independent_tickets_dispatch_concurrently():
    """Two different tickets should be able to dispatch at the same time."""
    import backlog
    import foreman

    t1 = backlog.create_ticket("task 1", "project-a")
    t2 = backlog.create_ticket("task 2", "project-b")

    results = [None, None]

    with mock.patch.object(foreman.subprocess, "Popen", side_effect=FileNotFoundError("test")):
        def dispatch(idx, tid):
            results[idx] = foreman._dispatch_async(["echo", "test"], tid)

        th1 = threading.Thread(target=dispatch, args=(0, t1["id"]))
        th2 = threading.Thread(target=dispatch, args=(1, t2["id"]))
        th1.start()
        th2.start()
        th1.join(timeout=5)
        th2.join(timeout=5)

    # Both should get through (both error from FileNotFoundError, but neither blocked)
    assert results[0]["status"] == "error"
    assert results[1]["status"] == "error"
