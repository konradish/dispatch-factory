# Merge Triage Evaluation: PRs #43, #44, and Ticket 27c29002

**Date:** 2026-03-28
**Scope:** Consolidate fixes from tickets 5a42c021, ac58de0f, and 27c29002

## Overall Score: 8/10

All three fixes address real race conditions and correctness bugs in the heartbeat/foreman pipeline. The code changes are minimal, targeted, and backed by lint verification. No architectural risk.

## PRs Evaluated

### PR #43 — Fix 4 backend bugs (ticket 5fdcf259)
- **State:** Already merged (ba79e7e)
- **Assessment:** Sound. Fixes: dead import crash, empty-file edge case in foreman, `shell=True` security violation, race condition on `_active_stream_path` global.
- **Evidence:** `shlex.split()` replaces `shell=True` (pipeline_runner.py:165), `threading.Lock` protects global state (foreman.py:34), empty-content guard (foreman.py:44-45).
- **Verdict:** Correctly merged. No issues found.

### PR #44 — Heartbeat race condition (ticket ac58de0f)
- **State:** Was open/draft, merged during this triage.
- **Assessment:** Sound. Moves `pipeline_runner.scan_for_completions()` to step 1 in `_beat()`, before `_gc_zombie_sessions()`. This prevents the GC from marking completed sessions as abandoned before the pipeline runner writes `-result.md` artifacts.
- **Evidence:** heartbeat.py:90-98 — completion processing now runs first with explanatory comment. Step numbering updated correctly (1-9).
- **Verdict:** Merged. Race condition fix is logically correct.

### Ticket 27c29002 — artifacts.py check-order fix
- **Assessment:** Applied directly. In `_detect_session_state()`, the `result` artifact check now runs before the `abandoned` check. This ensures that sessions with both `-result.md` and `-abandoned.json` are classified as "completed" rather than "abandoned" — the correct outcome when a worker finishes but the GC ran first due to timing.
- **Evidence:** artifacts.py:78-90 — order is now: result > abandoned > error > monitor > verifier > reviewer > planner > running.
- **Verdict:** Applied and lint-verified.

## Strengths

- **Targeted fixes:** Each change addresses a specific, well-understood race condition with minimal blast radius.
- **Consistent root cause:** All three tickets stem from the same timing issue — GC runs before completion artifacts are written. The fixes form a coherent defense-in-depth: (1) PR #44 reorders heartbeat steps so completions process first, (2) ticket 27c29002 ensures state detection prefers completion over abandonment even if artifacts overlap.
- **No behavioral regressions:** Changes only affect edge cases where timing was previously non-deterministic.

## Issues Found

### P2 — No test coverage for artifact state priority
- The `_detect_session_state` function has no unit tests verifying the priority order of artifact checks. The check-order fix is correct but fragile — a future refactor could reintroduce the bug.
- **Recommendation:** Add a test case: `_detect_session_state({"result": {...}, "abandoned": {...}})` should return `"completed"`, not `"abandoned"`.

### P2 — PR #43 scope creep
- PR #43 included 14 files with 1,493 additions beyond the 4 bug fixes (db.py, pipeline_runner.py, ForemanChat.tsx, BacklogView.tsx additions). These appear to be feature work bundled with bug fixes.
- **Recommendation:** Future PRs should separate bug fixes from feature additions for cleaner review.

## Recommendations

1. Add unit tests for `_detect_session_state` artifact priority order.
2. Consider adding an integration test that simulates the race: worker completes while GC is running.
3. Enforce PR scope discipline — bug-fix PRs should not bundle feature work.

## Evidence

| File | Change | Lines |
|------|--------|-------|
| `backend/heartbeat.py` | Completion processing moved to step 1 | 90-98 |
| `backend/artifacts.py` | Result check before abandoned check | 78-90 |
| `backend/foreman.py` | Empty-content guard + thread lock | 34, 44-45 |
| `backend/pipeline_runner.py` | `shlex.split()` replaces `shell=True` | 165 |
