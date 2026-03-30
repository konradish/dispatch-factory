# Ops Report: Bulk-Heal Stale worker_done Sessions

**Date:** 2026-03-29
**Operator:** dispatch-ops (unattended)

## Summary

14 sessions were stuck in `worker_done` state in the SQLite sessions cache despite having valid `result.md` artifacts on disk. Root cause was a bug in `_update_session_state()` that prevented the `result` artifact from being recognized during state detection.

## Root Cause

In `backend/artifacts.py:174`, the `_update_session_state` function skipped adding the `result` key to `artifacts_data` because result files are `.md` (not JSON). The guard `if name != "result"` meant the result artifact was recorded in `artifact_types` but never in `artifacts_data`, so `_detect_session_state()` (which checks `if "result" in artifacts`) could never transition sessions to `completed`.

## Fix

Changed line 174 from:
```python
if name != "result":
    artifacts_data[name] = _read_json(entry)
```
To:
```python
if name == "result":
    artifacts_data[name] = True  # .md, not JSON — just mark presence
else:
    artifacts_data[name] = _read_json(entry)
```

## Sessions Healed

### Original 10 (from task spec)

| Session ID | Project | PR | Verdict |
|---|---|---|---|
| worker-lawpass-1639 | lawpass | (none) | completed |
| worker-lawpass-1632 | lawpass | konradish/lawpass-ai#95 | completed |
| worker-recipebrain-1450 | recipebrain | konradish/meal_tracker#80 | completed |
| worker-dispatch-factory-1443 | dispatch-factory | konradish/dispatch-factory#57 | completed |
| worker-movies-1443 | movies | (none) | completed |
| worker-dispatch-factory-1439 | dispatch-factory | konradish/dispatch-factory#56 | completed |
| worker-movies-1437 | movies | konradish/family-movie-queue#83 | completed |
| worker-dispatch-factory-1427 | dispatch-factory | konradish/dispatch-factory#55 | completed |
| worker-movies-1426 | movies | konradish/family-movie-queue#82 | completed |
| worker-movies-1412 | movies | (none) | completed |

### Bonus 4 (discovered during sweep)

| Session ID | Project | Verdict |
|---|---|---|
| worker-dispatch-factory-1900 | dispatch-factory | completed |
| worker-lawpass-1900 | lawpass | completed |
| worker-recipebrain-1900 | recipebrain | completed |
| worker-movies-1903 | movies | completed |

## tmux Cleanup

No tmux sessions remained for any of the 14 session IDs. Already cleaned up.

## Remaining worker_done Count

**0** — all sessions resolved.

## Artifacts

- All 14 sessions had valid `-worker-done.json` (exit_code=0, error_class=success) and `-result.md` (STATUS: SUCCESS) on disk
- Tickets for all 10 original sessions were already marked `completed` in the tickets table
- Only the sessions cache table was stale

## Code Change

- `backend/artifacts.py` line 174: Fix result artifact not being added to `artifacts_data` dict, preventing state transition from `worker_done` to `completed`
