# Post-Mortem: electricapp Healer Feedback Loop

**Date:** 2026-03-25
**Status:** ON HOLD (blocked until dispatch-factory pause lifts)
**Tags:** healer, post-mortem, electricapp
**Sessions:** 0844, 1028, 1033, bbabcf75

## Summary

The healer's "abort and retry" strategy created a feedback loop on electricapp
where each healer intervention compounded the damage from the previous one.
A merge-conflict failure — caused by the healer's own prior intervention — was
treated as a fresh failure eligible for healing, leading to a spiral of
increasingly broken state.

## Timeline

### Session 0844 — Original Failure
- A dispatch session failed on electricapp (root cause TBD — likely a code or
  test failure).
- The healer detected the failure and intervened with its standard "abort and
  retry" strategy.

### Session 1028 — Healer Retry (First Compound)
- The healer's intervention from 0844 left uncommitted or partially-merged
  changes on the working branch.
- Session 1028 attempted to work on the same project and hit a **merge conflict**
  caused by the healer's prior changes.
- The healer saw this as a new failure and intervened again — aborting the
  conflicting state and retrying. This compounded the damage: the healer was
  now cleaning up its own mess without recognizing it.

### Session 1033 — Healer Spiral (Second Compound)
- Session 1033 encountered the aftermath of two healer interventions.
- The merge-conflict pattern repeated. The healer intervened a third time.
- At this point the project was in a deeply broken state with multiple layers
  of partial healer fixes on top of each other.

### Session bbabcf75 — Fourth Iteration
- A fourth session hit the same compounded state.
- The per-project healer circuit breaker (added in session 1037) eventually
  tripped after reaching the threshold of 2 consecutive healer interventions,
  but only **after** the damage had already been done.

## Root Cause Analysis

### The Fundamental Flaw: No Causality Detection

The healer treats every failure as independent. It has no mechanism to detect
that **its own prior intervention caused the current failure**. The decision
logic is:

```
if session_failed:
    diagnose()
    abort_and_retry()  # Always eligible, regardless of cause
```

What it should be:

```
if session_failed:
    if did_i_cause_this(project, failure_context):
        refuse_to_intervene()
        escalate_to_human()
    else:
        diagnose()
        abort_and_retry()
```

### Why Merge Conflicts Specifically Trigger This

Merge conflicts are the canonical case where healer intervention causes the
next failure:

1. Healer intervenes on session N, leaving changes on the branch
2. Session N+1 starts on the same branch, encounters conflicts with healer's changes
3. The conflict is a **direct consequence** of step 1, not an independent failure
4. Healer sees a failure and intervenes again, making the branch state worse

Other failure types (test failures, build errors) can also be healer-caused,
but merge conflicts are the most common because the healer's primary action is
modifying files on the working branch.

### Why the Circuit Breaker Is Insufficient

The per-project circuit breaker (`healer_circuit_breaker.py`, threshold=2) is a
**post-damage safety net**, not a **pre-intervention guard**:

| Property | Circuit Breaker | Causality Check (proposed) |
|----------|----------------|---------------------------|
| When it acts | After N interventions | Before each intervention |
| What it prevents | Further damage | Initial compounding |
| Damage already done | Yes (N interventions worth) | No (refuses first re-intervention) |
| False positive risk | Low (count-based) | Medium (requires overlap detection) |
| Complementary? | Yes — catches cases causality check misses | Yes — prevents most spirals before threshold |

Both mechanisms should coexist. The circuit breaker catches edge cases where
causality detection fails (indirect causation, delayed effects). The causality
check prevents the common case of direct re-intervention.

## Proposed Design: `did_i_cause_this()` Pre-Intervention Guard

### Interface

```python
def did_i_cause_this(project: str, current_failure: dict) -> bool:
    """Check if the current failure was likely caused by a prior healer intervention.

    Args:
        project: The project name.
        current_failure: Dict with at least:
            - error_type: str (e.g., "merge_conflict", "test_failure", "build_error")
            - changed_files: list[str] — files involved in the failure
            - error_message: str — raw error output

    Returns:
        True if the healer should refuse to intervene (likely self-caused).
    """
```

### Detection Signals

The function should check multiple signals and return True if any are strong:

1. **Direct file overlap**: Compare `current_failure.changed_files` against the
   files touched by the healer in its most recent intervention on this project
   (stored in `healer-circuit-breaker.json` `session_ids` — look up each
   session's healer artifact for `files_changed`).

2. **Merge-conflict marker**: If `error_type == "merge_conflict"`, check whether
   any of the conflicting files appear in the healer's prior session artifacts.
   Merge conflicts after healer intervention are almost always self-caused.

3. **Temporal proximity**: If the last healer intervention on this project was
   within the last N minutes (e.g., 60), apply a lower threshold for file
   overlap. Recent interventions are more likely to be the cause.

4. **Error signature matching**: If the current error message contains references
   to branches, commits, or file paths that appear in the healer's prior
   intervention artifacts, flag as likely self-caused.

### Where to Integrate

The check should be called in `heartbeat.py` at every point where
`healer_circuit_breaker.record_healer_intervention()` is currently called —
specifically in `_reconcile_backlog()` before allowing a healed session to
proceed, and in the dispatch path before allowing `--heal` on a project with
prior healer history.

The most impactful integration point is in the dispatch CLI itself (outside
this repo), where the healer decision is made. The factory-side check is a
secondary guard.

### Edge Cases

- **Indirect causation**: Healer changes file A, CI regenerates file B from A,
  file B conflicts. Naive file-overlap misses this. Mitigation: treat any
  merge conflict after a recent healer intervention as suspect (signal 2+3
  combined).

- **False positives**: A legitimate new failure on a project the healer recently
  touched. Mitigation: the causality check should `flag_human` rather than
  silently blocking — the human can override and dispatch with `--heal` if the
  failure is genuinely independent.

- **Stale state**: Healer intervention was weeks ago; current failure is
  unrelated. Mitigation: apply a time decay — interventions older than 24h get
  much lower weight.

## Action Items

1. **[ON HOLD]** Implement `did_i_cause_this()` in `healer_circuit_breaker.py`
2. **[ON HOLD]** Integrate causality check into `heartbeat.py` reconciliation
3. **[ON HOLD]** Add healer artifact field `files_changed` to dispatch CLI healer output
4. **[DONE]** Per-project circuit breaker (session 1037, `healer_circuit_breaker.py`)
5. **[DONE]** Post-heal deploy verification (`post_heal_verify.py`)
6. **[DONE]** Healed-but-failed root-cause ticket creation (`heartbeat.py:_check_healed_but_failed`)

## References

- `backend/healer_circuit_breaker.py` — existing per-project circuit breaker
- `backend/heartbeat.py:_reconcile_backlog` — where healer interventions are recorded
- `backend/heartbeat.py:_check_healed_but_failed` — existing healed-but-failed escalation
- Commit that added circuit breaker: session 1037
