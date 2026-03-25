"""Paused project registry — suppress neglect alerts for intentionally inactive projects.

Unlike archived projects (which are fully removed from health tracking and dispatch),
paused projects remain visible in the health dashboard but do not trigger neglect
alerts.  This is for projects that are intentionally on hold — not abandoned, just
waiting (e.g. blocked on external input, seasonal, on-hold by decision).

Paused projects still appear in:
- get_known_projects() (visible in project lists)
- project health dashboard (metrics still computed)
- auto-dispatch (tickets can still be dispatched)

Paused projects are excluded from:
- "neglected" alert in project_health (the only suppression)
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from config import settings

logger = logging.getLogger("dispatch-factory.paused-projects")

PAUSED_FILE = "paused-projects.json"

# No hardcoded defaults. Paused state is managed entirely at runtime via
# the foreman's pause_project/unpause_project actions, informed by the
# direction vector. The JSON file is the sole source of truth.


def _paused_path() -> Path:
    return Path(settings.artifacts_dir) / PAUSED_FILE


def _read_state() -> dict[str, dict]:
    """Read paused projects state from runtime JSON. Keys are project names."""
    path = _paused_path()
    if path.is_file():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _write_state(state: dict[str, dict]) -> None:
    path = _paused_path()
    path.write_text(json.dumps(state, indent=2))


def is_paused(project: str) -> bool:
    """Check if a project is paused."""
    return project in _read_state()


def get_paused() -> dict[str, dict]:
    """Return all paused projects with metadata."""
    return _read_state()


def pause_project(project: str, reason: str = "") -> bool:
    """Mark a project as paused. Returns False if already paused."""
    state = _read_state()
    if project in state:
        return False
    state[project] = {
        "paused_at": time.time(),
        "reason": reason,
    }
    _write_state(state)
    logger.info("Paused project %s: %s", project, reason)
    return True


def unpause_project(project: str) -> bool:
    """Remove a project from the paused registry. Returns False if not paused."""
    state = _read_state()
    if project not in state:
        return False
    del state[project]
    _write_state(state)
    logger.info("Unpaused project %s", project)
    return True
