# Dispatch Ops Report — 2026-03-30

## Task
Fix two dispatch reliability bugs in `backend/heartbeat.py`.

## Changes Made

### BUG 1 — Duplicate dispatch race (heartbeat.py:784)
**Problem:** The dedup prefix set in `_auto_dispatch()` only checked tickets with `status="dispatched"`, missing tickets in the `dispatching` transitional state. During the dispatching-to-dispatched window, a duplicate ticket could slip through.

**Fix:** Changed line 784 to include both statuses:
```python
# Before
inflight_tickets = backlog.list_tickets(status="dispatched")
# After
inflight_tickets = backlog.list_tickets(status="dispatched") + backlog.list_tickets(status="dispatching")
```

### BUG 2 — False deploy recording (heartbeat.py:237)
**Problem:** The `state=='completed'` block called `healer_circuit_breaker.record_successful_deploy(project)`, but `completed` means the session finished without a verified deploy. Only `deployed` state confirms a deploy actually landed. This falsely inflated the healer circuit breaker's success count.

**Fix:** Removed the `record_successful_deploy` call from the `completed` block (line 237). The `deployed` block (lines 205-216) already correctly calls it.

## Test Results
- 50 tests passed, 8 failed (pre-existing failures unrelated to these changes)
- Pre-existing failures confirmed identical on `main` before changes (SQLite DB path issues in `test_alert_lifecycle.py` and `test_meta_work_ratio.py`)
- All dispatch-relevant tests pass: `test_dispatch_race.py` (8/8), `test_healer_circuit_breaker.py` (9/9), `test_post_heal_verify.py` (4/4)

## Files Modified
- `backend/heartbeat.py` — 2 lines changed (1 added, 1 removed)
