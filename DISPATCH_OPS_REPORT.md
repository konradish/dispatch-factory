# Dispatch Ops Report — Fix zombie GC for planning sessions

**Date:** 2026-03-28
**Task:** Expand `_gc_zombie_sessions()` to cover sessions stuck in `planning` state

## Problem

8 sessions stuck in `planning` state were not being cleaned up by the zombie GC in `backend/heartbeat.py`. The condition at line 447 (`if session['state'] != 'running': continue`) caused the GC loop to skip any non-running session, including planning sessions whose tmux worker had died.

Stuck sessions: worker-dispatch-factory-1204, 1149, 1133, worker-lawpass-1127, 1051, worker-recipebrain-1020, worker-dispatch-factory-1030, 1023.

## Fix

**File:** `backend/heartbeat.py`, function `_gc_zombie_sessions()` (line 447)

Changed the state filter from:
```python
if session["state"] != "running":
    continue
```
to:
```python
if session["state"] not in ("running", "planning"):
    continue
```

This allows planning sessions to flow through the same age + no-active-worker checks already used for running sessions. No new logic paths — the existing `active_ids` check, log-file age check, and `abandon_session()` call all apply identically.

## What Changed

- 2-line diff in `backend/heartbeat.py`: expanded state condition + updated docstring
- No new dependencies, no config changes, no migration

## Verification

- The fix reuses all existing safety checks (active worker lookup, log file age threshold, `ZOMBIE_THRESHOLD_MINUTES`)
- Planning sessions with an active tmux worker are still skipped (`sid in active_ids`)
- Planning sessions younger than the threshold are still skipped
- Only planning sessions that are both workerless AND stale get abandoned
