"""Registry of healed-but-unverified sessions that have been reviewed and cleared.

When a healed session completes without a verified deploy, the heartbeat creates
an escalation ticket and the project health dashboard shows a healed_deploy_unverified
alert.  Once the session has been investigated (or auto-escalated), it can be
recorded here so the dashboard alert stops firing for it.

Without this registry, the alert accumulates across all projects forever —
every project that ever had a single healed-but-unverified session keeps the
alert indefinitely, diluting its signal.

Cleared sessions retain their artifacts — clearing is a soft acknowledgment,
not a deletion.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from config import settings

logger = logging.getLogger("dispatch-factory.cleared-healed-sessions")

CLEARED_FILE = "cleared-healed-sessions.json"


def _cleared_path() -> Path:
    return Path(settings.artifacts_dir) / CLEARED_FILE


def _read_state() -> dict[str, dict]:
    """Read cleared sessions state. Keys are session IDs."""
    path = _cleared_path()
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _write_state(state: dict[str, dict]) -> None:
    path = _cleared_path()
    path.write_text(json.dumps(state, indent=2))


def is_cleared(session_id: str) -> bool:
    """Check if a healed session has been cleared."""
    return session_id in _read_state()


def get_cleared() -> dict[str, dict]:
    """Return all cleared sessions with metadata."""
    return _read_state()


def get_cleared_ids() -> set[str]:
    """Return just the set of cleared session IDs (for fast filtering)."""
    return set(_read_state())


def clear_session(session_id: str, reason: str = "", source: str = "manual") -> bool:
    """Mark a healed session as cleared/acknowledged. Returns False if already cleared."""
    state = _read_state()
    if session_id in state:
        return False
    state[session_id] = {
        "cleared_at": time.time(),
        "reason": reason,
        "source": source,
    }
    _write_state(state)
    logger.info("Cleared healed session %s: %s (source=%s)", session_id, reason, source)
    return True


def clear_project_sessions(
    project: str,
    session_ids: list[str],
    reason: str = "",
    source: str = "manual",
) -> int:
    """Clear multiple sessions for a project at once. Returns count of newly cleared."""
    state = _read_state()
    count = 0
    now = time.time()
    for sid in session_ids:
        if sid not in state:
            state[sid] = {
                "cleared_at": now,
                "reason": reason,
                "project": project,
                "source": source,
            }
            count += 1
    if count > 0:
        _write_state(state)
        logger.info(
            "Cleared %d healed sessions for %s: %s (source=%s)",
            count, project, reason, source,
        )
    return count


def unclear_session(session_id: str) -> bool:
    """Remove a session from the cleared registry. Returns False if not cleared."""
    state = _read_state()
    if session_id not in state:
        return False
    del state[session_id]
    _write_state(state)
    logger.info("Uncleared healed session %s", session_id)
    return True
