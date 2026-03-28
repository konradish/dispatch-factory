"""Backlog ticket management — the factory's work queue.

Backed by SQLite via db.py. Same public API as the original JSON version.
"""

from __future__ import annotations

import json
import time
import uuid

import db


def list_tickets(status: str | None = None) -> list[dict]:
    """List all backlog tickets, optionally filtered by status."""
    with db.get_conn() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM tickets WHERE status = ? ORDER BY created_at", (status,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM tickets ORDER BY created_at").fetchall()
        return [db.row_to_ticket(r, db.get_ticket_notes(conn, r["id"])) for r in rows]


def create_ticket(
    task: str,
    project: str,
    priority: str = "normal",
    flags: list[str] | None = None,
    source: str = "manual",
    status: str = "pending",
    task_type: str = "code",
) -> dict:
    """Create a new backlog ticket."""
    ticket_id = uuid.uuid4().hex[:8]
    now = time.time()
    with db.get_conn() as conn:
        conn.execute(
            """INSERT INTO tickets (id, task, project, priority, task_type, flags, tags, status, source, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ticket_id, task, project, priority, task_type, json.dumps(flags or []), "[]", status, source, now),
        )
    return {
        "id": ticket_id,
        "task": task,
        "project": project,
        "priority": priority,
        "task_type": task_type,
        "flags": flags or [],
        "tags": [],
        "status": status,
        "source": source,
        "hold_reason": None,
        "notes": [],
        "session_id": None,
        "created_at": now,
        "dispatched_at": None,
        "completed_at": None,
    }


def add_note(ticket_id: str, text: str, author: str = "human") -> dict | None:
    """Append a note to a ticket's notes list. Returns updated ticket or None."""
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if not row:
            return None
        conn.execute(
            "INSERT INTO ticket_notes (ticket_id, text, author, timestamp) VALUES (?, ?, ?, ?)",
            (ticket_id, text, author, time.time()),
        )
        notes = db.get_ticket_notes(conn, ticket_id)
        return db.row_to_ticket(row, notes)


def update_ticket(ticket_id: str, updates: dict) -> dict | None:
    """Update a ticket by ID. Returns updated ticket or None if not found."""
    # Map of fields that need JSON serialization
    json_fields = {"flags", "tags"}
    allowed = {"task", "project", "priority", "task_type", "flags", "tags", "status", "source",
               "hold_reason", "session_id", "dispatched_at", "completed_at"}

    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if not row:
            return None

        sets = []
        vals = []
        for k, v in updates.items():
            if k in allowed:
                if k in json_fields:
                    sets.append(f"{k} = ?")
                    vals.append(json.dumps(v))
                else:
                    sets.append(f"{k} = ?")
                    vals.append(v)

        if sets:
            vals.append(ticket_id)
            conn.execute(f"UPDATE tickets SET {', '.join(sets)} WHERE id = ?", vals)

        row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        notes = db.get_ticket_notes(conn, ticket_id)
        return db.row_to_ticket(row, notes)


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
    with db.get_conn() as conn:
        if project:
            row = conn.execute(
                """SELECT * FROM tickets WHERE status = 'pending' AND project = ?
                   ORDER BY CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END, created_at
                   LIMIT 1""",
                (project,),
            ).fetchone()
        else:
            row = conn.execute(
                """SELECT * FROM tickets WHERE status = 'pending'
                   ORDER BY CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END, created_at
                   LIMIT 1""",
            ).fetchone()
        if not row:
            return None
        return db.row_to_ticket(row, db.get_ticket_notes(conn, row["id"]))


def has_inflight_ticket(project: str) -> bool:
    """Check if a project already has a dispatched or dispatching (in-flight) ticket."""
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM tickets WHERE status IN ('dispatched', 'dispatching') AND project = ? LIMIT 1",
            (project,),
        ).fetchone()
        return row is not None


def has_eligible_higher_priority(priority: str) -> bool:
    """Check if any pending tickets exist with strictly higher priority that are eligible."""
    import circuit_breaker

    priority_order = {"urgent": 0, "high": 1, "normal": 2, "low": 3}
    current_rank = priority_order.get(priority, 2)
    if current_rank == 0:
        return False

    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM tickets WHERE status = 'pending'").fetchall()
        for r in rows:
            t_rank = priority_order.get(r["priority"], 2)
            if t_rank >= current_rank:
                continue
            project = r["project"]
            if has_inflight_ticket(project):
                continue
            if circuit_breaker.is_project_blocked(project):
                continue
            return True
    return False


def delete_ticket(ticket_id: str) -> bool:
    """Delete a ticket by ID."""
    with db.get_conn() as conn:
        conn.execute("DELETE FROM ticket_notes WHERE ticket_id = ?", (ticket_id,))
        cursor = conn.execute("DELETE FROM tickets WHERE id = ?", (ticket_id,))
        return cursor.rowcount > 0
