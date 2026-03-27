"""Pipeline runner — orchestrates post-worker stages from the factory backend.

When a worker writes a `-worker-done.json` artifact, the heartbeat detects it
and calls `process_worker_completion()` to run post-worker stages based on
the task type and pipeline configuration.

This replaces the 1750-line runner script that was previously generated per task.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from config import settings

logger = logging.getLogger("dispatch-factory.pipeline-runner")

ARTIFACT_TYPES = {
    "-worker-done.json": "worker_done",
    "-planner.json": "planner",
    "-reviewer.json": "reviewer",
    "-verifier.json": "verifier",
    "-monitor.json": "monitor",
    "-result.md": "result",
    "-error.json": "error",
}


def scan_for_completions() -> list[dict]:
    """Find worker-done artifacts that haven't been processed yet (no result.md)."""
    artifacts_dir = Path(settings.artifacts_dir)
    if not artifacts_dir.is_dir():
        return []

    completions = []
    for entry in artifacts_dir.iterdir():
        if not entry.name.endswith("-worker-done.json"):
            continue
        session_id = entry.name.replace("-worker-done.json", "")
        # Skip if already has a result (post-worker already ran)
        result_path = artifacts_dir / f"{session_id}-result.md"
        if result_path.is_file():
            continue
        try:
            data = json.loads(entry.read_text())
            data["_session_id"] = session_id
            completions.append(data)
        except (json.JSONDecodeError, OSError):
            continue

    return completions


def process_worker_completion(completion: dict) -> list[str]:
    """Process a completed worker — run post-worker pipeline stages.

    Returns list of action strings for heartbeat logging.
    """
    actions = []
    session_id = completion.get("_session_id", completion.get("session", ""))
    project = completion.get("project", "unknown")
    error_class = completion.get("error_class", "unknown")
    pr_url = completion.get("pr_url", "")
    task_short = completion.get("task_short", "")

    logger.info("Processing worker completion: %s (project=%s, error=%s, pr=%s)",
                session_id, project, error_class, pr_url)

    if error_class != "success":
        # Worker failed — write result and move on
        _write_result(session_id, f"FAILED ({error_class})", pr_url, task_short)
        actions.append(f"pipeline: {session_id} worker failed ({error_class})")
        return actions

    task_type = completion.get("task_type", "code")
    auto_merge = completion.get("auto_merge", False)

    # Non-code tasks: auto-merge the report PR (no review needed)
    if task_type != "code" and pr_url:
        merge_ok = _auto_merge_pr(pr_url, project, session_id)
        if merge_ok:
            actions.append(f"pipeline: {session_id} PR auto-merged ({task_type} task)")
        else:
            actions.append(f"pipeline: {session_id} PR auto-merge failed")

    # Code tasks with auto_merge: run reviewer then merge
    elif task_type == "code" and auto_merge and pr_url:
        # TODO: Add LLM reviewer stage here (claude_reason reviews diff)
        # For now, auto-merge code PRs too — reviewer will be added incrementally
        merge_ok = _auto_merge_pr(pr_url, project, session_id)
        if merge_ok:
            actions.append(f"pipeline: {session_id} PR merged (code, reviewer pending)")
        else:
            actions.append(f"pipeline: {session_id} PR merge failed")

    status = "SUCCESS"
    if pr_url:
        status = f"SUCCESS (PR: {pr_url})"

    _write_result(session_id, status, pr_url, task_short)
    actions.append(f"pipeline: {session_id} completed — {status}")

    try:
        _send_ntfy(project, task_short, pr_url)
    except Exception as e:
        logger.warning("ntfy notification failed: %s", e)

    return actions


def _auto_merge_pr(pr_url: str, project: str, session_id: str) -> bool:
    """Merge a PR via gh CLI. Returns True on success."""
    import subprocess
    # Extract repo path from PR URL: https://github.com/owner/repo/pull/123
    import re
    m = re.match(r"https://github\.com/([^/]+/[^/]+)/pull/(\d+)", pr_url)
    if not m:
        logger.warning("Cannot parse PR URL: %s", pr_url)
        return False

    # Find project path from PROJECTS config
    project_paths = {
        "recipebrain": "/mnt/c/projects/meal_tracker",
        "electricapp": "/mnt/c/projects/electric-app",
        "dispatch-factory": "/mnt/c/projects/dispatch-factory",
        "lawpass": "/mnt/c/projects/lawpass-ai",
        "movies": "/mnt/c/projects/family-movies",
        "schoolbrain": "/mnt/c/projects/schoolbrain",
    }
    cwd = project_paths.get(project, "/tmp")

    try:
        # Mark PR as ready (un-draft), then merge
        subprocess.run(
            ["gh", "pr", "ready", pr_url],
            cwd=cwd, capture_output=True, text=True, timeout=30,
        )
        r = subprocess.run(
            ["gh", "pr", "merge", pr_url, "--squash", "--delete-branch"],
            cwd=cwd, capture_output=True, text=True, timeout=60,
        )
        if r.returncode == 0:
            logger.info("Merged PR: %s", pr_url)
            return True
        else:
            logger.warning("PR merge failed: %s", r.stderr[:200])
            return False
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.warning("PR merge error: %s", e)
        return False


def _write_result(session_id: str, status: str, pr_url: str, task_short: str) -> None:
    """Write a result.md artifact for the session."""
    artifacts_dir = Path(settings.artifacts_dir)
    result_path = artifacts_dir / f"{session_id}-result.md"

    lines = [
        f"# Dispatch Report: {session_id}",
        "",
        f"**Status:** {status}",
    ]
    if pr_url:
        lines.append(f"**PR:** {pr_url}")
    if task_short:
        lines.append(f"**Task:** {task_short}")
    lines.extend([
        "",
        f"*Generated by factory backend pipeline runner at {time.strftime('%Y-%m-%d %H:%M:%S')}*",
    ])

    result_path.write_text("\n".join(lines))
    logger.info("Wrote result: %s → %s", session_id, status)


def _send_ntfy(project: str, task_short: str, pr_url: str) -> None:
    """Send ntfy.sh notification for completed work."""
    import urllib.request
    url_hint = f" PR: {pr_url}" if pr_url else ""
    data = f"{task_short}{url_hint}".encode()
    req = urllib.request.Request(
        "https://ntfy.sh/dispatch-factory",
        data=data,
        headers={
            "Title": f"{project}: completed",
            "Priority": "default",
        },
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass
