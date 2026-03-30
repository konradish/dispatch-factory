# Fix: Duplicate-Dispatch Race Condition in `_dispatch_async()`

## Summary

A TOCTOU race in `backend/foreman.py:_dispatch_async()` allowed concurrent heartbeat cycles to dispatch the same ticket twice (evidence: workers 1952 and 1953 both running ticket `1f99ec97`). The fix adds per-ticket locks with compare-and-swap on ticket status before spawning the subprocess, plus lock lifecycle management to prevent unbounded memory growth. All 5 race condition tests pass.

## Findings

### Root Cause

The original guard only checked `session_id`:

```python
tickets = [t for t in backlog.list_tickets() if t["id"] == ticket_id]
if tickets and tickets[0].get("session_id"):
    return {"status": "skipped", ...}
```

**Problem:** Between setting `status = "dispatching"` and the background thread writing `session_id` (after subprocess completes), a concurrent heartbeat could see `session_id = None` and dispatch again. The race window spans the entire subprocess lifetime (seconds to minutes).

### Fix Applied (3 layers of defense)

1. **Per-ticket lock** (`_dispatch_locks` dict + `_dispatch_locks_guard`): Non-blocking `lock.acquire(blocking=False)` ensures only one thread enters dispatch for a given ticket. Second caller gets `already_dispatching` immediately.

2. **Compare-and-swap on status**: Inside the lock, the code re-reads ticket status and only proceeds if it's `pending` or `ready`. This catches cases where a prior dispatch already advanced the status.

3. **Lock lifecycle management**: `_cleanup_ticket_lock()` removes the lock entry after dispatch completes (success, error, or timeout), preventing unbounded dict growth. Lock is held for the full subprocess duration via the `_wait()` thread, released in a `finally` block.

### Key code paths changed

| Location | Change |
|----------|--------|
| `foreman.py:30-47` | New: `_dispatch_locks`, `_get_ticket_lock()`, `_cleanup_ticket_lock()` |
| `foreman.py:50-121` | Rewritten `_dispatch_async()` with lock acquisition, CAS check, structured error handling |
| `foreman.py:95-118` | `_wait()` restructured: nested try/finally ensures lock release on all exit paths including timeout |

### Error path analysis

All exit paths correctly release the lock:
- **Lock contention** (line 62): Returns immediately, lock never acquired
- **CAS rejection** (lines 73-74): Explicit `lock.release()` + cleanup
- **Pre-Popen exception** (lines 78-81): `except` block releases + re-raises
- **FileNotFoundError from Popen** (lines 90-92): Release + cleanup + return error
- **Subprocess timeout** (lines 99-103): `_wait()` finally block at lines 116-118
- **Normal completion** (lines 108-115): `_wait()` finally block at lines 116-118

### Test Coverage

5 tests in `backend/tests/test_dispatch_race.py` (all passing):

| Test | What it verifies |
|------|-----------------|
| `test_concurrent_dispatch_same_ticket_blocked` | Two threads race on same ticket; exactly one blocked |
| `test_dispatch_rejects_non_pending_ticket` | CAS rejects ticket with status=dispatching |
| `test_dispatch_rejects_dispatched_ticket` | CAS rejects ticket with status=dispatched |
| `test_lock_cleanup_after_dispatch` | Lock dict entry removed after failed dispatch |
| `test_independent_tickets_dispatch_concurrently` | Different tickets dispatch in parallel (no false blocking) |

## Recommendations

1. **Merge this fix** -- the race is confirmed and the fix is tested.
2. **Monitor for stale locks** -- If a dispatch thread dies without hitting `finally` (e.g., OOM kill), the lock persists. Consider a TTL-based cleanup in the heartbeat loop (remove locks older than 15 minutes).
3. **Consider SQLite-level CAS** -- For multi-process deployments, `UPDATE ... WHERE status IN ('pending', 'ready')` with rowcount check would provide DB-level atomicity. Not urgent since the factory is single-process.

## References

- `backend/foreman.py:30-121` -- lock infrastructure and `_dispatch_async()` implementation
- `backend/tests/test_dispatch_race.py` -- race condition test suite (5 tests)
- `backend/backlog.py` -- `list_tickets()`, `update_ticket()`, `mark_dispatched()` used by dispatch flow
- Incident evidence: workers 1952 and 1953 both running ticket `1f99ec97`
