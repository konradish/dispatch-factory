# Dispatch Report: Canary recipebrain-1421 — Stuck in Planning State

## Summary

**recipebrain-1421 is NOT currently stuck** — it completed successfully on 2026-03-28 at 14:49 with all artifacts present (planner, worker-done, result.md). However, the underlying bug that could cause planning sessions to get stuck **has regressed**: commit `2ce8fa6` (async dispatch refactor) was based on a stale branch and reverted the zombie GC fix from commit `4e9ea47` that expanded planning-state coverage. Sessions stuck in `planning` state with dead tmux workers will NOT be garbage-collected by the current heartbeat.

## Findings

### 1. Session recipebrain-1421 Status: COMPLETED

All artifacts present in `~/.local/share/dispatch/`:

| Artifact | Timestamp | Content |
|----------|-----------|---------|
| `worker-recipebrain-1421-planner.json` | Mar 28 14:22 | scope=small, type=feature, 4 steps |
| `worker-recipebrain-1421.log` | Mar 28 14:48 | Worker ran in `/mnt/c/projects/meal_tracker` |
| `worker-recipebrain-1421-worker-done.json` | Mar 28 14:48 | exit_code=0, error_class=success |
| `worker-recipebrain-1421-result.md` | Mar 28 14:49 | Status: SUCCESS |

The session progressed through: planning -> running -> worker_done -> completed. No tmux session exists (cleaned up after completion). The pipeline processed it end-to-end correctly.

### 2. Planner Station tmux Session: NOT PRESENT (expected)

```
$ tmux list-sessions
worker-recipebrain-1900  (active, current batch)
```

Session 1421 was from the previous batch (Mar 28) and has been cleaned up. No planner-specific tmux session exists — planning runs inline via the dispatch CLI, not as a separate tmux session.

### 3. Heartbeat Advancing Planning Sessions: REGRESSION FOUND

The zombie GC in `backend/heartbeat.py:448-501` has a **regression**:

**Current code (line 461):**
```python
if session["state"] != "running":
    continue
```

**Should be (per commit 4e9ea47):**
```python
if session["state"] not in ("running", "planning"):
    continue
```

**Root cause:** Commit `2ce8fa6` ("async dispatch + unified endpoints") was developed on a branch that forked BEFORE commit `4e9ea47` landed. When it was merged, it silently reverted the planning zombie GC fix via a merge conflict resolution or stale base.

**Impact:** Any session that:
1. Has a `-planner.json` artifact but no further artifacts
2. Has a dead/missing tmux worker process
3. Has a log file older than 30 minutes

...will remain in `planning` state indefinitely. The heartbeat will never GC it or mark it abandoned.

### 4. PR #54 Fix Assessment

PR #54 (`efa3361`) fixed false-abandonment in `artifacts.py` — specifically preventing premature abandonment of sessions that have `worker_done` artifacts. That fix is **intact and working correctly**.

The separate fix for planning-state zombie GC (`4e9ea47`, committed as `0addfdb` on the main line) is the one that regressed. These are two different bugs:
- PR #54: false-abandonment of completed sessions (FIXED, still working)
- Planning zombie GC: stuck planning sessions not cleaned up (REGRESSED)

### 5. Current Pipeline Health

Active tmux sessions as of investigation:
- `worker-dispatch-factory-1900` — running (python3 process active)
- `worker-lawpass-1900` — running (python3 process active)
- `worker-recipebrain-1900` — tmux session exists but **no pane listed** in `tmux list-panes -a`

The `worker-recipebrain-1900` session may be in a transitional state or already completed.

## Recommendations

### P0: Re-apply Planning Zombie GC Fix

One-line fix in `backend/heartbeat.py:461`:

```python
# Change:
if session["state"] != "running":
# To:
if session["state"] not in ("running", "planning"):
```

This is a direct re-application of commit `4e9ea47`. Low risk — the fix was already validated before.

### P1: Add Regression Test

Add a test in `backend/tests/` that verifies `_gc_zombie_sessions()` processes sessions in `planning` state. This would catch future regressions from merge conflicts.

### P2: Branch Hygiene

The regression happened because `2ce8fa6` was based on a stale branch. Consider:
- Rebasing feature branches before merge
- CI check that verifies zombie GC handles all expected states

## References

| File | Lines | Purpose |
|------|-------|---------|
| `backend/heartbeat.py` | 448-501 | `_gc_zombie_sessions()` — zombie detection and abandonment |
| `backend/heartbeat.py` | 461 | **Regression site** — planning state excluded from GC |
| `backend/artifacts.py` | 76-103 | `_detect_session_state()` — state derivation from artifacts |
| `backend/pipeline_runner.py` | 42-64 | `scan_for_completions()` — worker-done processing |
| Commit `4e9ea47` | | Original fix for planning zombie GC |
| Commit `2ce8fa6` | | Async dispatch refactor that reverted the fix |
| Commit `efa3361` | | PR #54 false-abandonment fix (intact) |
