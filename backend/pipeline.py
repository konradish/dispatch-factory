"""Pipeline definition — extract and represent the dispatch pipeline structure.

This reads the current pipeline configuration from the dispatch script
and presents it as structured data. Eventually this will be the source
of truth that dispatch reads from, making the pipeline evolvable.
"""

from __future__ import annotations

from config import settings

# The pipeline definition — extracted from dispatch's hardcoded structure.
# This is the data representation of what's currently baked into 4,242 lines of Python.
# Making it explicit is the first step toward making it evolvable.

PIPELINE_DEFINITION: dict = {
    "version": "1.0",
    "description": "dispatch SDLC pipeline — deterministic workflow wrapping non-deterministic LLM workers",
    "global": {
        "session_timeout_minutes": 60,
        "deploy_window": [23, 5],
        "stage_timeout_seconds": 1200,
    },
    "stages": [
        {
            "id": "planner",
            "name": "Planner",
            "phase": 0,
            "description": "Scope analysis, risk assessment, step decomposition. Uses keyword heuristic to decide whether planning is needed.",
            "enabled": "auto",
            "trigger_keywords": [
                "refactor", "migrate", "architecture", "extract", "redesign",
                "integration", "new feature", "endpoint", "database", "schema",
                "multi-file", "overhaul", "rewrite",
            ],
            "skip_keywords": ["typo", "css", "lint", "docs", "readme", "comment"],
            "engine": "claude_reason",
            "model": "claude-opus-4-6",
            "max_turns": 100,
            "timeout_seconds": 90,
            "outputs": ["planner.json"],
            "gates": {
                "database_change": "stops pipeline — requires human review",
            },
            "flags": {
                "--plan": "force planner",
                "--no-plan": "skip planner",
            },
        },
        {
            "id": "worker",
            "name": "Worker",
            "phase": 1,
            "description": "LLM implementation — code, tests, commit, create PR. Full Claude Code instance with repo access.",
            "enabled": True,
            "engine": "cy -p",
            "model": "claude-opus-4-6",
            "timeout_seconds": 3600,
            "outputs": ["PR (GitHub)", ".log"],
            "retry_on_failure": False,
            "watchdog": {
                "enabled": True,
                "timeout_minutes": 60,
                "action": "kill",
            },
        },
        {
            "id": "reviewer",
            "name": "Reviewer",
            "phase": 2,
            "description": "Results-oriented LLM review. Two jobs: policy check (diff-only) + criteria satisfaction (accepts pre-existing code).",
            "enabled": True,
            "engine": "claude_reason",
            "model": "claude-opus-4-6",
            "max_turns": 100,
            "timeout_seconds": 90,
            "outputs": ["reviewer.json"],
            "verdicts": ["APPROVE", "REQUEST_CHANGES"],
            "on_request_changes": {
                "action": "retry_worker",
                "max_retries": 5,
                "includes_feedback": True,
            },
        },
        {
            "id": "verifier",
            "name": "Verifier",
            "phase": 3,
            "description": "Merge PR, run validation, deploy to dev and prod. Multi-stage with per-stage error classification.",
            "enabled": True,
            "engine": "subprocess",
            "sub_stages": [
                {"id": "merge", "cmd": "gh pr merge", "required": True},
                {"id": "local_validate", "cmd": "make validate", "required": True},
                {"id": "backup", "cmd": "make backup-pre-deploy", "required": False},
                {"id": "deploy_dev", "cmd": "make deploy-dev", "required": True},
                {"id": "deploy_prod", "cmd": "make deploy-prod", "required": False, "gate": "deploy_window"},
            ],
            "outputs": ["verifier.json"],
            "healer": {
                "enabled": True,
                "engine": "claude_reason",
                "model": "claude-opus-4-6",
                "max_turns": 100,
                "timeout_seconds": 90,
                "actions": ["retry_same", "retry_modified_cmd", "run_fix_then_retry", "skip_stage", "abort"],
                "fallback": "static_classification",
                "outputs": ["healer.json"],
                "post_heal_verification": {
                    "enabled": True,
                    "require_deploy_success": True,
                    "on_unverified": "escalate",
                    "description": (
                        "After healer intervention, verify deploy actually succeeded. "
                        "Sessions that complete without DEPLOYED status after healing "
                        "are treated as failures and escalated — prevents false confidence."
                    ),
                },
            },
            "auto_fixer": {
                "enabled": True,
                "engine": "cy -p",
                "trigger": "build_error",
                "outputs": ["fixer.json"],
            },
        },
        {
            "id": "monitor",
            "name": "Monitor",
            "phase": 4,
            "description": "Post-deploy smoke tests, deployment markers, health checks.",
            "enabled": True,
            "engine": "subprocess",
            "checks": ["smoke_test_dev", "smoke_test_prod", "deployment_marker"],
            "outputs": ["monitor.json"],
            "on_prod_failure": "auto_rollback",
        },
        {
            "id": "visual",
            "name": "Visual Validation",
            "phase": "4b",
            "description": "Playwright screenshots + LLM vision eval. Detects broken UI that smoke tests miss.",
            "enabled": True,
            "engine": "playwright + claude_reason",
            "model": "claude-opus-4-6",
            "screenshot_routes": "auto_detect_from_diff",
            "auto_fix": {
                "enabled": True,
                "max_attempts": 3,
                "engine": "cy -p",
            },
            "outputs": ["visual-dev.json", "visual-prod.json", "visual-eval-*.json"],
            "on_prod_failure": "auto_rollback",
        },
        {
            "id": "reporter",
            "name": "Reporter",
            "phase": 5,
            "description": "Generate result.md summary + push ntfy.sh notification.",
            "enabled": True,
            "engine": "template",
            "outputs": ["result.md"],
            "notification": {
                "service": "ntfy.sh",
                "patterns": {
                    "shipped": "default priority",
                    "needs_decision": "high priority",
                    "prod_broken": "urgent priority",
                },
            },
        },
    ],
}


def get_pipeline() -> dict:
    """Return the current pipeline definition."""
    return PIPELINE_DEFINITION


def get_stage(stage_id: str) -> dict | None:
    """Return a single stage definition."""
    for stage in PIPELINE_DEFINITION["stages"]:
        if stage["id"] == stage_id:
            return stage
    return None


def get_pipeline_summary() -> dict:
    """Compact summary for dashboard display."""
    stages = []
    for s in PIPELINE_DEFINITION["stages"]:
        healer = None
        if "healer" in s:
            healer = {
                "enabled": s["healer"]["enabled"],
                "actions": s["healer"]["actions"],
            }

        stages.append({
            "id": s["id"],
            "name": s["name"],
            "phase": s["phase"],
            "description": s["description"],
            "enabled": s["enabled"],
            "engine": s.get("engine", ""),
            "model": s.get("model", ""),
            "timeout": s.get("timeout_seconds"),
            "healer": healer,
            "outputs": s.get("outputs", []),
        })

    return {
        "version": PIPELINE_DEFINITION["version"],
        "global": PIPELINE_DEFINITION["global"],
        "stages": stages,
        "dispatch_bin": settings.dispatch_bin,
    }
