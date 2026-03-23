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
import subprocess
import time

import artifacts
import backlog
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

    # Run GC immediately on startup to catch zombies from before restart
    try:
        startup_actions = _gc_zombie_sessions()
        if startup_actions:
            logger.info("Startup GC: %s", startup_actions)
    except Exception:
        logger.exception("Startup GC error")

    while True:
        try:
            actions = _beat()
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

    # 1. Garbage-collect zombie sessions (running state, no active worker)
    actions.extend(_gc_zombie_sessions())

    # 2. Reconcile backlog with completed sessions (includes abandoned)
    actions.extend(_reconcile_backlog())

    # 3. Check for stuck workers
    actions.extend(_check_stuck_workers())

    # 3. Run operator (LLM reasoning with rotating lens)
    if _state.get("auto_dispatch_enabled", False):
        try:
            import factory_operator
            result = factory_operator.run_operator()
            if result.get("actions"):
                actions.append(f"operator[{result.get('lens', '?')}]: {len(result['actions'])} actions")
            elif result.get("assessment"):
                actions.append(f"operator[{result.get('lens', '?')}]: {result['assessment'][:80]}")
        except Exception as e:
            actions.append(f"operator error: {e}")

    return actions


def _reconcile_backlog() -> list[str]:
    """Check dispatched tickets — mark completed/failed based on session state."""
    actions = []
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
        if state in ("deployed", "completed"):
            backlog.mark_completed(ticket["id"], "completed")
            actions.append(f"ticket {ticket['id']} completed ({session_id})")
        elif state == "error":
            backlog.mark_completed(ticket["id"], "failed")
            actions.append(f"ticket {ticket['id']} failed ({session_id})")
        elif state == "rolled_back":
            backlog.mark_completed(ticket["id"], "failed")
            actions.append(f"ticket {ticket['id']} rolled back ({session_id})")
        elif state == "abandoned":
            backlog.mark_completed(ticket["id"], "failed")
            actions.append(f"ticket {ticket['id']} abandoned ({session_id})")

    return actions


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
        if session["state"] != "running":
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

        # Mark as abandoned
        if artifacts.abandon_session(sid, reason=f"no active worker, idle {int(age_minutes)}min"):
            actions.append(f"gc: abandoned {sid} (idle {int(age_minutes)}min)")
            logger.info("GC abandoned zombie session %s (idle %dmin)", sid, int(age_minutes))

    return actions


def _auto_dispatch() -> list[str]:
    """Auto-dispatch pending tickets if worker capacity is available."""
    actions = []
    active = artifacts.get_active_sessions()
    max_concurrent = _state.get("max_concurrent", 3)

    if len(active) >= max_concurrent:
        return actions

    slots = max_concurrent - len(active)
    for _ in range(slots):
        ticket = backlog.next_pending()
        if not ticket:
            break

        # Dispatch via CLI
        cmd = [settings.dispatch_bin, ticket["task"], "--project", ticket["project"]]
        cmd.extend(ticket.get("flags", []))

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode == 0:
                # Extract session ID from stdout
                import re
                match = re.search(r"session\s*:\s*([\w-]+)", result.stdout)
                session_id = match.group(1) if match else "unknown"
                backlog.mark_dispatched(ticket["id"], session_id)
                actions.append(f"auto-dispatched {ticket['id']} → {session_id}")
            else:
                actions.append(f"dispatch failed for {ticket['id']}: {result.stderr[:100]}")
        except subprocess.TimeoutExpired:
            actions.append(f"dispatch timeout for {ticket['id']}")
        except FileNotFoundError:
            actions.append("dispatch binary not found")
            break

    return actions
