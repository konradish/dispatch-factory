"""Factory heartbeat — periodic health check and auto-dispatch.

The heartbeat runs every N seconds and:
1. Checks active worker health (stuck detection)
2. Reconciles backlog tickets with completed sessions
3. Optionally auto-dispatches pending tickets when capacity is available

Auto-dispatch is gated by config — off by default.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time

import artifacts
import backlog
import circuit_breaker
import cleared_healed_sessions
import healer_circuit_breaker
import empty_backlog_detector
import factory_idle_mode
import meta_work_ratio
import paused_projects
import post_heal_verify
from config import settings

logger = logging.getLogger("dispatch-factory.heartbeat")

# Track heartbeat state
_state: dict = {
    "last_beat": 0.0,
    "beats": 0,
    "last_actions": [],
    "auto_dispatch_enabled": settings.heartbeat.auto_dispatch,
    "max_concurrent": settings.heartbeat.max_concurrent,
    "enabled": settings.heartbeat.enabled,
    "interval_minutes": settings.heartbeat.interval_minutes,
    "foreman_every_n_beats": 5,  # Default: every 5th beat (~2.5 min). Set higher to save quota.
}


def get_state() -> dict:
    """Return current heartbeat state."""
    return {**_state, "uptime_seconds": time.time() - _state.get("started_at", time.time())}


async def heartbeat_loop(interval: int | None = None) -> None:
    """Main heartbeat loop — runs as a background asyncio task."""
    if not _state.get("enabled", False):
        logger.info("Heartbeat disabled in config")
        return

    if interval is None:
        interval = _state["interval_minutes"] * 60

    _state["started_at"] = time.time()
    logger.info("Heartbeat started (interval=%ds, auto_dispatch=%s)", interval, _state["auto_dispatch_enabled"])

    # Run completion processing + GC on startup, mirroring _beat() order:
    # process completions BEFORE zombie GC so pipeline_runner can write
    # -result.md artifacts before sessions are marked abandoned.
    startup_actions = []
    import pipeline_runner
    for completion in pipeline_runner.scan_for_completions():
        try:
            startup_actions.extend(pipeline_runner.process_worker_completion(completion))
        except Exception as e:
            sid = completion.get('_session_id', 'unknown')
            startup_actions.append(f'pipeline_runner error for {sid}: {e}')
            logger.warning('startup completion processing failed for %s: %s', sid, e)
    try:
        startup_actions.extend(_gc_zombie_sessions())
    except Exception:
        logger.exception("Startup GC error")
    if startup_actions:
        logger.info("Startup GC: %s", startup_actions)

    while True:
        try:
            # Run _beat in thread pool so it doesn't block the async event loop
            loop = asyncio.get_event_loop()
            actions = await loop.run_in_executor(None, _beat)
            _state["last_beat"] = time.time()
            _state["beats"] += 1
            _state["last_actions"] = actions
            if actions:
                logger.info("Heartbeat #%d: %s", _state["beats"], actions)
        except Exception:
            logger.exception("Heartbeat error")

        await asyncio.sleep(interval)


def _beat() -> list[str]:
    """Single heartbeat tick. Returns list of actions taken."""
    actions: list[str] = []

    # 1. Process completed workers (post-worker pipeline stages)
    #    Runs BEFORE zombie GC so that pipeline_runner can write -result.md
    #    artifacts before sessions are marked abandoned.
    import pipeline_runner
    for completion in pipeline_runner.scan_for_completions():
        try:
            actions.extend(pipeline_runner.process_worker_completion(completion))
        except Exception as e:
            sid = completion.get('_session_id', 'unknown')
            actions.append(f'pipeline_runner error for {sid}: {e}')
            logger.warning('completion processing failed for %s: %s', sid, e)
            # Write an error result so scan_for_completions won't retry forever.
            try:
                pipeline_runner._write_result(
                    sid, f"FAILED (pipeline_runner error: {e})", "", ""
                )
                actions.append(f'wrote error result for {sid} to stop retry loop')
            except Exception:
                logger.exception('failed to write error result for %s', sid)

    # 2. Garbage-collect zombie sessions (running state, no active worker)
    actions.extend(_gc_zombie_sessions())

    # 3. Reconcile backlog with completed sessions (includes abandoned)
    actions.extend(_reconcile_backlog())

    # 4. Check for stuck workers
    actions.extend(_check_stuck_workers())

    # 5. Check for empty backlog with projects needing human direction
    actions.extend(_check_empty_backlog())

    # 5b. Factory idle mode: emit factory-wide flag_human reminder (24h cooldown)
    idle_flag = factory_idle_mode.check_and_flag()
    if idle_flag:
        actions.append(idle_flag)

    # 6. Sweep orphaned healed-but-unverified sessions not covered by
    #    backlog reconciliation (root cause of persistent alerts on
    #    dispatch-factory, lawpass, recipebrain — see PR #32).
    actions.extend(_sweep_orphaned_healed_sessions())

    # 7. Reviewer calibration: DISABLED — no LLM reviewer in current pipeline.
    # Re-enable when LLM reviewer stage is added to pipeline_runner.py.
    # actions.extend(reviewer_calibration.check_and_run())

    # 8. Auto-dispatch pending tickets when capacity available
    if _state.get("auto_dispatch_enabled", False):
        actions.extend(_auto_dispatch())

    # 9. Run foreman in background thread — every Nth beat (configurable)
    #    Foreman can take minutes with 100 turns. Must not block the event loop.
    foreman_every = _state.get("foreman_every_n_beats", 5)
    if _state.get("auto_dispatch_enabled", False) and _state["beats"] % foreman_every == 0:
        import threading
        def _run_foreman_bg():
            try:
                import foreman
                result = foreman.run_foreman()
                summary = ""
                if result.get("actions"):
                    summary = f"foreman[{result.get('lens', '?')}]: {len(result['actions'])} actions"
                elif result.get("assessment"):
                    summary = f"foreman[{result.get('lens', '?')}]: {result['assessment'][:80]}"
                if summary:
                    _state["last_actions"] = _state.get("last_actions", []) + [summary]
            except Exception as e:
                _state["last_actions"] = _state.get("last_actions", []) + [f"foreman error: {e}"]
        threading.Thread(target=_run_foreman_bg, daemon=True).start()
        actions.append("foreman: started in background")

    return actions


def _reconcile_backlog() -> list[str]:
    """Check dispatched tickets — mark completed/failed based on session state."""
    actions = []

    # Stale dispatching tickets: if dispatching for >10 min with no session_id,
    # the _dispatch_async background thread died. Reset to pending.
    for ticket in backlog.list_tickets(status="dispatching"):
        dispatched_at = ticket.get("dispatched_at")
        if not dispatched_at:
            continue  # Can't compute age without dispatched_at
        dispatched_age = time.time() - dispatched_at
        if dispatched_age > 600 and not ticket.get("session_id"):
            backlog.update_ticket(ticket["id"], {"status": "pending"})
            actions.append(f"reset stale dispatching ticket {ticket['id']} → pending ({dispatched_age/60:.0f}m old)")

    dispatched = backlog.list_tickets(status="dispatched")
    if not dispatched:
        return actions

    for ticket in dispatched:
        session_id = ticket.get("session_id")
        if not session_id:
            continue

        session = artifacts.get_session(session_id)
        if not session:
            continue

        state = session.get("state", "")
        project = ticket.get("project", "unknown")
        if state == "deployed":
            healed = _session_was_healed(session)
            if healed:
                # Post-heal verification: don't trust DEPLOYED status after
                # healer intervention — run a health check first.
                actions.extend(healer_circuit_breaker.record_healer_intervention(project, session_id))
                actions.extend(_verify_healed_deploy(session, project, session_id, ticket))
            else:
                backlog.mark_completed(ticket["id"], "completed")
                actions.append(f"ticket {ticket['id']} completed ({session_id})")
                actions.extend(circuit_breaker.record_result(project, success=True))
                actions.extend(healer_circuit_breaker.record_successful_deploy(project))
                actions.extend(_maybe_create_auto_verify_ticket(ticket, session_id))
        elif state == "completed":
            # "completed" means result.md exists but verifier didn't report DEPLOYED.
            # If the session was healed, this is suspicious — the healer may have
            # skipped deploy or masked a failure.  Treat as a deploy-unverified
            # failure to prevent false confidence.
            healed = _session_was_healed(session)
            if healed:
                actions.extend(healer_circuit_breaker.record_healer_intervention(project, session_id))
                rebase_reason = _healer_left_rebase_paused(project)
                if rebase_reason:
                    actions.append(f"healer-rebase-paused: {project} ({session_id}) — {rebase_reason}")
                backlog.mark_completed(ticket["id"], "failed")
                actions.append(f"ticket {ticket['id']} healed-but-unverified ({session_id})")
                actions.extend(circuit_breaker.record_result(project, success=False))
                actions.extend(_escalate_healed_unverified(session, project, session_id))
            else:
                backlog.mark_completed(ticket["id"], "completed")
                actions.append(f"ticket {ticket['id']} completed ({session_id})")
                actions.extend(circuit_breaker.record_result(project, success=True))
                actions.extend(healer_circuit_breaker.record_successful_deploy(project))
                actions.extend(_maybe_create_auto_verify_ticket(ticket, session_id))
        elif state in ("error", "rolled_back"):
            healed = _session_was_healed(session)
            if healed:
                actions.extend(healer_circuit_breaker.record_healer_intervention(project, session_id))
            backlog.mark_completed(ticket["id"], "failed")
            label = "rolled back" if state == "rolled_back" else "failed"
            actions.append(f"ticket {ticket['id']} {label} ({session_id})")
            actions.extend(circuit_breaker.record_result(project, success=False))
            actions.extend(_check_healed_but_failed(session, project, session_id))
        elif state == "abandoned":
            backlog.mark_completed(ticket["id"], "failed")
            actions.append(f"ticket {ticket['id']} abandoned ({session_id})")
            actions.extend(circuit_breaker.record_result(project, success=False))
        elif state == "worker_done":
            # Worker finished but result.md not yet written by process_worker_completion.
            # Check how long it's been stuck — if >5 min, force one retry then fail.
            worker_done_file = artifacts._artifacts_path() / f"{session_id}-worker-done.json"
            worker_done_age = 0.0
            if worker_done_file.is_file():
                try:
                    worker_done_age = time.time() - worker_done_file.stat().st_mtime
                except OSError:
                    pass

            if worker_done_age <= 300:
                # Still fresh — let scan_for_completions handle it on the next heartbeat cycle.
                logger.warning("ticket %s session %s in worker_done — deferring to next cycle", ticket["id"], session_id)
            else:
                # Stuck >5 min — force one retry of process_worker_completion
                logger.warning(
                    "ticket %s session %s stuck in worker_done for %.0fs — forcing completion retry",
                    ticket["id"], session_id, worker_done_age,
                )
                import pipeline_runner
                try:
                    done_data = {}
                    try:
                        import json as _json
                        done_data = _json.loads(worker_done_file.read_text())
                    except (OSError, ValueError):
                        pass
                    done_data["_session_id"] = session_id
                    retry_actions = pipeline_runner.process_worker_completion(done_data)
                    actions.extend(retry_actions)
                    actions.append(f"worker_done escalation: retried {session_id} after {worker_done_age/60:.0f}m")
                except Exception as e:
                    logger.error(
                        "worker_done escalation failed for %s: %s — writing error result and marking failed",
                        session_id, e,
                    )
                    try:
                        pipeline_runner._write_result(
                            session_id,
                            f"FAILED (worker_done stuck {worker_done_age/60:.0f}m, retry error: {e})",
                            "",
                            "",
                        )
                    except Exception:
                        logger.exception("failed to write error result for stuck worker_done %s", session_id)
                    backlog.mark_completed(ticket["id"], "failed")
                    actions.append(
                        f"worker_done escalation: {session_id} failed after {worker_done_age/60:.0f}m — marked failed"
                    )

    return actions


def _session_was_healed(session: dict) -> bool:
    """Check if a session had healer intervention."""
    healer = session.get("artifacts", {}).get("healer")
    return isinstance(healer, dict)


_DEPLOY_FIX_RE = re.compile(r"\b(deploy|fix)\b", re.IGNORECASE)


def _maybe_create_auto_verify_ticket(ticket: dict, session_id: str) -> list[str]:
    """Auto-create a P1 verification ticket for deploy/fix tasks.

    When a worker_done session's task contains 'deploy' or 'fix', we create
    a follow-up verification ticket so the result is explicitly confirmed.
    """
    if not settings.heartbeat.auto_verify:
        return []

    task_text = ticket.get("task", "")
    if not _DEPLOY_FIX_RE.search(task_text):
        return []

    project = ticket.get("project", "unknown")

    # Respect verification depth limits to prevent runaway verify chains.
    if _verification_depth_exceeded(project):
        return []

    verify_task = (
        f"Verify {project} after completed task (session {session_id}): {task_text[:200]}"
    )
    verify_ticket = backlog.create_ticket(
        task=verify_task,
        project=project,
        priority="urgent",
        source="auto-verify",
        task_type="verify",
    )
    return [
        f"auto-verify: created P1 verification ticket {verify_ticket['id']} "
        f"for deploy/fix task ({session_id})"
    ]


def _healer_left_rebase_paused(project: str) -> str | None:
    """Check if a healer's git operation left a rebase paused in the worktree.

    Returns a reason string if rebase is in progress, None otherwise.
    This catches the case where run_fix_then_retry's git pull --rebase
    hits a merge conflict and is left awaiting manual resolution.
    """
    return post_heal_verify._detect_rebase_in_progress(project)


def _verification_depth_exceeded(project: str) -> bool:
    """Check if verification ticket chain for a project has hit max depth.

    Counts recent tickets with verify/escalation sources for the same project.
    When depth >= max_verification_depth, returns True to prevent runaway
    verification chains (e.g. the 4-deep chain that consumed 5 workers to
    answer 1 question about electricapp's deploy status).
    """
    max_depth = settings.heartbeat.max_verification_depth
    verify_sources = {"healer-verification", "healer", "healer-circuit-breaker", "auto-verify"}

    # Count all verify-chain tickets for this project that are pending, dispatching,
    # dispatched, or recently completed/failed (within 1 hour).
    all_tickets = backlog.list_tickets()
    now = time.time()
    one_hour_ago = now - 3600

    depth = 0
    for t in all_tickets:
        if t.get("project") != project:
            continue
        if t.get("source") not in verify_sources:
            continue
        status = t.get("status", "")
        if status in ("pending", "dispatching", "dispatched"):
            depth += 1
        elif status in ("completed", "failed"):
            completed_at = t.get("completed_at") or 0
            if completed_at > one_hour_ago:
                depth += 1

    if depth >= max_depth:
        logger.warning(
            "Verification depth %d >= max %d for %s — skipping new verify ticket",
            depth, max_depth, project,
        )
        return True
    return False


def _write_depth_exceeded_result(session_id: str, project: str, reason: str) -> None:
    """Write a result.md summarizing why verification was capped instead of spawning another worker."""
    import pipeline_runner
    pipeline_runner._write_result(
        session_id,
        f"VERIFICATION_DEPTH_EXCEEDED — {reason}",
        "",
        f"Verification chain for {project} hit max depth; no further workers spawned",
    )


def _escalate_healed_unverified(session: dict, project: str, session_id: str) -> list[str]:
    """Escalate a healed session that completed without verified deploy.

    When the healer intervenes and the session reaches 'completed' state
    (has result.md) but the verifier never reported DEPLOYED, the healer
    likely skipped or masked the deploy failure.  This creates a high-priority
    ticket and prevents the false-success pattern seen in electricapp-2221
    and dispatch-factory-2203.
    """
    # --- Verification depth guard ---
    if _verification_depth_exceeded(project):
        _write_depth_exceeded_result(session_id, project, "healed-but-unverified")
        cleared_healed_sessions.clear_session(
            session_id,
            reason="auto-cleared after verification depth exceeded",
            source="heartbeat",
        )
        return [
            f"verification-depth-exceeded: {project} ({session_id}) — "
            f"skipped creating verify ticket (depth >= {settings.heartbeat.max_verification_depth})"
        ]

    healer = session.get("artifacts", {}).get("healer", {})
    action = healer.get("action", "unknown")
    diagnosis = healer.get("diagnosis", "")[:200]

    verifier = session.get("artifacts", {}).get("verifier", {})
    deploy_status = verifier.get("status", "unknown") if isinstance(verifier, dict) else "missing"
    stations = verifier.get("stations", verifier.get("stages", {})) if isinstance(verifier, dict) else {}

    task = (
        f"Post-heal deploy verification FAILED: {project} session {session_id} "
        f"was healed ({action}) but deploy was never verified "
        f"(verifier status: {deploy_status}, stations: {stations}). "
        f"Healer diagnosis: {diagnosis}"
    )

    ticket = backlog.create_ticket(
        task=task,
        project=project,
        priority="high",
        source="healer-verification",
    )

    # Auto-clear the session from the health dashboard alert now that an
    # escalation ticket exists.  Without this, the healed_deploy_unverified
    # alert accumulates across all projects forever, diluting its signal.
    was_cleared = cleared_healed_sessions.clear_session(
        session_id,
        reason=f"auto-cleared after escalation ticket {ticket['id']}",
        source="heartbeat",
    )
    if was_cleared:
        logger.info(
            "Auto-cleared healed session %s for %s (ticket %s) — "
            "verifying write-back to %s",
            session_id, project, ticket["id"],
            cleared_healed_sessions.CLEARED_FILE,
        )
    else:
        logger.warning(
            "Session %s was already cleared — alert should not have fired "
            "for %s. Possible read/write inconsistency in %s",
            session_id, project,
            cleared_healed_sessions.CLEARED_FILE,
        )

    logger.warning(
        "Healed-but-unverified: %s session %s completed without deploy — "
        "created escalation ticket %s",
        project,
        session_id,
        ticket["id"],
    )

    return [
        f"healer-verification: {project} ({session_id}) healed but deploy "
        f"unverified — escalation ticket {ticket['id']}"
    ]


def _verify_healed_deploy(
    session: dict, project: str, session_id: str, ticket: dict,
) -> list[str]:
    """Run post-heal deploy verification before recording a healed session as success.

    Even when the verifier reports DEPLOYED after healing, the deploy may be
    broken.  Session 2247 found 2/3 healed sessions had broken deploys despite
    DEPLOYED status.  This runs a lightweight health check and only records
    success if it passes.
    """
    result = post_heal_verify.verify_deploy(project, session_id)
    post_heal_verify.write_verification_artifact(session_id, result)

    if result["status"] == "passed":
        backlog.mark_completed(ticket["id"], "completed")
        logger.info(
            "Post-heal verification PASSED for %s (%s): %s",
            session_id, project, result["reason"],
        )
        circuit_actions = circuit_breaker.record_result(project, success=True)
        return [
            f"ticket {ticket['id']} completed — heal-verified ({session_id})",
            *circuit_actions,
        ]

    if result["status"] == "skipped":
        # No URL configured — treat healed+deployed as unverified failure
        # to prevent false confidence.  Projects should configure a health
        # check URL to get credit for healed deploys.
        backlog.mark_completed(ticket["id"], "failed")
        logger.warning(
            "Post-heal verification SKIPPED for %s (%s): %s — "
            "treating as unverified failure",
            session_id, project, result["reason"],
        )
        circuit_actions = circuit_breaker.record_result(project, success=False)
        escalation = _escalate_healed_unverified(session, project, session_id)
        return [
            f"ticket {ticket['id']} healed-deploy-unverified ({session_id}): {result['reason']}",
            *circuit_actions,
            *escalation,
        ]

    # status == "failed"
    backlog.mark_completed(ticket["id"], "failed")
    logger.warning(
        "Post-heal verification FAILED for %s (%s): %s",
        session_id, project, result["reason"],
    )
    circuit_actions = circuit_breaker.record_result(project, success=False)
    escalation = _escalate_healed_unverified(session, project, session_id)
    return [
        f"ticket {ticket['id']} healed-deploy-failed ({session_id}): {result['reason']}",
        *circuit_actions,
        *escalation,
    ]


def _check_healed_but_failed(session: dict, project: str, session_id: str) -> list[str]:
    """If the session was healed but still failed, create a root-cause ticket.

    When the healer intervenes and the deploy still fails, it indicates a
    systemic issue that needs human investigation — not another automated
    retry.  Auto-creating a root-cause ticket prevents the pattern from
    being silently masked (e.g. electricapp 3x heal-then-fail).
    """
    healer = session.get("artifacts", {}).get("healer")
    if not isinstance(healer, dict):
        return []

    # --- Verification depth guard ---
    if _verification_depth_exceeded(project):
        _write_depth_exceeded_result(session_id, project, "healed-but-failed")
        return [
            f"verification-depth-exceeded: {project} ({session_id}) — "
            f"skipped creating root-cause ticket (depth >= {settings.heartbeat.max_verification_depth})"
        ]

    action = healer.get("action", "unknown")
    diagnosis = healer.get("diagnosis", "")[:200]

    task = (
        f"Root-cause investigation: {project} deploy failed after healer "
        f"intervention ({action}). Session {session_id}. "
        f"Healer diagnosis: {diagnosis}"
    )

    ticket = backlog.create_ticket(
        task=task,
        project=project,
        priority="high",
        source="healer",
    )

    logger.warning(
        "Healed-but-failed: %s session %s — created root-cause ticket %s",
        project,
        session_id,
        ticket["id"],
    )

    return [f"healer-accountability: created root-cause ticket {ticket['id']} for {project} ({session_id})"]


def _check_stuck_workers() -> list[str]:
    """Detect workers that have been running too long without producing artifacts."""
    actions = []
    active = artifacts.get_active_sessions()

    for session in active:
        sid = session["id"]
        detail = artifacts.get_session(sid)
        if not detail:
            continue

        # If worker has been running >60min with no artifacts, flag it
        log_path = artifacts._artifacts_path() / f"{sid}.log"
        if log_path.is_file():
            try:
                age_minutes = (time.time() - log_path.stat().st_mtime) / 60
                # Log file not updated in 60+ minutes = likely stuck
                if age_minutes > 60 and not detail.get("artifacts"):
                    actions.append(f"stuck: {sid} (no artifacts, log idle {int(age_minutes)}min)")
            except OSError:
                pass

    return actions


# Minimum age (minutes) before a session with no active worker is considered a zombie.
ZOMBIE_THRESHOLD_MINUTES = 30


def _gc_zombie_sessions() -> list[str]:
    """Detect and mark zombie sessions — running state with no active tmux worker.

    A zombie is a session whose most recent artifact hasn't been updated in
    ZOMBIE_THRESHOLD_MINUTES and whose tmux pane is either gone or dropped
    back to a bare shell.
    """
    actions: list[str] = []
    active_ids = {s["id"] for s in artifacts.get_active_sessions()}
    all_sessions = artifacts.list_sessions()

    for session in all_sessions:
        sid = session["id"]
        if session["state"] not in ("running", "planning"):
            continue
        if sid in active_ids:
            continue  # Worker is still alive

        # Check age — only GC if log file is old enough
        log_path = artifacts._artifacts_path() / f"{sid}.log"
        if not log_path.is_file():
            continue
        try:
            age_minutes = (time.time() - log_path.stat().st_mtime) / 60
        except OSError:
            continue

        if age_minutes < ZOMBIE_THRESHOLD_MINUTES:
            continue

        # Skip if worker_done artifact exists (worker finished, pipeline will pick it up)
        worker_done_file = artifacts._artifacts_path() / f"{sid}-worker-done.json"
        if worker_done_file.is_file():
            continue

        # Mark as abandoned
        if artifacts.abandon_session(sid, reason=f"no active worker, idle {int(age_minutes)}min"):
            actions.append(f"gc: abandoned {sid} (idle {int(age_minutes)}min)")
            logger.info("GC abandoned zombie session %s (idle %dmin)", sid, int(age_minutes))

    # Kill tmux sessions for completed workers that pipeline_runner already processed
    # (has result.md). These are dead shells sitting around. Check worker_done + any
    # state that shouldn't still have a tmux session if result.md exists.
    import subprocess as _sp
    for session in all_sessions:
        sid = session["id"]
        if sid in active_ids:
            continue  # Still running a real process
        result_path = artifacts._artifacts_path() / f"{sid}-result.md"
        if not result_path.is_file():
            continue  # Pipeline hasn't processed yet — leave it
        # Check if tmux session still exists
        try:
            r = _sp.run(["tmux", "has-session", "-t", sid], capture_output=True, timeout=5)
            if r.returncode == 0:
                _sp.run(["tmux", "kill-session", "-t", sid], capture_output=True, timeout=5)
                actions.append(f"gc: killed completed tmux {sid}")
        except (_sp.TimeoutExpired, FileNotFoundError):
            pass

    return actions


def _check_empty_backlog() -> list[str]:
    """Detect projects with empty backlogs that need human direction.

    When pending tickets reach 0 for a project and the direction vector
    contains 'HUMAN INPUT NEEDED', escalate a flag_human reminder every
    24 hours until direction is provided.  The factory should not silently
    idle on product work.
    """
    actions: list[str] = []
    flaggable = empty_backlog_detector.detect()

    for entry in flaggable:
        if not entry["should_flag"]:
            continue

        project = entry["project"]
        empty_backlog_detector.record_flag(project)
        logger.warning(
            "Empty backlog + HUMAN INPUT NEEDED: %s has no pending tickets "
            "and direction vector requests human input — flagging foreman",
            project,
        )
        actions.append(
            f"flag_human: {project} has empty backlog and needs human direction "
            f"(HUMAN INPUT NEEDED in direction vector)"
        )

    return actions


def _sweep_orphaned_healed_sessions() -> list[str]:
    """Auto-clear healed-but-unverified sessions missed by backlog reconciliation.

    The auto-clear in _escalate_healed_unverified only runs when a dispatched
    ticket is reconciled.  Sessions that were reconciled before auto-clear was
    added, or sessions started outside the backlog (manual dispatch CLI), are
    never auto-cleared and cause the healed_deploy_unverified alert to persist
    indefinitely.  This sweep closes the gap by using the same criteria as the
    health check (project_health.py) to find and clear orphaned sessions.
    """
    actions: list[str] = []
    sessions = artifacts.list_sessions_with_timestamps()
    already_cleared = cleared_healed_sessions.get_cleared_ids()
    paused = paused_projects.get_paused()

    for s in sessions:
        if s["project"] in paused:
            continue
        if not s.get("summary", {}).get("healed", False):
            continue
        if s["state"] != "completed":
            continue
        if s["id"] in already_cleared:
            continue

        cleared_healed_sessions.clear_session(
            s["id"],
            reason="auto-cleared by heartbeat sweep (orphaned healed session)",
            source="heartbeat-sweep",
        )
        actions.append(f"sweep-cleared: {s['id']} ({s['project']})")
        logger.info(
            "Heartbeat sweep: auto-cleared orphaned healed session %s for %s",
            s["id"], s["project"],
        )

    return actions


def _auto_dispatch() -> list[str]:
    """Auto-dispatch pending/ready tickets if worker capacity is available."""
    actions = []

    # Factory idle mode: hard stop — no dispatches when all projects need human input
    if factory_idle_mode.is_idle():
        actions.append("factory_idle: all active projects need human input — dispatch blocked")
        return actions

    # Reviewer calibration gate: DISABLED — no LLM reviewer in current pipeline.
    # cal_state = reviewer_calibration.get_calibration_state()
    # if cal_state.get("consecutive_failures", 0) > 0:
    #     actions.append("reviewer_miscalibrated: dispatch blocked until calibration passes")
    #     return actions

    active = artifacts.get_active_sessions()
    max_concurrent = _state.get("max_concurrent", 3)

    if len(active) >= max_concurrent:
        return actions

    blocked_projects: set[str] = set()
    slots = max_concurrent - len(active)
    dispatched_count = 0
    pending = backlog.list_tickets(status="pending") + backlog.list_tickets(status="ready")

    # Build set of in-flight task prefixes for dedup guard
    inflight_tickets = backlog.list_tickets(status="dispatched")
    inflight_prefixes: dict[str, str] = {}  # task[:80] -> ticket_id
    for t in inflight_tickets:
        prefix = t.get("task", "")[:80]
        if prefix:
            inflight_prefixes[prefix] = t["id"]

    # Sort by priority like next_pending does
    priority_order = {"urgent": 0, "high": 1, "normal": 2, "low": 3}
    pending.sort(key=lambda t: (priority_order.get(t.get("priority", "normal"), 2), t["created_at"]))

    for ticket in pending:
        if dispatched_count >= slots:
            break

        # Task-type guard: skip verify tickets — dispatch binary doesn't support
        # the 'verify' task type, so they fail silently and cycle forever.
        if ticket.get('task_type') == 'verify':
            actions.append(f"skipped {ticket['id']}: verify task_type not dispatchable")
            continue

        # Pre-dispatch guard: skip if project already has an in-flight ticket
        if backlog.has_inflight_ticket(ticket["project"]):
            actions.append(f"skipped {ticket['id']}: {ticket['project']} already has in-flight ticket")
            continue

        # Task-text dedup guard: skip if task prefix matches any in-flight ticket
        task_prefix = ticket.get("task", "")[:80]
        if task_prefix and task_prefix in inflight_prefixes:
            other_id = inflight_prefixes[task_prefix]
            logger.info("skipped %s: task text matches in-flight %s", ticket["id"], other_id)
            actions.append(f"skipped {ticket['id']}: task text matches in-flight {other_id}")
            continue

        # Meta-work ratio: block dispatch-factory work when ratio is too high
        if ticket["project"] == "dispatch-factory" and meta_work_ratio.is_blocked(ticket.get("priority", "normal")):
            actions.append(f"meta-work-ratio blocked dispatch for {ticket['id']} (dispatch-factory)")
            continue

        # Circuit breaker: block dispatches to projects with consecutive failures
        if ticket["project"] in blocked_projects:
            continue
        if circuit_breaker.is_project_blocked(ticket["project"]):
            blocked_projects.add(ticket["project"])
            tags = ticket.get("tags", [])
            actions.append(f"circuit-breaker blocked dispatch for {ticket['id']} ({ticket['project']})")
            logger.warning(
                "Circuit breaker skipped ticket %s (%s) — tags: %s, priority: %s",
                ticket["id"],
                ticket["project"],
                tags,
                ticket.get("priority", "normal"),
            )
            continue

        # Priority inversion guard: when this dispatch would fill the last slot,
        # reject lower-priority tickets if eligible higher-priority work is pending
        remaining_slots = slots - dispatched_count
        if remaining_slots <= 1 and backlog.has_eligible_higher_priority(ticket.get("priority", "normal")):
            actions.append(f"priority_inversion_prevented: {ticket['id']} ({ticket.get('priority', 'normal')}) — higher-priority tickets pending")
            continue

        # Healer circuit breaker: disable healer for projects in spiral
        if healer_circuit_breaker.is_healer_blocked(ticket["project"]):
            if "--no-heal" not in ticket.get("flags", []):
                ticket.setdefault("flags", []).append("--no-heal")
            actions.append(f"healer-circuit-breaker: {ticket['id']} dispatched with --no-heal ({ticket['project']})")

        # Dispatch via CLI — filter to known CLI flags only
        valid_flags = {"--no-merge", "--plan", "--no-plan", "--deploy-only", "--validate-only", "--force-deploy", "--no-heal"}
        cmd = [settings.dispatch_bin, ticket["task"], "--project", ticket["project"]]
        # Pass task type if set
        task_type = ticket.get("task_type", "code")
        if task_type != "code":
            cmd.extend(["--type", task_type])
        cmd.extend(f for f in ticket.get("flags", []) if f in valid_flags)

        from foreman import _dispatch_async
        result = _dispatch_async(cmd, ticket["id"])
        if result["status"] == "ok":
            dispatched_count += 1
            actions.append(f"auto-dispatched {ticket['id']} (async)")
            # Add to dedup set so subsequent candidates are checked
            if task_prefix:
                inflight_prefixes[task_prefix] = ticket["id"]
        else:
            actions.append(f"dispatch failed for {ticket['id']}: {result.get('detail', 'unknown')}")

    return actions
