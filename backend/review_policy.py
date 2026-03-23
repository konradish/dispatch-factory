"""Review policy — stricter criteria for the dispatch reviewer stage.

Audit findings (2026-03-22):
  - 198/202 sessions APPROVE (98%), only 4 REQUEST_CHANGES ever
  - 66/70 healed sessions reviewed with zero healer context
  - Empty-diff PRs rubber-stamped without flagging
  - Scope creep acknowledged but never rejected

This module defines tighter review standards that the dispatch runner
should enforce. The policy is exposed via API so the dispatch CLI can
fetch it at review time and inject it into the reviewer prompt.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from config import settings

logger = logging.getLogger("dispatch-factory.review-policy")

POLICY_FILE = "review-policy.json"

# Default policy — applied when no override file exists.
_DEFAULT_POLICY: dict = {
    "version": "2",
    "description": (
        "Tightened review policy after leniency audit (2026-03-22). "
        "98% approval rate with zero rejections for scope creep or empty diffs."
    ),
    "rejection_criteria": [
        {
            "id": "scope_creep",
            "severity": "reject",
            "description": (
                "Diff contains changes unrelated to the task description. "
                "Unrelated changes must be split into separate PRs."
            ),
        },
        {
            "id": "empty_diff_no_justification",
            "severity": "reject",
            "description": (
                "PR has zero changed files and the task required code changes. "
                "Empty diffs are only acceptable for tasks explicitly scoped to "
                "external artifacts (direction vectors, issue creation, branch cleanup)."
            ),
        },
        {
            "id": "broken_callers",
            "severity": "reject",
            "description": (
                "Diff changes function signatures, API contracts, or response formats "
                "without updating all callers visible in the codebase."
            ),
        },
        {
            "id": "missing_criteria",
            "severity": "reject",
            "description": (
                "One or more acceptance criteria from the spec are not satisfied "
                "by either the diff or pre-existing code."
            ),
        },
        {
            "id": "security_issue",
            "severity": "reject",
            "description": (
                "Hardcoded credentials, API keys, debug prints in production paths, "
                "or command injection vectors."
            ),
        },
    ],
    "healed_session_policy": {
        "enabled": True,
        "description": (
            "Healed sessions receive extra scrutiny. The reviewer MUST acknowledge "
            "healer intervention and assess whether the healing masked a real problem."
        ),
        "extra_checks": [
            "Was the healer action appropriate for the failure type?",
            "Did healing mask a code quality issue that should block the PR?",
            "If healer skipped a stage, is that stage actually optional for this task?",
            "Does the PR introduce patterns likely to trigger healing again?",
        ],
        "reject_if": [
            "Healer skipped a required deployment stage and code changes are non-trivial",
            "Healer diagnosed a code-level issue but the PR does not address it",
            "The same healing pattern has occurred 2+ times for this project recently",
        ],
    },
    "style_policy": {
        "severity": "flag_only",
        "description": (
            "Style issues (import order, naming conventions, missing type hints) "
            "should be flagged in feedback but do not warrant REQUEST_CHANGES alone. "
            "However, if combined with other issues, they contribute to rejection."
        ),
    },
    "empty_diff_policy": {
        "allowed_task_types": [
            "direction vector update",
            "issue creation",
            "branch cleanup",
            "stale PR closure",
            "investigation report",
        ],
        "description": (
            "Empty diffs are acceptable ONLY when the task is explicitly scoped to "
            "work outside the repository. For any task implying code changes, "
            "an empty diff should trigger REQUEST_CHANGES."
        ),
    },
}


def _policy_path() -> Path:
    return Path(settings.artifacts_dir) / POLICY_FILE


def get_policy() -> dict:
    """Return the active review policy (custom override or default)."""
    path = _policy_path()
    if path.is_file():
        try:
            custom = json.loads(path.read_text())
            return custom
        except (json.JSONDecodeError, OSError):
            logger.warning("Could not read custom review policy, using default")
    return _DEFAULT_POLICY


def get_reviewer_prompt_addendum(is_healed: bool = False) -> str:
    """Generate the policy section to inject into the reviewer prompt.

    This is the key integration point — dispatch calls this endpoint
    and appends the result to the reviewer prompt.
    """
    policy = get_policy()

    lines = [
        "## Review Policy (enforced — do not override with pragmatism)",
        "",
        "The following are REJECTION criteria. If any apply, you MUST verdict REQUEST_CHANGES:",
        "",
    ]

    for criterion in policy.get("rejection_criteria", []):
        lines.append(f"- **{criterion['id']}**: {criterion['description']}")

    lines.append("")

    if is_healed:
        healed = policy.get("healed_session_policy", {})
        lines.append("## HEALED SESSION — Extra Scrutiny Required")
        lines.append("")
        lines.append(
            "This session was healed by the automated healer. "
            "You MUST address the following in your review:"
        )
        lines.append("")
        for check in healed.get("extra_checks", []):
            lines.append(f"- {check}")
        lines.append("")
        lines.append("REQUEST_CHANGES if any of these apply:")
        lines.append("")
        for condition in healed.get("reject_if", []):
            lines.append(f"- {condition}")
        lines.append("")

    empty = policy.get("empty_diff_policy", {})
    lines.append("## Empty Diff Policy")
    lines.append("")
    lines.append(empty.get("description", ""))
    lines.append("Allowed task types for empty diffs: "
                 + ", ".join(empty.get("allowed_task_types", [])))
    lines.append("")

    return "\n".join(lines)


def get_reviewer_stats() -> dict:
    """Compute reviewer verdict statistics from session artifacts."""
    artifacts_dir = Path(settings.artifacts_dir)
    if not artifacts_dir.is_dir():
        return {"error": "artifacts directory not found"}

    approve = 0
    request_changes = 0
    error = 0
    healed_approved = 0
    healed_total = 0
    empty_diff_approved = 0
    scope_creep_approved = 0

    for entry in sorted(artifacts_dir.iterdir()):
        if not entry.name.endswith("-reviewer.json"):
            continue

        try:
            data = json.loads(entry.read_text())
        except (json.JSONDecodeError, OSError):
            continue

        verdict = data.get("verdict", "")
        feedback = data.get("feedback", "").lower()

        if verdict == "APPROVE":
            approve += 1

            # Check for healed session
            healer_file = entry.with_name(
                entry.name.replace("-reviewer.json", "-healer.json")
            )
            if healer_file.is_file():
                healed_total += 1
                healed_approved += 1

            # Check for empty diff approval
            if "empty diff" in feedback or "no repo code" in feedback or "no code change" in feedback:
                empty_diff_approved += 1

            # Check for scope creep approval
            if "scope creep" in feedback or "unrelated" in feedback:
                scope_creep_approved += 1

        elif verdict == "REQUEST_CHANGES":
            request_changes += 1
            healer_file = entry.with_name(
                entry.name.replace("-reviewer.json", "-healer.json")
            )
            if healer_file.is_file():
                healed_total += 1

        elif verdict == "ERROR":
            error += 1

    total = approve + request_changes + error
    return {
        "total_reviews": total,
        "approve": approve,
        "request_changes": request_changes,
        "error": error,
        "approval_rate": round(approve / total * 100, 1) if total > 0 else 0,
        "healed_sessions_reviewed": healed_total,
        "healed_approved": healed_approved,
        "healed_approval_rate": (
            round(healed_approved / healed_total * 100, 1) if healed_total > 0 else 0
        ),
        "empty_diff_approved": empty_diff_approved,
        "scope_creep_approved": scope_creep_approved,
        "policy_version": get_policy().get("version", "unknown"),
    }
