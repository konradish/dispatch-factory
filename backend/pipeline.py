"""Pipeline definition — extract and represent the dispatch pipeline structure.

This reads the current pipeline configuration from the dispatch script
and presents it as structured data. Eventually this will be the source
of truth that dispatch reads from, making the pipeline evolvable.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from config import settings

PIPELINE_FILE = "pipeline-definition.json"
MUTABLE_FIELDS = {"enabled", "timeout_seconds", "max_turns", "trigger_keywords", "skip_keywords"}
IMMUTABLE_FIELDS = {"id", "name", "phase", "engine", "outputs"}
REQUIRED_STAGES = {"worker", "reporter"}

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
            "max_turns": 6,
            "timeout_seconds": 300,
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
            "review_policy": {
                "version": "2",
                "policy_endpoint": "/api/review-policy",
                "prompt_addendum_endpoint": "/api/review-policy/prompt",
                "integration_status": "NOT_WIRED",
                "description": (
                    "Tightened after leniency audit (2026-03-22): 98% approval rate, "
                    "healed sessions reviewed without healer context, scope creep tolerated. "
                    "KNOWN GAP (2026-03-25): dispatch binary does NOT fetch this policy — "
                    "run_reviewer() uses a hardcoded 'Be pragmatic' prompt. "
                    "Until the binary is updated, this policy is dead letter."
                ),
                "healed_session_scrutiny": {
                    "enabled": True,
                    "description": (
                        "When reviewing a healed session, dispatch must pass "
                        "is_healed=true to the prompt addendum endpoint so the "
                        "reviewer receives extra scrutiny instructions."
                    ),
                },
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


def _pipeline_path() -> Path:
    return Path(settings.artifacts_dir) / PIPELINE_FILE


def _load_pipeline() -> dict:
    """Load pipeline: code defaults merged with JSON overrides."""
    result = copy.deepcopy(PIPELINE_DEFINITION)
    path = _pipeline_path()
    if not path.is_file():
        return result
    try:
        overrides = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return result
    if "global" in overrides:
        result["global"].update(overrides["global"])
    override_stages = {s["id"]: s for s in overrides.get("stages", overrides.get("stations", []))}
    for stage in result["stages"]:
        if stage["id"] in override_stages:
            for k, v in override_stages[stage["id"]].items():
                if k != "id":
                    stage[k] = v
    return result


def _save_overrides(pipeline_def: dict) -> None:
    defaults = PIPELINE_DEFINITION
    overrides: dict = {}
    global_diff = {k: v for k, v in pipeline_def["global"].items() if v != defaults["global"].get(k)}
    if global_diff:
        overrides["global"] = global_diff
    default_stages = {s["id"]: s for s in defaults["stages"]}
    stage_diffs = []
    for stage in pipeline_def["stages"]:
        sid = stage["id"]
        default = default_stages.get(sid, {})
        diff = {"id": sid}
        for k, v in stage.items():
            if k != "id" and k in MUTABLE_FIELDS and v != default.get(k):
                diff[k] = v
        if len(diff) > 1:
            stage_diffs.append(diff)
    if stage_diffs:
        overrides["stages"] = stage_diffs
    path = _pipeline_path()
    if overrides:
        path.write_text(json.dumps(overrides, indent=2))
    elif path.is_file():
        path.unlink()


def _validate_stage_update(stage_id: str, updates: dict) -> list[str]:
    errors: list[str] = []
    for k in updates:
        if k in IMMUTABLE_FIELDS:
            errors.append(f"Field '{k}' is immutable")
        elif k not in MUTABLE_FIELDS:
            errors.append(f"Unknown mutable field '{k}'")
    if "enabled" in updates:
        val = updates["enabled"]
        if stage_id in REQUIRED_STAGES and val is not True:
            errors.append(f"Stage '{stage_id}' cannot be disabled")
        if val not in (True, False) and not (stage_id == "planner" and val == "auto"):
            errors.append("enabled must be true/false" + (" or 'auto' for planner" if stage_id == "planner" else ""))
    if "timeout_seconds" in updates:
        val = updates["timeout_seconds"]
        if not isinstance(val, (int, float)) or val < 1 or val > 7200:
            errors.append("timeout_seconds must be 1-7200")
    if "max_turns" in updates:
        val = updates["max_turns"]
        if not isinstance(val, int) or val < 1 or val > 200:
            errors.append("max_turns must be 1-200")
    for kw_field in ("trigger_keywords", "skip_keywords"):
        if kw_field in updates:
            if stage_id != "planner":
                errors.append(f"'{kw_field}' only applies to planner")
            elif not isinstance(updates[kw_field], list) or not all(isinstance(x, str) and x for x in updates[kw_field]):
                errors.append(f"'{kw_field}' must be a list of non-empty strings")
    return errors


def _validate_global_update(updates: dict) -> list[str]:
    errors: list[str] = []
    allowed = {"session_timeout_minutes", "deploy_window", "stage_timeout_seconds"}
    for k in updates:
        if k not in allowed:
            errors.append(f"Unknown global field '{k}'")
    if "session_timeout_minutes" in updates:
        val = updates["session_timeout_minutes"]
        if not isinstance(val, (int, float)) or val < 1 or val > 180:
            errors.append("session_timeout_minutes must be 1-180")
    if "stage_timeout_seconds" in updates:
        val = updates["stage_timeout_seconds"]
        if not isinstance(val, (int, float)) or val < 1 or val > 7200:
            errors.append("stage_timeout_seconds must be 1-7200")
    if "deploy_window" in updates:
        val = updates["deploy_window"]
        if not isinstance(val, list) or len(val) != 2 or not all(isinstance(x, int) and 0 <= x <= 23 for x in val):
            errors.append("deploy_window must be [start_hour, end_hour] with values 0-23")
    return errors


def update_station(station_id: str, updates: dict) -> dict | list[str] | None:
    """Update mutable fields on a stage. Returns updated stage, validation errors, or None."""
    pipeline_def = _load_pipeline()
    stage = next((s for s in pipeline_def["stages"] if s["id"] == station_id), None)
    if stage is None:
        return None
    errors = _validate_stage_update(station_id, updates)
    if errors:
        return errors
    for k, v in updates.items():
        if k in MUTABLE_FIELDS:
            stage[k] = v
    _save_overrides(pipeline_def)
    return stage


def update_global(updates: dict) -> dict | list[str]:
    """Update global pipeline config."""
    errors = _validate_global_update(updates)
    if errors:
        return errors
    pipeline_def = _load_pipeline()
    pipeline_def["global"].update(updates)
    _save_overrides(pipeline_def)
    return pipeline_def["global"]


def reset_pipeline() -> dict:
    """Delete override file, return code defaults."""
    path = _pipeline_path()
    if path.is_file():
        path.unlink()
    return copy.deepcopy(PIPELINE_DEFINITION)


def get_pipeline() -> dict:
    """Return the current pipeline definition (with overrides applied)."""
    return _load_pipeline()


def get_stage(stage_id: str) -> dict | None:
    """Return a single stage definition."""
    pipeline_def = _load_pipeline()
    for stage in pipeline_def["stages"]:
        if stage["id"] == stage_id:
            return stage
    return None


def get_pipeline_summary() -> dict:
    """Compact summary for dashboard display."""
    pipeline_def = _load_pipeline()
    stages = []
    for s in pipeline_def["stages"]:
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
        "version": pipeline_def["version"],
        "global": pipeline_def["global"],
        "stages": stages,
        "dispatch_bin": settings.dispatch_bin,
        "has_overrides": _pipeline_path().is_file(),
    }
