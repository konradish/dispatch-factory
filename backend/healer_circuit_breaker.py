"""Healer circuit breaker — disable healer for projects with repeated healer spirals.

If healer has intervened N times consecutively on the same project without a
successful (non-healed) deploy following, the healer is disabled for that project
and an escalation ticket is created. The deploy circuit breaker blocks ALL
dispatches; this only disables the healer (dispatch continues with --no-heal).

Motivated by the electricapp healer spiral (sessions 0844->1028->1033) where
the healer compounded damage when the root cause wasn't healable.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import backlog
from config import settings

logger = logging.getLogger("dispatch-factory.healer-circuit-breaker")

CIRCUIT_FILE = "healer-circuit-breaker.json"
HEALER_INTERVENTION_THRESHOLD = 2


def _circuit_path() -> Path:
    return Path(settings.artifacts_dir) / CIRCUIT_FILE


def _read_state() -> dict[str, dict]:
    """Read healer circuit breaker state. Keys are project names."""
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


def _ensure_entry(state: dict[str, dict], project: str) -> dict:
    """Ensure a project entry exists in state, creating if needed."""
    if project not in state:
        state[project] = {
            "consecutive_healer_interventions": 0,
            "tripped": False,
            "tripped_at": None,
            "escalation_ticket_id": None,
            "session_ids": [],
            "last_updated": time.time(),
        }
    return state[project]


def record_healer_intervention(project: str, session_id: str) -> list[str]:
    """Record a healer intervention for a project. Returns list of actions taken."""
    actions: list[str] = []
    state = _read_state()
    entry = _ensure_entry(state, project)

    entry["consecutive_healer_interventions"] += 1
    entry["session_ids"].append(session_id)
    actions.append(
        f"healer-circuit-breaker: {project} healer intervention "
        f"#{entry['consecutive_healer_interventions']} ({session_id})"
    )

    if (
        entry["consecutive_healer_interventions"] >= HEALER_INTERVENTION_THRESHOLD
        and not entry["tripped"]
    ):
        entry["tripped"] = True
        entry["tripped_at"] = time.time()

        session_list = ", ".join(entry["session_ids"])
        ticket = backlog.create_ticket(
            task=(
                f"Healer circuit breaker tripped for {project} — "
                f"{entry['consecutive_healer_interventions']} consecutive healer "
                f"interventions without successful deploy. "
                f"Sessions: {session_list}. "
                f"Root cause is likely not healable; disable healer and investigate."
            ),
            project=project,
            priority="high",
            source="healer-circuit-breaker",
        )
        entry["escalation_ticket_id"] = ticket["id"]
        actions.append(
            f"healer-circuit-breaker: TRIPPED for {project}, "
            f"created escalation ticket {ticket['id']}"
        )
        logger.warning(
            "Healer circuit breaker tripped for %s after %d consecutive "
            "interventions (sessions: %s), ticket %s",
            project,
            entry["consecutive_healer_interventions"],
            session_list,
            ticket["id"],
        )

    entry["last_updated"] = time.time()
    _write_state(state)
    return actions


def record_successful_deploy(project: str) -> list[str]:
    """Reset healer intervention counter on a successful non-healed deploy."""
    actions: list[str] = []
    state = _read_state()

    if project not in state:
        return actions

    entry = state[project]
    if entry["consecutive_healer_interventions"] > 0 or entry["tripped"]:
        actions.append(
            f"healer-circuit-breaker: {project} reset "
            f"(non-healed deploy succeeded)"
        )
        logger.info(
            "Healer circuit breaker reset for %s (non-healed deploy succeeded)",
            project,
        )

    entry["consecutive_healer_interventions"] = 0
    entry["tripped"] = False
    entry["tripped_at"] = None
    entry["escalation_ticket_id"] = None
    entry["session_ids"] = []
    entry["last_updated"] = time.time()
    _write_state(state)
    return actions


def is_healer_blocked(project: str) -> bool:
    """Check if healer is blocked for a project."""
    state = _read_state()
    entry = state.get(project)
    if not entry:
        return False
    return entry.get("tripped", False)


def get_state() -> dict[str, dict]:
    """Return full healer circuit breaker state for all projects."""
    return _read_state()


def reset_project(project: str) -> bool:
    """Manually reset the healer circuit breaker for a project."""
    state = _read_state()
    if project not in state:
        return False
    state[project] = {
        "consecutive_healer_interventions": 0,
        "tripped": False,
        "tripped_at": None,
        "escalation_ticket_id": None,
        "session_ids": [],
        "last_updated": time.time(),
    }
    _write_state(state)
    logger.info("Healer circuit breaker manually reset for %s", project)
    return True
