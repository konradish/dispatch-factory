"""Project health dashboard — per-project metrics derived from artifacts.

Tracks:
- Last successful deploy date
- Consecutive deploy failures
- Days since last dispatch
- Open PR count (via gh CLI)
"""

from __future__ import annotations

import subprocess
import time
from datetime import datetime, timezone

import artifacts
import circuit_breaker
import cleared_healed_sessions


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

        # Count healed-but-unverified sessions (healed + completed, not deployed),
        # excluding sessions that have already been reviewed and cleared.
        cleared_ids = cleared_healed_sessions.get_cleared_ids()
        healed_unverified = [
            s for s in proj_sessions
            if s.get("summary", {}).get("healed", False)
            and s["state"] == "completed"
            and s["id"] not in cleared_ids
        ]

        # Health score: flag neglected or troubled projects
        alerts: list[str] = []
        if days_since_dispatch is not None and days_since_dispatch > 7:
            alerts.append("neglected")
        if consecutive_failures >= 2:
            alerts.append("deploy_broken")
        if tripped:
            alerts.append("circuit_breaker_tripped")
        if open_prs is not None and open_prs > 5:
            alerts.append("pr_backlog")
        if healed_unverified:
            alerts.append("healed_deploy_unverified")

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
            "alerts": alerts,
        })

    return results
