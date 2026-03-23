"""Empty backlog detector — flags when the factory silently idles on product work.

When pending product tickets reach 0 and the direction vector contains
'HUMAN INPUT NEEDED' for active (non-paused, non-archived) projects,
this module escalates a flag_human reminder every 24 hours until
direction is provided.

The detection is per-project: a project is flagged only if:
1. It has 'HUMAN INPUT NEEDED' in the direction vector
2. It has zero pending backlog tickets
3. It is not paused or archived
4. At least 24 hours have elapsed since the last flag for that project
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

import backlog
import paused_projects
from config import settings

logger = logging.getLogger("dispatch-factory.empty-backlog-detector")

STATE_FILE = "empty-backlog-flags.json"

# Minimum interval between flag_human escalations per project (seconds).
FLAG_INTERVAL_SECONDS = 24 * 60 * 60


def _state_path() -> Path:
    return Path(settings.artifacts_dir) / STATE_FILE


def _read_state() -> dict[str, dict]:
    """Read flag state. Keys are project names, values have last_flagged_at."""
    path = _state_path()
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _write_state(state: dict[str, dict]) -> None:
    path = _state_path()
    path.write_text(json.dumps(state, indent=2))


def _parse_direction_for_human_input(direction: str) -> list[str]:
    """Extract project names that have HUMAN INPUT NEEDED in the direction vector.

    Looks for lines like:
      - project-name: HUMAN INPUT NEEDED
      - **project-name** — HUMAN INPUT NEEDED
      - project-name ... HUMAN INPUT NEEDED ...

    Returns list of project names found.
    """
    projects: list[str] = []
    for line in direction.splitlines():
        if "HUMAN INPUT NEEDED" not in line.upper():
            continue
        # Try to extract a project name (kebab-case identifier) from the line.
        # Common patterns in direction vectors:
        #   - `project-name: ...`
        #   - `**project-name** ...`
        #   - `- project-name — ...`
        match = re.search(r"\*{0,2}([a-z][a-z0-9-]+)\*{0,2}", line)
        if match:
            projects.append(match.group(1))
    return projects


def _read_direction() -> str:
    """Read the direction vector file."""
    direction_file = Path(settings.artifacts_dir) / "autopilot-direction.md"
    if direction_file.is_file():
        return direction_file.read_text().strip()
    return ""


def detect() -> list[dict]:
    """Detect projects with empty backlogs that need human direction.

    Returns a list of dicts with:
      - project: str
      - last_flagged_at: float | None
      - should_flag: bool (True if 24h cooldown has elapsed)
    """
    direction = _read_direction()
    if not direction:
        return []

    human_input_projects = _parse_direction_for_human_input(direction)
    if not human_input_projects:
        return []

    paused = paused_projects.get_paused()
    pending_tickets = backlog.list_tickets(status="pending")
    pending_by_project: dict[str, int] = {}
    for t in pending_tickets:
        pending_by_project[t["project"]] = pending_by_project.get(t["project"], 0) + 1

    state = _read_state()
    now = time.time()
    results = []

    for project in human_input_projects:
        # Skip paused projects
        if project in paused:
            continue

        # Only flag if zero pending tickets for this project
        if pending_by_project.get(project, 0) > 0:
            continue

        last_flagged = state.get(project, {}).get("last_flagged_at")
        elapsed = (now - last_flagged) if last_flagged else float("inf")
        should_flag = elapsed >= FLAG_INTERVAL_SECONDS

        results.append({
            "project": project,
            "last_flagged_at": last_flagged,
            "should_flag": should_flag,
        })

    return results


def record_flag(project: str) -> None:
    """Record that a flag_human was sent for a project."""
    state = _read_state()
    state[project] = {"last_flagged_at": time.time()}
    _write_state(state)


def clear_flag(project: str) -> bool:
    """Clear the flag state for a project (e.g. when direction is provided)."""
    state = _read_state()
    if project not in state:
        return False
    del state[project]
    _write_state(state)
    logger.info("Cleared empty-backlog flag for %s", project)
    return True


def get_state() -> dict[str, dict]:
    """Return current flag state for all projects."""
    return _read_state()
