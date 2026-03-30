# Ops Report: Auto-Verification Ticket Creation + Depth Guard Integration

**Date:** 2026-03-30
**Task:** Rebase auto-verification feature onto main, integrating with max_verification_depth guard

## Summary

Rebased the auto-verification ticket creation feature (from dispatch/0329-2308) onto main after the max_verification_depth guard was merged (PR #86). The auto-verify function now respects verification depth limits, and `auto-verify` is included as a tracked source in `_verification_depth_exceeded()`.

## Changes

### `backend/heartbeat.py`

1. **Added `import re`** at module top

2. **Added `_DEPLOY_FIX_RE` pattern and `_maybe_create_auto_verify_ticket()` function** (after `_session_was_healed()`):
   - Compiled regex `\b(deploy|fix)\b` (case-insensitive) for keyword matching
   - Creates a ticket with `priority="urgent"`, `source="auto-verify"`, `task_type="verify"`
   - Task text includes project, session ID, and truncated original task (200 chars)
   - Checks `_verification_depth_exceeded()` before creating to prevent runaway chains
   - Returns action log entries for heartbeat reporting

3. **Wired into completion branches** in `_reconcile_backlog()`:
   - After successful "deployed" state (non-healed path)
   - After successful "completed" state (non-healed path)
   - NOT added to healed paths (those already have their own escalation logic)
   - NOT added to error/abandoned paths (failed tasks don't need verification)

4. **Added `"auto-verify"` to `verify_sources`** in `_verification_depth_exceeded()` so auto-verify tickets count toward the chain depth limit.

## Verification

- `ruff check` on backend: passed
- `tsc -b --noEmit` on frontend: passed

## Design Decisions

- Used `urgent` priority (P1) as specified in the task
- Only triggers on successful completions — failed/abandoned tasks don't get verification tickets
- Skips healed sessions since they already have `_escalate_healed_unverified()` / `_verify_healed_deploy()`
- Word-boundary regex prevents false matches (e.g., "prefix" won't match "fix")
- Integrated with `_verification_depth_exceeded()` to prevent auto-verify from creating unbounded chains
