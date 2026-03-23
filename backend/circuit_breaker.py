"""Deploy circuit breaker — block dispatches to projects with consecutive deploy failures.

If a project fails deploy 2x consecutively, a 'fix deploy pipeline' ticket is
auto-created at high priority and further dispatches to that project are blocked
until the circuit is manually reset.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import backlog
from config import settings

logger = logging.getLogger("dispatch-factory.circuit-breaker")

CIRCUIT_FILE = "circuit-breaker.json"
CONSECUTIVE_FAILURE_THRESHOLD = 2


def _circuit_path() -> Path:
    return Path(settings.artifacts_dir) / CIRCUIT_FILE


def _read_state() -> dict[str, dict]:
    """Read circuit breaker state. Keys are project names."""
    path = _circuit_path()
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _write_state(state: dict[str, dict]) -> None:
    path = _circuit_path()
    path.write_text(json.dumps(state, indent=2))


def record_result(project: str, success: bool) -> list[str]:
    """Record a deploy result for a project. Returns list of actions taken."""
    actions: list[str] = []
    state = _read_state()

    if project not in state:
        state[project] = {
            "consecutive_failures": 0,
            "tripped": False,
            "tripped_at": None,
            "fix_ticket_id": None,
            "last_updated": time.time(),
        }

    entry = state[project]

    if success:
        # Reset on success
        if entry["consecutive_failures"] > 0 or entry["tripped"]:
            actions.append(f"circuit-breaker: {project} reset (deploy succeeded)")
        entry["consecutive_failures"] = 0
        entry["tripped"] = False
        entry["tripped_at"] = None
        entry["fix_ticket_id"] = None
    else:
        entry["consecutive_failures"] += 1
        actions.append(
            f"circuit-breaker: {project} failure #{entry['consecutive_failures']}"
        )

        if (
            entry["consecutive_failures"] >= CONSECUTIVE_FAILURE_THRESHOLD
            and not entry["tripped"]
        ):
            entry["tripped"] = True
            entry["tripped_at"] = time.time()

            # Auto-create fix ticket
            ticket = backlog.create_ticket(
                task=f"Fix deploy pipeline for {project} — {CONSECUTIVE_FAILURE_THRESHOLD} consecutive deploy failures",
                project=project,
                priority="high",
                source="circuit-breaker",
            )
            entry["fix_ticket_id"] = ticket["id"]
            actions.append(
                f"circuit-breaker: TRIPPED for {project}, created fix ticket {ticket['id']}"
            )
            logger.warning(
                "Circuit breaker tripped for %s after %d consecutive failures, ticket %s",
                project,
                entry["consecutive_failures"],
                ticket["id"],
            )

    entry["last_updated"] = time.time()
    _write_state(state)
    return actions


def is_project_blocked(project: str) -> bool:
    """Check if a project is blocked by the circuit breaker."""
    state = _read_state()
    entry = state.get(project)
    if not entry:
        return False
    return entry.get("tripped", False)


def get_state() -> dict[str, dict]:
    """Return full circuit breaker state for all projects."""
    return _read_state()


def reset_project(project: str) -> bool:
    """Manually reset the circuit breaker for a project."""
    state = _read_state()
    if project not in state:
        return False
    state[project] = {
        "consecutive_failures": 0,
        "tripped": False,
        "tripped_at": None,
        "fix_ticket_id": None,
        "last_updated": time.time(),
    }
    _write_state(state)
    logger.info("Circuit breaker manually reset for %s", project)
    return True
