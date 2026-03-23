"""Archived project registry — exclude completed projects from dispatch rotation.

When a project is marked as archived, it is excluded from:
- get_known_projects() (stops appearing in project lists)
- project health dashboard (stops generating health alerts)
- auto-dispatch (no new tickets dispatched)

Archived projects retain their artifact history — archival is a soft filter,
not a deletion.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from config import settings

logger = logging.getLogger("dispatch-factory.archived-projects")

ARCHIVE_FILE = "archived-projects.json"


def _archive_path() -> Path:
    return Path(settings.artifacts_dir) / ARCHIVE_FILE


def _read_state() -> dict[str, dict]:
    """Read archived projects state. Keys are project names."""
    path = _archive_path()
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _write_state(state: dict[str, dict]) -> None:
    path = _archive_path()
    path.write_text(json.dumps(state, indent=2))


def is_archived(project: str) -> bool:
    """Check if a project is archived."""
    return project in _read_state()


def get_archived() -> dict[str, dict]:
    """Return all archived projects with metadata."""
    return _read_state()


def archive_project(project: str, reason: str = "") -> bool:
    """Mark a project as archived. Returns False if already archived."""
    state = _read_state()
    if project in state:
        return False
    state[project] = {
        "archived_at": time.time(),
        "reason": reason,
    }
    _write_state(state)
    logger.info("Archived project %s: %s", project, reason)
    return True


def unarchive_project(project: str) -> bool:
    """Remove a project from the archive. Returns False if not archived."""
    state = _read_state()
    if project not in state:
        return False
    del state[project]
    _write_state(state)
    logger.info("Unarchived project %s", project)
    return True
