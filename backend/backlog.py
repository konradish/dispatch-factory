"""Backlog ticket management — the factory's work queue."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from config import settings

BACKLOG_FILE = "factory-backlog.json"


def _backlog_path() -> Path:
    return Path(settings.artifacts_dir) / BACKLOG_FILE


def _read_backlog() -> list[dict]:
    path = _backlog_path()
    if not path.is_file():
        return []
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def _write_backlog(tickets: list[dict]) -> None:
    path = _backlog_path()
    path.write_text(json.dumps(tickets, indent=2))


def list_tickets(status: str | None = None) -> list[dict]:
    """List all backlog tickets, optionally filtered by status."""
    tickets = _read_backlog()
    if status:
        tickets = [t for t in tickets if t.get("status") == status]
    return tickets


def create_ticket(
    task: str,
    project: str,
    priority: str = "normal",
    flags: list[str] | None = None,
    source: str = "manual",
) -> dict:
    """Create a new backlog ticket."""
    ticket = {
        "id": uuid.uuid4().hex[:8],
        "task": task,
        "project": project,
        "priority": priority,  # low, normal, high, urgent
        "flags": flags or [],
        "status": "pending",  # intake, needs_input, ready, pending, dispatched, completed, failed, cancelled
        "source": source,  # manual, heartbeat, auto
        "session_id": None,
        "created_at": time.time(),
        "dispatched_at": None,
        "completed_at": None,
    }
    tickets = _read_backlog()
    tickets.append(ticket)
    _write_backlog(tickets)
    return ticket


def update_ticket(ticket_id: str, updates: dict) -> dict | None:
    """Update a ticket by ID. Returns updated ticket or None if not found."""
    tickets = _read_backlog()
    for t in tickets:
        if t["id"] == ticket_id:
            for k, v in updates.items():
                if k in t and k != "id":
                    t[k] = v
            _write_backlog(tickets)
            return t
    return None


def mark_dispatched(ticket_id: str, session_id: str) -> dict | None:
    """Mark a ticket as dispatched with its session ID."""
    return update_ticket(ticket_id, {
        "status": "dispatched",
        "session_id": session_id,
        "dispatched_at": time.time(),
    })


def mark_completed(ticket_id: str, status: str = "completed") -> dict | None:
    """Mark a ticket as completed or failed."""
    return update_ticket(ticket_id, {
        "status": status,
        "completed_at": time.time(),
    })


def next_pending(project: str | None = None) -> dict | None:
    """Get the highest-priority pending ticket, optionally for a specific project."""
    tickets = _read_backlog()
    pending = [t for t in tickets if t["status"] == "pending"]
    if project:
        pending = [t for t in pending if t["project"] == project]
    if not pending:
        return None

    priority_order = {"urgent": 0, "high": 1, "normal": 2, "low": 3}
    pending.sort(key=lambda t: (priority_order.get(t.get("priority", "normal"), 2), t["created_at"]))
    return pending[0]


def has_inflight_ticket(project: str) -> bool:
    """Check if a project already has a dispatched (in-flight) ticket."""
    dispatched = list_tickets(status="dispatched")
    return any(t["project"] == project for t in dispatched)


def has_eligible_higher_priority(priority: str) -> bool:
    """Check if any pending tickets exist with strictly higher priority that are eligible for dispatch.

    A pending ticket is eligible if its project is not circuit-broken and has no
    in-flight ticket.  This prevents priority inversion: lower-priority work
    should not consume capacity when higher-priority work is waiting.
    """
    import circuit_breaker

    priority_order = {"urgent": 0, "high": 1, "normal": 2, "low": 3}
    current_rank = priority_order.get(priority, 2)
    if current_rank == 0:
        return False  # Nothing is higher than urgent

    pending = list_tickets(status="pending")
    for t in pending:
        t_rank = priority_order.get(t.get("priority", "normal"), 2)
        if t_rank >= current_rank:
            continue  # Not higher priority
        # Check eligibility: project not blocked and no in-flight ticket
        project = t["project"]
        if has_inflight_ticket(project):
            continue
        if circuit_breaker.is_project_blocked(project):
            continue
        return True
    return False


def delete_ticket(ticket_id: str) -> bool:
    """Delete a ticket by ID."""
    tickets = _read_backlog()
    original_len = len(tickets)
    tickets = [t for t in tickets if t["id"] != ticket_id]
    if len(tickets) < original_len:
        _write_backlog(tickets)
        return True
    return False
