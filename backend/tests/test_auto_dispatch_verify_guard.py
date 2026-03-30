"""Tests for verify task_type guard in _auto_dispatch().

Verifies that tickets with task_type='verify' are skipped by _auto_dispatch()
since the dispatch binary doesn't support the verify task type.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _setup(tmp_path: Path, monkeypatch):
    """Set up test environment with temp artifacts dir and mock subprocess."""
    import db
    from config import settings

    settings.artifacts_dir = str(tmp_path)
    db._db_path = tmp_path / "factory.db"
    db.init_db()


def test_auto_dispatch_skips_verify_tickets():
    """Verify tickets should be skipped with an explanatory action message."""
    import backlog
    import heartbeat

    ticket = backlog.create_ticket("verify something", "test-project", task_type="verify")

    actions = heartbeat._auto_dispatch()

    assert any(
        ticket["id"] in a and "verify task_type not dispatchable" in a
        for a in actions
    ), f"Expected verify skip action for {ticket['id']}, got: {actions}"

    # Ticket should still be pending (not dispatched)
    t = next(t for t in backlog.list_tickets() if t["id"] == ticket["id"])
    assert t["status"] == "pending"
