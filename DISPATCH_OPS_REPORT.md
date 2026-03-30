# Ops Report: Add max_verification_depth to pipeline

**Date:** 2026-03-30
**Task:** Prevent runaway verification chains that consume multiple workers for a single issue

## Problem

The electricapp deploy status question triggered a 4-deep verification chain consuming 5 workers:
1. Original ticket dispatched -> healer intervenes -> verify ticket created
2. Verify ticket dispatched -> healer intervenes again -> another verify ticket
3. Chain continues 4 deep, each spawning a new worker

Root cause: `_escalate_healed_unverified()` and `_check_healed_but_failed()` in heartbeat.py create new tickets unconditionally, with no awareness of prior verify tickets for the same project.

## Changes

### 1. `backend/config.py` — Added `max_verification_depth` field

- Added `max_verification_depth: int = 2` to `HeartbeatConfig` dataclass
- Default value of 2 means at most 2 verify/escalation tickets can chain before the system stops spawning workers

### 2. `.dispatch-factory.example.toml` — Documented new setting

- Added `max_verification_depth = 2` with explanatory comment to `[heartbeat]` section

### 3. `backend/heartbeat.py` — Depth check + guards

**New functions:**
- `_verification_depth_exceeded(project)`: Counts tickets with verify sources (`healer-verification`, `healer`, `healer-circuit-breaker`) for the same project. Counts pending/dispatching/dispatched tickets plus completed/failed tickets within the last hour. Returns True if count >= max_verification_depth.
- `_write_depth_exceeded_result(session_id, project, reason)`: Writes a result.md via `pipeline_runner._write_result()` with `VERIFICATION_DEPTH_EXCEEDED` status so the session gets a final artifact instead of dangling.

**Guards added to:**
- `_escalate_healed_unverified()`: If depth exceeded, writes result.md, clears the healed session from dashboard alerts, and returns without creating a new ticket.
- `_check_healed_but_failed()`: If depth exceeded, writes result.md and returns without creating a root-cause ticket.

## Verification

- `uv run ruff check .` — all backend checks pass
- `uv run python -c "import config; print(config.settings.heartbeat.max_verification_depth)"` — prints `2`
- Frontend lint errors are pre-existing and unrelated

## Risk Assessment

- **Low risk**: Only affects automated ticket creation in the heartbeat reconciliation loop
- **No data loss**: Sessions still get a result.md artifact with clear status
- **Configurable**: Operators can adjust `max_verification_depth` in `.dispatch-factory.toml`
- **Backward compatible**: Default of 2 is a new constraint but prevents known-bad behavior
