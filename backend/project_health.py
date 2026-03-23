"""Project health dashboard — per-project metrics derived from artifacts.

Tracks:
- Last successful deploy date
- Consecutive deploy failures
- Days since last dispatch
- Open PR count (via gh CLI)
"""

from __future__ import annotations

import logging
import subprocess
import time
from datetime import datetime, timezone

import artifacts
import circuit_breaker
import cleared_healed_sessions
import empty_backlog_detector
import paused_projects

logger = logging.getLogger("dispatch-factory.project-health")


def _session_lacks_deploy_verification(session: dict) -> bool:
    """Return True if a healed session has no evidence of successful deploy.

    A session with a verifier artifact reporting DEPLOYED status has been
    verified — the heal did not skip the deploy.  Only sessions where the
    verifier is missing or reports a non-DEPLOYED status truly lack
    deploy verification.
    """
    verifier = session.get("artifacts", {}).get("verifier")
    if not isinstance(verifier, dict):
        return True  # no verifier at all
    return verifier.get("status") != "DEPLOYED"


def _days_ago(timestamp: float) -> float:
    """Return how many days ago a Unix timestamp was."""
    return (time.time() - timestamp) / 86400


def _count_open_prs(project: str) -> int | None:
    """Count open PRs for a project via gh CLI. Returns None if unavailable."""
    try:
        result = subprocess.run(
            ["gh", "pr", "list", "--repo", f"konradish/{project}",
             "--state", "open", "--json", "number", "--limit", "100"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            import json
            prs = json.loads(result.stdout)
            return len(prs)
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        pass
    return None


def get_project_health() -> list[dict]:
    """Compute health metrics for all known projects (excludes archived)."""
    projects = artifacts.get_known_projects()  # already filters archived
    sessions = artifacts.list_sessions_with_timestamps()
    cb_state = circuit_breaker.get_state()

    # Group sessions by project
    by_project: dict[str, list[dict]] = {p: [] for p in projects}
    for s in sessions:
        p = s["project"]
        if p not in by_project:
            by_project[p] = []
        by_project[p].append(s)

    paused = paused_projects.get_paused()

    # Projects flagged for empty backlog + HUMAN INPUT NEEDED
    empty_backlog_projects = {e["project"] for e in empty_backlog_detector.detect()}

    # Read cleared healed-session IDs once (not per-project)
    cleared_ids = cleared_healed_sessions.get_cleared_ids()
    logger.debug(
        "Health check: loaded %d cleared healed-session IDs", len(cleared_ids),
    )

    results = []
    for project in sorted(by_project):
        proj_sessions = by_project[project]

        # Last successful deploy
        deployed = [
            s for s in proj_sessions
            if s["state"] == "deployed"
        ]
        last_deploy_ts = max((s["mtime"] for s in deployed), default=None)
        last_deploy_date = (
            datetime.fromtimestamp(last_deploy_ts, tz=timezone.utc).isoformat()
            if last_deploy_ts else None
        )

        # Consecutive deploy failures from circuit breaker
        cb = cb_state.get(project, {})
        consecutive_failures = cb.get("consecutive_failures", 0)
        tripped = cb.get("tripped", False)

        # Days since last dispatch (any session type)
        last_dispatch_ts = max((s["mtime"] for s in proj_sessions), default=None)
        days_since_dispatch = (
            round(_days_ago(last_dispatch_ts), 1)
            if last_dispatch_ts else None
        )

        # Open PR count (best-effort)
        open_prs = _count_open_prs(project)

        # Health score: flag neglected or troubled projects
        alerts: list[str] = []
        is_paused = project in paused
        if days_since_dispatch is not None and days_since_dispatch > 7 and not is_paused:
            alerts.append("neglected")
        if consecutive_failures >= 2:
            alerts.append("deploy_broken")
        if tripped:
            alerts.append("circuit_breaker_tripped")
        if open_prs is not None and open_prs > 5:
            alerts.append("pr_backlog")

        # Count healed-but-unverified sessions (healed + completed, not deployed),
        # excluding sessions that have already been reviewed and cleared.
        # Guard: skip for paused projects entirely — nobody is actively managing
        # deploys so the alert just accumulates noise and regenerates after clears.
        if not is_paused:
            healed_unverified = [
                s for s in proj_sessions
                if s.get("summary", {}).get("healed", False)
                and s["state"] == "completed"
                and s["id"] not in cleared_ids
                and _session_lacks_deploy_verification(s)
            ]
            if healed_unverified:
                alerts.append("healed_deploy_unverified")
                logger.warning(
                    "healed_deploy_unverified alert for %s: %d sessions "
                    "not in cleared registry (session_ids=%s, cleared_ids_count=%d)",
                    project,
                    len(healed_unverified),
                    [s["id"] for s in healed_unverified],
                    len(cleared_ids),
                )

        # Empty backlog: project needs human direction but has no pending tickets
        if project in empty_backlog_projects:
            alerts.append("empty_backlog")

        results.append({
            "project": project,
            "last_successful_deploy": last_deploy_date,
            "days_since_last_deploy": (
                round(_days_ago(last_deploy_ts), 1)
                if last_deploy_ts else None
            ),
            "consecutive_deploy_failures": consecutive_failures,
            "circuit_breaker_tripped": tripped,
            "days_since_last_dispatch": days_since_dispatch,
            "last_dispatch_date": (
                datetime.fromtimestamp(last_dispatch_ts, tz=timezone.utc).isoformat()
                if last_dispatch_ts else None
            ),
            "open_prs": open_prs,
            "total_sessions": len(proj_sessions),
            "paused": is_paused,
            "alerts": alerts,
        })

    return results
