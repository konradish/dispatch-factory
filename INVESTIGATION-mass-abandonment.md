# Investigation: Mass Session Abandonment (17/20 Sessions)

**Date:** 2026-03-22
**Severity:** P1 — #1 reliability issue
**Status:** Root cause identified, recommendations provided

## Executive Summary

17 of 20 recent sessions were marked "abandoned" — but this is **not** a single catastrophic event. The 17 abandonments represent **accumulated failures over 20 days** (March 2–22) that were only surfaced when the Session GC feature (PR #7, commit 293bc54) went live on March 22 at 20:46 UTC-5. The GC's startup scan correctly identified all 17 as zombies and marked them abandoned simultaneously.

The root causes are **three distinct failure modes in the dispatch runner**, not a dispatch-factory misconfiguration.

## Timeline

| Session | Date Created | Idle Minutes | Failure Mode |
|---------|-------------|-------------|--------------|
| worker-movies-1725 | Mar 02 17:26 | 28,937 | Runner crash (unknown — 1-line log) |
| worker-movies-1732 | Mar 02 17:35 | 28,928 | **Completed work but no result artifact** |
| worker-movies-1558 | Mar 06 15:59 | 23,264 | **NameError: `claude_reason` not defined** |
| worker-schoolbrain-1004 | Mar 04 10:06 | 26,496 | **Planner timeout (120s) — crashed** |
| worker-recipebrain-1904 | Mar 04 19:04 | 25,959 | Runner repeated launch loop (no progress) |
| worker-lawpass-1002 | Mar 07 10:02 | 22,181 | Runner crash (2-line log, no artifacts) |
| validate-movies-0016 | Mar 09 00:17 | 19,946 | Validate completed screenshots, no result |
| worker-electricapp-1154 | Mar 09 11:54 | 19,248 | Planner phase started, then silent crash |
| validate-recipebrain-1104 | Mar 09 11:16 | 19,287 | Validate completed screenshots, no result |
| worker-lawpass-1711 | Mar 09 17:12 | 18,930 | **Planner timeout (90s) → "continuing without plan" → silent exit** |
| validate-recipebrain-1706 | Mar 09 17:09 | 18,934 | Validate completed screenshots, no result |
| worker-recipebrain-2105 | Mar 09 21:05 | 18,698 | Runner crash (2-line log, shell output only) |
| worker-lawpass-2105 | Mar 09 21:05 | 18,698 | Runner crash (3-line log, shell output only) |
| worker-electricapp-2109 | Mar 09 21:09 | 18,694 | Runner crash (3-line log, shell output only) |
| deploy-recipebrain-1655 | Mar 10 16:55 | 17,493 | Deploy stuck on `make deploy-prod` (hanging) |
| deploy-recipebrain-1727 | Mar 10 17:43 | 17,459 | Deploy hanging |
| worker-lawpass-1730 | Mar 21 17:32 | 1,631 | Most recent — runner crash (3-line log) |

## Three Root Causes

### Root Cause 1: Runner Script Crashes (10/17 sessions)

**Pattern:** Worker sessions produce only a 2-3 line log (shell prompt + python command), then the tmux session drops to a bare shell. No error artifact, no pipeline artifacts, no `-error.json`.

**Why no error artifact:** The `-run.py` runner script crashes *before* reaching its error-handling code. When `proc = subprocess.Popen(['cy', '-p', prompt])` either:
- The `cy` binary fails to start (OAuth token expired, missing dependency)
- The planner phase crashes (NameError, timeout), and the crash is *unhandled* in older runner versions
- The runner process itself dies (OOM, signal)

**Evidence:**
- `worker-movies-1558`: `NameError: name 'claude_reason' is not defined` — the runner script was generated before `claude_reason()` was added as a function (dispatch CLI bug)
- `worker-schoolbrain-1004`: `subprocess.TimeoutExpired` during planner phase — 120s timeout in `subprocess.run()` crashes the entire runner (no try/except)
- `worker-lawpass-1711`: Planner timed out at 90s, then "continuing without plan" — but no further log output suggests `cy` process failed silently

**Impact:** The runner writes no `-error.json` when it crashes early, so the session appears "running" forever until GC catches it.

### Root Cause 2: Missing Result Artifacts from Completed Work (3/17 sessions)

**Pattern:** `worker-movies-1732` shows a full PR creation in its log ("PR created: https://github.com/..."), yet no `-result.md` was written. The session completed its work but the post-worker pipeline (reviewer → verifier → result) never ran.

**Why:** The runner's post-worker stages (reviewer, verifier) are downstream of `proc.wait()`. If the worker exits with code 0 but the runner doesn't reach the result-writing stage (process killed externally, runner crash after worker completes), no result artifact is created.

**Evidence:**
- `worker-movies-1732` log contains "PR created: https://github.com/konradish/family-movie-queue/pull/1" and a summary of changes — the *work was done* but the session is abandoned

### Root Cause 3: Deploy/Validate Hangs (4/17 sessions)

**Pattern:** Deploy sessions start their pipeline (`make deploy-prod`) and hang indefinitely. Validate sessions complete their screenshots but never write a completion artifact.

**Evidence:**
- `deploy-recipebrain-1655`: Last log line is `[verifier] deploy-prod: make deploy-prod` — the make target hung
- `validate-movies-0016`, `validate-recipebrain-1104`, `validate-recipebrain-1706`: All have visual screenshot artifacts but no completion marker

**Why:** Deploy targets may prompt for confirmation or hang on network issues. The runner's watchdog timeout (3600s = 60min) should catch this, but the deploy/validate runners may not have the watchdog thread.

## Why This is NOT a dispatch-factory Bug

The Session GC (PR #7) **worked exactly as designed**:

1. All 17 sessions had no active tmux worker process (confirmed by `tmux list-panes`)
2. All 17 had log files idle for 1,631–28,937 minutes (1–20 days)
3. The 30-minute `ZOMBIE_THRESHOLD_MINUTES` correctly identified them as zombies
4. The startup GC sweep on March 22 correctly marked them all abandoned

**The GC is the cure, not the disease.** Without it, these 17 sessions would remain in "running" state indefinitely, polluting the pipeline view.

## Recommendations

### Immediate (dispatch CLI fixes)

1. **Wrap planner phase in try/except** — planner crashes should not kill the entire runner. The runner should continue to the worker phase. (`worker-movies-1558`, `worker-schoolbrain-1004`)

2. **Always write `-error.json` on runner crash** — add a top-level try/except in `-run.py` that catches any exception and writes an error artifact before exiting. This ensures GC is not the only way to detect failures.

3. **Verify `claude_reason` is defined before use** — the dispatch CLI's runner template must include all function definitions. The `NameError` on `worker-movies-1558` means the function was referenced before being added to the template.

### Medium-term (dispatch-factory improvements)

4. **Add a "crashed" state** — sessions with only a `.log` file and no artifacts after >30 minutes should be classified as "crashed" rather than "running". The current state detection in `artifacts._detect_session_state()` treats any session with only a log as "running" — this masks early crashes.

5. **Heartbeat real-time GC** — the current heartbeat interval defaults to 30 minutes. Zombie sessions accumulate between beats. Consider a shorter interval (5 min) for GC specifically.

6. **Deploy timeout** — deploy runners should have the same watchdog thread as worker runners to prevent indefinite hangs.

### Structural

7. **Runner should signal lifecycle** — write a `-started.json` artifact when the runner begins and a `-exited.json` when it ends (regardless of success/failure). This creates a clear lifecycle bracket that the factory can monitor.

## Sessions That Succeeded

For context, 3 of the 20 recent sessions did complete successfully. The successful sessions had proper result artifacts and completed the full pipeline. The 85% failure rate (17/20) is dominated by early runner crashes, not pipeline logic failures.

## Files Referenced

- `backend/heartbeat.py:165-200` — GC logic (working correctly)
- `backend/artifacts.py:_detect_session_state()` — State derivation (masks early crashes as "running")
- `~/.local/bin/dispatch` — Dispatch CLI (runner template generation)
- `~/.local/share/dispatch/` — Session artifacts directory
