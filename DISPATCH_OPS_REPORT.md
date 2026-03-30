# Dispatch Ops Report: Factory Process Restart + Validation

**Date:** 2026-03-30 01:40 UTC
**Operator:** dispatch ops worker (unattended)

## 1. Factory Restart

- Killed stale `factory` tmux session (created 2026-03-29 21:29)
- Started new session: `tmux new-session -d -s factory -c /mnt/c/projects/dispatch-factory 'make dev'`
- Health check after 10s startup: `{"status":"ok"}`

## 2. PR #90 Code Verification

```
07269e6 [dispatch] Fix 3 bugs in dispatch-factory backend that cause silent tic (#90)
41b398a fix: add worker_done timeout escalation to prevent sessions stuck indefinitely (#89)
6e8d002 fix: 4 critical bugs breaking completion pipeline (#88)
```

Confirmed: commit `07269e6` (PR #90 bug fixes) is on HEAD.

## 3. Backend Tests

Initial run: **1 failure** in `test_factory_idle_mode.py`

### Bug Found: `backlog.settings` mock targets non-existent attribute

The `backlog` module was migrated to SQLite (via `db.py`) but tests still mocked `backlog.settings` — an attribute that no longer exists. Fixed by mocking `backlog.list_tickets` instead, which is the actual function called by `factory_idle_mode.is_idle()`.

### Bug Found: `_get_active_projects` regex matches section headers

The regex `\*{0,2}([a-z][a-z0-9-]+)\*{0,2}` in `factory_idle_mode._get_active_projects()` matched "ctive" from the header `## Active Projects`. Fixed by restricting matches to list item lines (starting with `-`).

### Final result: **31 passed, 0 failed**

## 4. Backlog Status Post-Restart

| Status | Count |
|--------|-------|
| pending | 11 |
| dispatched | 1 |
| completed | 178 |
| cancelled | 156 |
| failed | 13 |
| needs_input | 3 |
| blocked | 2 |
| **Total** | **364** |

Factory is healthy and dispatching. 11 pending tickets available for processing.

## Code Changes

1. **`backend/factory_idle_mode.py`** — Added list-item filter to `_get_active_projects` regex to prevent matching section headers
2. **`backend/tests/test_factory_idle_mode.py`** — Replaced `mock.patch("backlog.settings")` with `mock.patch("backlog.list_tickets", return_value=[...])` across all test functions (6 occurrences)

## Outcome

Factory restarted successfully with PR #90 code. Two latent bugs found and fixed during test validation. All 31 tests pass. Pipeline operational with 11 pending tickets.
