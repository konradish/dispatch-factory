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

# Project config — mirrors dispatch CLI PROJECTS dict
PROJECT_CONFIG = {
    "recipebrain": {"path": "/mnt/c/projects/meal_tracker", "test_cmd": "make test"},
    "electricapp": {"path": "/mnt/c/projects/electric-app", "test_cmd": "make test"},
    "dispatch-factory": {"path": "/mnt/c/projects/dispatch-factory", "test_cmd": "cd backend && uv run ruff check . && cd ../frontend && npx tsc -b --noEmit"},
    "lawpass": {"path": "/mnt/c/projects/lawpass-ai", "test_cmd": "make test"},
    "movies": {"path": "/mnt/c/projects/family-movies", "test_cmd": "make test"},
    "schoolbrain": {"path": "/mnt/c/projects/schoolbrain", "test_cmd": "make test"},
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

    # Code tasks with auto_merge: validate → merge
    elif task_type == "code" and auto_merge and pr_url:
        # Step 1: Validate (run tests on the PR branch)
        validate_ok = _run_validation(project, pr_url, session_id)
        if validate_ok:
            actions.append(f"pipeline: {session_id} validation passed")
            # Step 2: Merge
            merge_ok = _auto_merge_pr(pr_url, project, session_id)
            if merge_ok:
                actions.append(f"pipeline: {session_id} PR merged")
            else:
                actions.append(f"pipeline: {session_id} PR merge failed")
        else:
            actions.append(f"pipeline: {session_id} validation FAILED — PR left as draft")

    # Code tasks without auto_merge but with PR: still validate
    elif task_type == "code" and pr_url:
        validate_ok = _run_validation(project, pr_url, session_id)
        if validate_ok:
            actions.append(f"pipeline: {session_id} validation passed (draft PR, manual merge)")
        else:
            actions.append(f"pipeline: {session_id} validation FAILED")

    status = "SUCCESS"
    if pr_url:
        status = f"SUCCESS (PR: {pr_url})"

    _write_result(session_id, status, pr_url, task_short)
    actions.append(f"pipeline: {session_id} completed — {status}")

    # Kill the tmux session — worker is done, shell is just sitting there
    try:
        import subprocess
        subprocess.run(["tmux", "kill-session", "-t", session_id],
                       capture_output=True, timeout=10)
        actions.append(f"pipeline: killed tmux session {session_id}")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    try:
        _send_ntfy(project, task_short, pr_url)
    except Exception as e:
        logger.warning("ntfy notification failed: %s", e)

    return actions


def _get_project_path(project: str) -> str:
    """Get project directory path."""
    cfg = PROJECT_CONFIG.get(project, {})
    return cfg.get("path", "/tmp")


def _run_validation(project: str, pr_url: str, session_id: str) -> bool:
    """Checkout PR branch, run tests, return to main. Returns True if tests pass."""
    import subprocess

    cfg = PROJECT_CONFIG.get(project, {})
    cwd = cfg.get("path", "/tmp")
    test_cmd = cfg.get("test_cmd", "make test")

    logger.info("Running validation for %s: %s", session_id, test_cmd)

    try:
        # Checkout the PR branch
        r = subprocess.run(
            ["gh", "pr", "checkout", pr_url],
            cwd=cwd, capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            logger.warning("Could not checkout PR: %s", r.stderr[:200])
            return False

        # Run tests
        r = subprocess.run(
            test_cmd, shell=True,
            cwd=cwd, capture_output=True, text=True, timeout=300,
        )
        passed = r.returncode == 0

        if not passed:
            logger.warning("Validation failed for %s: %s", session_id, r.stdout[-300:] + r.stderr[-300:])
            # Write validation failure artifact
            artifacts_dir = Path(settings.artifacts_dir)
            val_path = artifacts_dir / f"{session_id}-validate.json"
            val_path.write_text(json.dumps({
                "passed": False,
                "test_cmd": test_cmd,
                "output": (r.stdout[-500:] + r.stderr[-500:]).strip(),
                "timestamp": time.time(),
            }, indent=2))
        else:
            logger.info("Validation passed for %s", session_id)

        # Return to main regardless
        subprocess.run(["git", "checkout", "main"], cwd=cwd, capture_output=True, timeout=10)
        subprocess.run(["git", "pull", "origin", "main"], cwd=cwd, capture_output=True, timeout=15)

        return passed

    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.warning("Validation error for %s: %s", session_id, e)
        subprocess.run(["git", "checkout", "main"], cwd=cwd, capture_output=True, timeout=10)
        return False


def _auto_merge_pr(pr_url: str, project: str, session_id: str) -> bool:
    """Merge a PR via gh CLI. Returns True on success."""
    import subprocess
    import re
    m = re.match(r"https://github\.com/([^/]+/[^/]+)/pull/(\d+)", pr_url)
    if not m:
        logger.warning("Cannot parse PR URL: %s", pr_url)
        return False

    cwd = _get_project_path(project)

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
