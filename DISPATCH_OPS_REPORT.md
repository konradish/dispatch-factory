# Dispatch Ops Report

**Date:** 2026-03-28
**Task:** Fix missing 'worker_done' state handler in `_reconcile_backlog()`

## Problem

The `_reconcile_backlog()` function in `backend/heartbeat.py` handles dispatched ticket reconciliation by checking session states. The elif chain covered `deployed`, `completed`, `error`, `rolled_back`, and `abandoned` — but **not** `worker_done`.

When `process_worker_completion()` hasn't yet written `result.md` by the time `_reconcile_backlog()` runs, the session state is still `worker_done` and falls through the entire elif chain silently, without updating the ticket or logging anything.

## Fix

Added an `elif state == "worker_done"` handler at line 222 that:
- **Skips/defers** the ticket (does not modify ticket status)
- **Logs a warning** for visibility: `ticket {id} session {session_id} in worker_done — deferring to next cycle`

This is the safest approach: `scan_for_completions()` runs at the start of each heartbeat cycle and will call `process_worker_completion()`, which writes `result.md` and transitions the session to a terminal state. The next `_reconcile_backlog()` call will then match one of the existing handlers.

## Changes

- `backend/heartbeat.py`: Added 4 lines (elif handler + comment + log warning) at line 222-225

## Verification

- `ruff check backend/heartbeat.py` — 2 pre-existing lint warnings (unused imports: `subprocess`, `reviewer_calibration`), no new issues introduced
- Diff is minimal and isolated to the elif chain

## Outcome

Success. The `worker_done` state is now handled explicitly with a safe defer-and-log pattern.
