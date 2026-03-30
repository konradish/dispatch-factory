"""Auto-merge for factory self-fixes.

When a worker PR meets ALL of these criteria, it can be auto-merged
without human intervention:
  (a) Only touches backend/*.py files
  (b) Passes make lint && make test
  (c) Is authored by a factory worker (dispatch branch naming convention)

This prevents the 7-cycle merge delay pattern where safe backend-only
fixes sit in draft PR limbo waiting for human review.
"""

from __future__ import annotations

import logging
import re
import subprocess

logger = logging.getLogger("dispatch-factory.auto-merge")

# Branch prefix used by dispatch workers
DISPATCH_BRANCH_RE = re.compile(r"^dispatch/")

# Files that are safe to auto-merge: only backend Python files
SAFE_FILE_RE = re.compile(r"^backend/[^/]*\.py$|^backend/.+/[^/]*\.py$")


def is_eligible_for_auto_merge(
    pr_url: str,
    project: str,
    project_path: str,
) -> bool:
    """Check if a PR meets auto-merge criteria for factory self-fixes.

    Returns True only when ALL conditions are met:
      1. Project is dispatch-factory
      2. PR branch matches dispatch worker naming convention
      3. PR only touches backend/*.py files
    """
    if project != "dispatch-factory":
        return False

    branch = _get_pr_branch(pr_url, project_path)
    if not branch or not DISPATCH_BRANCH_RE.match(branch):
        logger.info("Auto-merge skip: branch %r not a dispatch worker branch", branch)
        return False

    changed_files = _get_pr_changed_files(pr_url, project_path)
    if not changed_files:
        logger.info("Auto-merge skip: no changed files found for %s", pr_url)
        return False

    non_backend = [f for f in changed_files if not SAFE_FILE_RE.match(f)]
    if non_backend:
        logger.info(
            "Auto-merge skip: PR touches non-backend files: %s",
            non_backend[:5],
        )
        return False

    logger.info(
        "Auto-merge eligible: %s (branch=%s, %d backend-only .py files)",
        pr_url, branch, len(changed_files),
    )
    return True


def _get_pr_branch(pr_url: str, cwd: str) -> str | None:
    """Get the head branch name of a PR."""
    try:
        r = subprocess.run(
            ["gh", "pr", "view", pr_url, "--json", "headRefName", "-q", ".headRefName"],
            cwd=cwd, capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def _get_pr_changed_files(pr_url: str, cwd: str) -> list[str]:
    """Get list of files changed in a PR."""
    try:
        r = subprocess.run(
            ["gh", "pr", "diff", pr_url, "--name-only"],
            cwd=cwd, capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0:
            return [f for f in r.stdout.strip().splitlines() if f]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return []
