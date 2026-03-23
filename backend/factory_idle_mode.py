"""Factory idle mode — hard stop when ALL active projects need human input.

When every active (non-paused, non-archived) project has HUMAN INPUT NEEDED
in the direction vector AND zero pending backlog tickets, the factory enters
idle mode and refuses ALL dispatches — including meta-work.  No priority
bypass, no exceptions.

This is strictly stronger than the meta-work ratio circuit breaker: that
limits the ratio of self-improvement work; this is a full stop when there
is genuinely nothing to do without human direction.

A single factory-wide flag_human reminder is emitted every 24 hours while
idle mode is active.  This is distinct from the per-project empty-backlog
flags in empty_backlog_detector.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import backlog
import empty_backlog_detector
import paused_projects
import archived_projects
from config import settings

logger = logging.getLogger("dispatch-factory.factory-idle-mode")

STATE_FILE = "factory-idle-flag.json"

# Minimum interval between factory-wide flag_human reminders (seconds).
FLAG_INTERVAL_SECONDS = 24 * 60 * 60


def _state_path() -> Path:
    return Path(settings.artifacts_dir) / STATE_FILE


def _read_state() -> dict:
    """Read factory idle flag state."""
    path = _state_path()
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _write_state(state: dict) -> None:
    path = _state_path()
    path.write_text(json.dumps(state, indent=2))


def _get_active_projects() -> set[str]:
    """Return the set of projects that appear in the direction vector
    and are NOT paused or archived.

    Returns an empty set if the direction file is missing or unreadable,
    which causes is_idle() to return False (safe default).
    """
    direction = empty_backlog_detector._read_direction()
    if not direction:
        return set()

    # Extract ALL project names from the direction vector (not just
    # those with HUMAN INPUT NEEDED).
    import re
    projects: set[str] = set()
    for line in direction.splitlines():
        match = re.search(r"\*{0,2}([a-z][a-z0-9-]+)\*{0,2}", line)
        if match:
            projects.add(match.group(1))

    paused = set(paused_projects.get_paused().keys())
    archived = set(archived_projects.get_archived().keys())
    return projects - paused - archived


def is_idle() -> bool:
    """Check if the factory should be in idle mode.

    Idle mode activates when ALL of:
    1. There is at least one active (non-paused, non-archived) project
    2. Every active project has HUMAN INPUT NEEDED in the direction vector
    3. There are zero pending backlog tickets across all projects

    Returns False (not idle) when:
    - Direction file is missing or unreadable (safe default)
    - Zero active projects (prevents deadlock on first boot)
    - Any active project does NOT have HUMAN INPUT NEEDED
    - Any pending backlog tickets exist
    """
    direction = empty_backlog_detector._read_direction()
    if not direction:
        return False

    active_projects = _get_active_projects()

    # Edge case: empty set — all() on empty returns True, which would
    # deadlock the factory.  Treat zero active projects as NOT idle.
    if not active_projects:
        return False

    # Check that EVERY active project has HUMAN INPUT NEEDED
    human_input_projects = set(
        empty_backlog_detector._parse_direction_for_human_input(direction)
    )
    if not active_projects.issubset(human_input_projects):
        return False

    # Check that there are zero pending backlog tickets (any project)
    pending = backlog.list_tickets(status="pending")
    if pending:
        return False

    return True


def get_state() -> dict:
    """Return current factory idle mode state for the API."""
    idle = is_idle()
    flag_state = _read_state()
    return {
        "idle": idle,
        "last_flagged_at": flag_state.get("last_flagged_at"),
        "flag_type": "factory_idle",
    }


def check_and_flag() -> str | None:
    """Check idle mode and emit a factory-wide flag_human if 24h cooldown elapsed.

    Returns the flag action string if a flag was emitted, None otherwise.
    """
    if not is_idle():
        return None

    state = _read_state()
    now = time.time()
    last_flagged = state.get("last_flagged_at")
    elapsed = (now - last_flagged) if last_flagged else float("inf")

    if elapsed < FLAG_INTERVAL_SECONDS:
        return None

    # Emit flag
    _write_state({"last_flagged_at": now})
    logger.warning(
        "Factory idle mode: ALL active projects need human input and backlog "
        "is empty — emitting factory-wide flag_human reminder"
    )
    return (
        "flag_human(factory_idle): all active projects have HUMAN INPUT NEEDED "
        "and backlog is empty — factory is idle, awaiting human direction"
    )
