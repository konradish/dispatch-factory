# Task-Text Dedup Guard for `_auto_dispatch()`

## Summary

Added a task-text dedup guard to `_auto_dispatch()` in `backend/heartbeat.py` that prevents dispatching tickets whose task text (first 80 chars) matches any already in-flight ticket. This closes a gap where the existing per-project inflight guard (line 618) wouldn't catch duplicate tasks across different projects or re-queued identical tasks.

## Findings

### Problem

`_auto_dispatch()` had a per-project inflight guard (`backlog.has_inflight_ticket(project)`) that prevents dispatching two tickets for the same project simultaneously. However, it had no guard against dispatching tickets with identical task text — meaning if two tickets existed with the same task description (even across different projects, or if a ticket was re-created while the original was still in-flight), both could be dispatched.

### Implementation

**Location:** `backend/heartbeat.py`, `_auto_dispatch()` function (lines 599–684 after edit)

**Changes:**

1. **Before the dispatch loop** (line 601–607): Fetch all `dispatched` tickets via `backlog.list_tickets(status="dispatched")`, build a `dict[str, str]` mapping `task[:80]` prefixes to ticket IDs.

2. **Inside the loop** (lines 622–628): After the per-project inflight check, compare the candidate ticket's `task[:80]` against the prefix set. If matched, log and skip with message: `skipped {ticket_id}: task text matches in-flight {other_id}`.

3. **After successful dispatch** (lines 678–680): Add the newly-dispatched ticket's task prefix to the set so subsequent loop iterations catch duplicates within the same batch.

### Design Decisions

- **80-char prefix match**: Sufficient to catch identical or near-identical tasks while avoiding false positives from tasks that merely share a common opening phrase. Task descriptions in this system are typically specific enough that 80 chars is distinctive.
- **`dict` over `set`**: Using a dict allows reporting *which* in-flight ticket matched, improving debuggability in logs.
- **Single `list_tickets` call**: The dispatched tickets are fetched once before the loop rather than per-candidate, minimizing I/O.
- **Guard ordering**: Placed after per-project inflight check (which is cheaper) but before meta-work ratio and circuit breaker checks, since dedup is a fundamental correctness guard.

### Verification

- `make lint`: Passes (2 pre-existing lint errors unrelated to this change: unused `subprocess` and `reviewer_calibration` imports)
- `make test`: 31 passed, 15 pre-existing failures (all in `test_alert_lifecycle.py`, `test_meta_work_ratio.py`, `test_factory_idle_mode.py` — caused by `backlog.settings` attribute error, unrelated to this change)
- No existing tests for `_auto_dispatch()` were found to regress against.

## Recommendations

1. **Add unit tests for `_auto_dispatch()`** — the function has no test coverage. A test should verify: (a) dedup skips matching task text, (b) newly dispatched tasks are added to the prefix set, (c) empty task text doesn't cause false matches.
2. **Fix pre-existing test failures** — 15 tests fail due to `backlog.settings` attribute error, likely from a recent refactor that moved settings out of the backlog module.
3. **Fix pre-existing lint errors** — remove unused `subprocess` and `reviewer_calibration` imports in `heartbeat.py`.

## References

- `backend/heartbeat.py:586-684` — `_auto_dispatch()` function
- `backend/backlog.py:15` — `list_tickets()` function signature
- `backend/heartbeat.py:618` — existing per-project inflight guard
