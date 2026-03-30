# Dispatch Ops Report

**Date:** 2026-03-29
**Task:** Fix 3 pipeline bugs on main

## Changes

### 1. heartbeat.py:678 — Add `--no-heal` to valid_flags
**Problem:** The healer circuit breaker appends `--no-heal` to ticket flags, but the CLI flag filter on line 684 strips it because it wasn't in `valid_flags`. The circuit breaker was silently bypassed.
**Fix:** Added `"--no-heal"` to the `valid_flags` set.

### 2. pipeline_runner.py:61-63 — Write error result.md on parse failure
**Problem:** When `scan_for_completions()` hits a JSONDecodeError or OSError, it logs a warning and skips, but never writes a `result.md`. The session stays in `worker_done` forever, retried every heartbeat.
**Fix:** After the warning log, write an error `result.md` so the session is marked complete and stops retrying.

### 3. main.py:48 — Add `--force-deploy` to ALLOWED_FLAGS
**Problem:** `heartbeat.py` accepts `--force-deploy` but `main.py` API validation rejects it, so API-submitted tickets can't use the flag.
**Fix:** Added `"--force-deploy"` to `ALLOWED_FLAGS` frozenset.

## Verification

- `uv run ruff check heartbeat.py pipeline_runner.py main.py` — all checks passed

## Outcome

All 3 fixes applied, linted clean, committed and PR created.
