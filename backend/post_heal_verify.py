"""Post-heal deploy verification — lightweight health check after healer intervention.

When the healer marks a session as healed and the verifier reports DEPLOYED,
we run a lightweight HTTP health check against the project's deploy URL to
confirm the deploy actually succeeded.  Session 2247 found that 2/3 healed
sessions still had broken deploys — the DEPLOYED status from the verifier
is unreliable after healing because the healer may have retried a partial
deploy or skipped a failing stage.

The verification result is written as a -heal-verified.json artifact and
used by the heartbeat to decide whether to record success or escalate.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from config import settings

logger = logging.getLogger("dispatch-factory.post-heal-verify")

# Cache project URLs to avoid repeated subprocess calls within a heartbeat tick.
_url_cache: dict[str, str | None] = {}
_cache_ts: float = 0.0
_CACHE_TTL = 300.0  # 5 minutes


def _detect_rebase_in_progress(project: str) -> str | None:
    """Check if a git rebase is paused in the project worktree.

    Returns a reason string if rebase is in progress, None otherwise.
    Uses `git rev-parse --git-dir` to resolve the git directory correctly
    even for worktrees with separate git dirs.
    """
    try:
        result = subprocess.run(
            ["git", "-C", _get_project_dir(project), "rev-parse", "--git-dir"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

    if result.returncode != 0:
        return None

    git_dir = Path(result.stdout.strip())
    if not git_dir.is_absolute():
        git_dir = Path(_get_project_dir(project)) / git_dir

    for rebase_dir in ("rebase-merge", "rebase-apply"):
        if (git_dir / rebase_dir).is_dir():
            return f"rebase paused awaiting manual resolution ({rebase_dir})"

    return None


def _get_project_dir(project: str) -> str:
    """Get the project worktree directory from dispatch --projects or settings."""
    # Use the dispatch projects directory convention
    projects_dir = getattr(settings, "projects_dir", None)
    if projects_dir:
        return str(Path(projects_dir) / project)
    return str(Path.home() / "projects" / project)


def _get_project_url(project: str) -> str | None:
    """Get the deploy/health URL for a project from dispatch --projects."""
    global _url_cache, _cache_ts

    if time.time() - _cache_ts > _CACHE_TTL:
        _url_cache.clear()
        _cache_ts = time.time()

    if project in _url_cache:
        return _url_cache[project]

    url = _fetch_project_url(project)
    _url_cache[project] = url
    return url


def _fetch_project_url(project: str) -> str | None:
    """Query dispatch --projects for the project's health check URL."""
    try:
        result = subprocess.run(
            [settings.dispatch_bin, "--projects"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

    if result.returncode != 0:
        return None

    # Parse output for project section — look for local_url or smoke fields
    in_project = False
    for line in result.stdout.splitlines():
        stripped = line.strip()
        # Project headers are unindented names
        if not line.startswith(" ") and stripped:
            in_project = stripped.rstrip(":") == project
            continue
        if in_project:
            # Look for local_url or smoke_url
            for key in ("local_url", "smoke"):
                match = re.match(rf"\s*{key}\s*[:=]\s*(.+)", line)
                if match:
                    url = match.group(1).strip().strip("\"'")
                    if url.startswith("http"):
                        return url

    return None


def verify_deploy(project: str, session_id: str) -> dict:
    """Run a lightweight deploy health check for a healed session.

    Returns a verification result dict:
    - status: "passed" | "failed" | "skipped"
    - reason: human-readable explanation
    - url: the URL checked (if any)
    - http_status: the HTTP status code (if checked)
    - latency_ms: response time in milliseconds (if checked)
    """
    # Pre-verification guard: check for paused rebase before attempting
    # health check.  A paused rebase means the healer's git operation
    # failed — the deploy cannot have succeeded.
    rebase_reason = _detect_rebase_in_progress(project)
    if rebase_reason:
        logger.warning(
            "Rebase in progress for %s (%s) — failing verification: %s",
            project, session_id, rebase_reason,
        )
        return {
            "status": "failed",
            "reason": rebase_reason,
            "url": None,
            "http_status": None,
            "latency_ms": None,
            "session_id": session_id,
            "verified_at": time.time(),
        }

    url = _get_project_url(project)

    if not url:
        return {
            "status": "skipped",
            "reason": f"no health check URL configured for {project}",
            "url": None,
            "http_status": None,
            "latency_ms": None,
            "session_id": session_id,
            "verified_at": time.time(),
        }

    return _check_url(url, project, session_id)


def _check_url(url: str, project: str, session_id: str) -> dict:
    """HTTP GET against the URL and check for a healthy response."""
    start = time.time()
    try:
        req = urllib.request.Request(url, method="GET")
        req.add_header("User-Agent", "dispatch-factory/post-heal-verify")
        with urllib.request.urlopen(req, timeout=15) as resp:
            status_code = resp.status
            latency_ms = round((time.time() - start) * 1000)

            if 200 <= status_code < 400:
                return {
                    "status": "passed",
                    "reason": f"health check passed ({status_code})",
                    "url": url,
                    "http_status": status_code,
                    "latency_ms": latency_ms,
                    "session_id": session_id,
                    "verified_at": time.time(),
                }
            else:
                return {
                    "status": "failed",
                    "reason": f"health check returned {status_code}",
                    "url": url,
                    "http_status": status_code,
                    "latency_ms": latency_ms,
                    "session_id": session_id,
                    "verified_at": time.time(),
                }
    except urllib.error.HTTPError as e:
        latency_ms = round((time.time() - start) * 1000)
        return {
            "status": "failed",
            "reason": f"health check HTTP error: {e.code}",
            "url": url,
            "http_status": e.code,
            "latency_ms": latency_ms,
            "session_id": session_id,
            "verified_at": time.time(),
        }
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        latency_ms = round((time.time() - start) * 1000)
        return {
            "status": "failed",
            "reason": f"health check connection error: {e}",
            "url": url,
            "http_status": None,
            "latency_ms": latency_ms,
            "session_id": session_id,
            "verified_at": time.time(),
        }


def write_verification_artifact(session_id: str, result: dict) -> Path:
    """Write the verification result as a -heal-verified.json artifact."""
    artifacts_dir = Path(settings.artifacts_dir)
    path = artifacts_dir / f"{session_id}-heal-verified.json"
    path.write_text(json.dumps(result, indent=2))
    logger.info(
        "Wrote heal-verified artifact for %s: %s (%s)",
        session_id, result["status"], result["reason"],
    )
    return path
