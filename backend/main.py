"""FastAPI application for dispatch-factory control plane."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import archived_projects
import artifacts
import cleared_healed_sessions
import backlog
import empty_backlog_detector
import paused_projects
import circuit_breaker
import healer_circuit_breaker
import heartbeat
import meta_work_ratio
import intake
import factory_idle_mode
import foreman
import pipeline
import review_policy
import reviewer_calibration
import terminal
from config import settings

logger = logging.getLogger("dispatch-factory")

# ---------------------------------------------------------------------------
# Validation patterns
# ---------------------------------------------------------------------------

SESSION_ID_RE = re.compile(r"^(?:worker|deploy|validate)-[a-z][a-z0-9-]*-\d+$")
SESSION_ID_EXTERNAL_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9._-]{2,80}$")
PROJECT_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")
ALLOWED_FLAGS = frozenset(["--no-merge", "--plan", "--no-plan", "--deploy-only", "--validate-only", "--no-heal", "--force-deploy"])

# Task quality gate — reject vague or underspecified tasks.
# Vague tasks correlate with deploy failures (sessions 1822, 1824, 1609).
TASK_MIN_LENGTH = 20
VAGUE_TASK_PATTERNS = re.compile(
    r"^("
    r"test|testing|try this|check|fix|fix it|update|updates|do it"
    r"|what are next steps\??"
    r"|next steps\??"
    r"|todo|tbd|placeholder|asdf|hello"
    r"|make it work|do something|finish this"
    r"|needs work|investigate|look into"
    r")$",
    re.IGNORECASE,
)


def _validate_task_quality(task: str) -> None:
    """Reject tasks that are too short or vague to be actionable.

    A dispatchable task must be at least 20 characters and describe
    a concrete deliverable — not a generic verb or question.
    """
    if len(task) < TASK_MIN_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Task too short ({len(task)} chars). "
            f"Minimum {TASK_MIN_LENGTH} characters — describe a concrete deliverable.",
        )
    if VAGUE_TASK_PATTERNS.match(task.strip()):
        raise HTTPException(
            status_code=400,
            detail="Task is too vague to dispatch. "
            "Describe a concrete deliverable (e.g. 'Add retry logic to payment webhook handler').",
        )


def _validate_session_id(session_id: str) -> None:
    if not SESSION_ID_RE.match(session_id):
        raise HTTPException(status_code=400, detail="Invalid session ID format")


def _require_controls() -> None:
    if not settings.enable_controls:
        raise HTTPException(status_code=403, detail="Control endpoints are disabled")


def _require_terminal() -> None:
    if not settings.terminal.enabled:
        raise HTTPException(status_code=403, detail="Terminal endpoints are disabled")


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class TicketRequest(BaseModel):
    task: str
    project: str
    flags: list[str] = []


class DispatchRequest(BaseModel):
    """Unified dispatch — create ticket + dispatch, or dispatch existing ticket."""
    task: str | None = None
    project: str | None = None
    ticket_id: str | None = None
    priority: str = "normal"
    task_type: str = "code"
    flags: list[str] = []
    source: str = "interactive"


# ---------------------------------------------------------------------------
# WebSocket file-watcher
# ---------------------------------------------------------------------------

_ws_clients: set[WebSocket] = set()


async def _watch_artifacts() -> None:
    """Watch the artifacts directory and push change notifications to WebSocket clients."""
    from watchfiles import awatch

    artifacts_dir = Path(settings.artifacts_dir)
    if not artifacts_dir.is_dir():
        logger.warning("Artifacts dir does not exist: %s — watcher not started", artifacts_dir)
        return

    try:
        async for changes in awatch(artifacts_dir):
            if not _ws_clients:
                continue
            # Send a lightweight notification — clients refetch what they need.
            changed_files = [str(Path(c[1]).name) for c in changes]
            payload = {"type": "artifacts_changed", "files": changed_files}
            dead: list[WebSocket] = []
            for ws in _ws_clients.copy():
                try:
                    await ws.send_json(payload)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                _ws_clients.discard(ws)
    except asyncio.CancelledError:
        return
    except Exception:
        logger.exception("File watcher crashed")


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

def _read_git_commit() -> str:
    """Read the short git commit hash at startup, or 'unknown' on failure."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


_git_commit: str = _read_git_commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("dispatch-factory starting")
    logger.info("  artifacts_dir: %s", settings.artifacts_dir)
    logger.info("  dispatch_bin:  %s", settings.dispatch_bin)
    logger.info("  controls:      %s", settings.enable_controls)
    logger.info("  terminal:      %s", settings.terminal.enabled)
    logger.info("  git_commit:    %s", _git_commit)

    dispatch_path = Path(settings.dispatch_bin)
    if not dispatch_path.is_file():
        logger.warning("dispatch binary not found at %s", dispatch_path)

    # Initialize SQLite database (migrates from JSON on first run)
    import db
    db.init_db()
    logger.info("  database:      %s", db._get_db_path())

    watcher_task = asyncio.create_task(_watch_artifacts())
    heartbeat_task = asyncio.create_task(heartbeat.heartbeat_loop(interval=30))
    yield
    # Shutdown
    watcher_task.cancel()
    heartbeat_task.cancel()
    for task in [watcher_task, heartbeat_task]:
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="dispatch-factory", lifespan=lifespan)

# CORS — allow localhost dev servers only.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8420",
        "http://127.0.0.1:8420",
        "http://factory.localhost",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "git_commit": _git_commit}


@app.get("/api/projects")
async def list_projects() -> list[str]:
    """List known projects from dispatch --projects or artifact scan."""
    return artifacts.get_known_projects()


# ---------------------------------------------------------------------------
# Read endpoints (always available)
# ---------------------------------------------------------------------------

@app.get("/api/sessions")
async def list_sessions() -> list[dict]:
    all_sessions = artifacts.list_sessions()
    active_ids = {s["id"] for s in artifacts.get_active_sessions()}
    # Only return sessions with a live tmux process
    return [s for s in all_sessions if s["id"] in active_ids]


@app.get("/api/sessions/history")
async def session_history(limit: int = 50) -> list[dict]:
    """All sessions with full artifact summaries, sorted by recency."""
    return artifacts.list_sessions_with_timestamps()[:limit]


@app.get("/api/sessions/active")
async def active_sessions() -> list[dict]:
    return artifacts.get_active_sessions()


@app.get("/api/brief")
async def brief() -> dict[str, Any]:
    """Autopilot brief + factory summary stats."""
    return artifacts.get_brief()


@app.get("/api/log")
async def factory_log(limit: int = 100) -> list[dict]:
    """Timeline of factory events across all sessions."""
    return artifacts.get_factory_log(limit=limit)


@app.get("/api/activity")
async def activity_feed(limit: int = 100) -> list[dict]:
    """Unified activity feed: session events + ticket lifecycle events."""
    return artifacts.get_activity_feed(limit=limit)


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str) -> dict[str, Any]:
    _validate_session_id(session_id)
    data = artifacts.get_session(session_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return data


@app.get("/api/state")
async def autopilot_state() -> dict[str, Any]:
    state = artifacts.get_autopilot_state()
    if state is None:
        return {"status": "no_state_file"}
    return state


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

@app.websocket("/api/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    _ws_clients.add(ws)
    try:
        while True:
            # Keep connection alive; client can send pings.
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _ws_clients.discard(ws)


# ---------------------------------------------------------------------------
# Control endpoints (gated)
# ---------------------------------------------------------------------------

@app.post("/api/tickets")
async def create_ticket(req: TicketRequest) -> dict[str, str]:
    """Legacy direct dispatch — creates ticket and dispatches via unified path."""
    _require_controls()

    if not PROJECT_NAME_RE.match(req.project):
        raise HTTPException(status_code=400, detail="Invalid project name")
    task = req.task.strip()
    if not task or len(task) > 500:
        raise HTTPException(status_code=400, detail="Task must be 1-500 characters")
    _validate_task_quality(task)
    for flag in req.flags:
        if flag not in ALLOWED_FLAGS:
            raise HTTPException(status_code=400, detail=f"Flag not allowed: {flag}")

    ticket = backlog.create_ticket(task=task, project=req.project, flags=req.flags, source="ui-legacy")
    _run_dispatch_guards(ticket)
    cmd = _build_dispatch_cmd(ticket)
    result = foreman._dispatch_async(cmd, ticket["id"])

    return {
        "status": result["status"],
        "ticket_id": ticket["id"],
        "detail": result.get("detail", ""),
    }


@app.get("/api/sessions/{session_id}/output")
async def get_session_output(session_id: str, lines: int = 20) -> dict:
    """Return last N lines of live tmux output for a running session."""
    _validate_session_id(session_id)
    try:
        result = subprocess.run(
            ["tmux", "capture-pane", "-t", session_id, "-p", "-S", str(-lines)],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            # Strip ANSI escape codes for clean display
            import re
            clean = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', result.stdout)
            # Remove empty trailing lines
            output_lines = clean.rstrip().split("\n")
            return {"session_id": session_id, "lines": output_lines, "alive": True}
        return {"session_id": session_id, "lines": [], "alive": False}
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {"session_id": session_id, "lines": [], "alive": False}


@app.post("/api/sessions/{session_id}/hold")
async def hold_session(session_id: str) -> dict[str, str]:
    _require_controls()
    _validate_session_id(session_id)

    try:
        result = subprocess.run(
            [settings.dispatch_bin, "--hold", session_id],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="dispatch binary not found")
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Timed out")

    return {"status": "held" if result.returncode == 0 else "error", "output": result.stdout}


@app.post("/api/sessions/{session_id}/kill")
async def kill_session(session_id: str) -> dict[str, str]:
    _require_controls()
    _validate_session_id(session_id)

    try:
        result = subprocess.run(
            ["tmux", "kill-session", "-t", session_id],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="tmux not found")
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Timed out")

    return {"status": "killed" if result.returncode == 0 else "error", "output": result.stderr}


# ---------------------------------------------------------------------------
# Terminal endpoints (gated)
# ---------------------------------------------------------------------------

@app.post("/api/terminal/{session_name}/attach")
async def attach_terminal(session_name: str) -> dict[str, Any]:
    _require_terminal()
    _validate_session_id(session_name)

    port = terminal.start_ttyd(session_name)
    if port is None:
        raise HTTPException(status_code=503, detail="No available ports or ttyd failed to start")
    return {"port": port, "session": session_name}


@app.post("/api/terminal/{session_name}/detach")
async def detach_terminal(session_name: str) -> dict[str, str]:
    _require_terminal()
    _validate_session_id(session_name)

    stopped = terminal.stop_ttyd(session_name)
    return {"status": "stopped" if stopped else "not_running"}


@app.get("/api/terminal")
async def list_terminals() -> dict[str, int]:
    _require_terminal()
    return terminal.list_ttyd()


# ---------------------------------------------------------------------------
# Backlog endpoints (gated)
# ---------------------------------------------------------------------------

class BacklogTicketRequest(BaseModel):
    task: str
    project: str
    priority: str = "normal"
    flags: list[str] = []
    status: str = "pending"


@app.get("/api/backlog")
async def list_backlog(status: str | None = None) -> list[dict]:
    return backlog.list_tickets(status=status)


@app.post("/api/backlog")
async def create_backlog_ticket(req: BacklogTicketRequest) -> dict:
    _require_controls()

    if not PROJECT_NAME_RE.match(req.project):
        raise HTTPException(status_code=400, detail="Invalid project name")
    task = req.task.strip()
    if not task or len(task) > 500:
        raise HTTPException(status_code=400, detail="Task must be 1-500 characters")
    _validate_task_quality(task)
    for flag in req.flags:
        if flag not in ALLOWED_FLAGS:
            raise HTTPException(status_code=400, detail=f"Flag not allowed: {flag}")
    if req.priority not in ("low", "normal", "high", "urgent"):
        raise HTTPException(status_code=400, detail="Priority must be low/normal/high/urgent")
    valid_statuses = ("intake", "needs_input", "on_hold", "ready", "pending")
    if req.status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Status must be one of: {valid_statuses}")

    ticket = backlog.create_ticket(
        task=task,
        project=req.project,
        priority=req.priority,
        flags=req.flags,
        source="manual",
    )
    # Override default status if specified
    if req.status != "pending":
        backlog.update_ticket(ticket["id"], {"status": req.status})
        ticket["status"] = req.status
    return ticket


@app.patch("/api/backlog/{ticket_id}")
async def update_backlog_ticket(ticket_id: str, updates: dict) -> dict:
    _require_controls()
    result = backlog.update_ticket(ticket_id, updates)
    if result is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return result


@app.delete("/api/backlog/{ticket_id}")
async def delete_backlog_ticket(ticket_id: str) -> dict[str, str]:
    _require_controls()
    deleted = backlog.delete_ticket(ticket_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return {"status": "deleted"}


@app.post("/api/backlog/{ticket_id}/note")
async def add_ticket_note(ticket_id: str, body: dict) -> dict:
    """Add a note to a backlog ticket. Optionally change status."""
    _require_controls()
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Note text is required")
    author = body.get("author", "human")
    new_status = body.get("status")  # optional: move ticket after noting

    ticket = backlog.add_note(ticket_id, text, author)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if new_status:
        ticket = backlog.update_ticket(ticket_id, {"status": new_status})
    return ticket


@app.get("/api/backlog/{ticket_id}/thread")
async def get_ticket_thread(ticket_id: str) -> list[dict]:
    """Return unified timeline of ticket events + session artifacts."""
    tickets = backlog.list_tickets()
    ticket = next((t for t in tickets if t["id"] == ticket_id), None)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    events: list[dict] = []

    # Ticket lifecycle events
    if ticket.get("created_at"):
        events.append({
            "type": "created",
            "timestamp": ticket["created_at"],
            "data": {"task": ticket["task"], "project": ticket["project"], "priority": ticket["priority"]},
        })

    for note in ticket.get("notes", []):
        events.append({
            "type": "note",
            "timestamp": note["timestamp"],
            "data": note,
        })

    if ticket.get("dispatched_at"):
        events.append({
            "type": "dispatched",
            "timestamp": ticket["dispatched_at"],
            "data": {"session_id": ticket.get("session_id")},
        })

    if ticket.get("completed_at"):
        events.append({
            "type": "completed",
            "timestamp": ticket["completed_at"],
            "data": {"status": ticket.get("status")},
        })

    # Session artifacts (if ticket was dispatched)
    if ticket.get("session_id"):
        session_events = artifacts.get_session_timeline(ticket["session_id"])
        events.extend(session_events)

    events.sort(key=lambda e: e["timestamp"])
    return events


@app.post("/api/backlog/{ticket_id}/dispatch")
async def dispatch_backlog_ticket(ticket_id: str) -> dict:
    """Dispatch a pending backlog ticket immediately. Delegates to unified dispatch."""
    _require_controls()

    tickets = backlog.list_tickets()
    ticket = next((t for t in tickets if t["id"] == ticket_id), None)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    _run_dispatch_guards(ticket)

    cmd = _build_dispatch_cmd(ticket)
    result = foreman._dispatch_async(cmd, ticket_id)

    return {
        "status": result["status"],
        "ticket_id": ticket_id,
        "detail": result.get("detail", ""),
        "source": "ui",
    }


# ---------------------------------------------------------------------------
# Unified dispatch
# ---------------------------------------------------------------------------

def _run_dispatch_guards(ticket: dict) -> None:
    """Shared pre-dispatch guards. Raises HTTPException on any block."""
    if ticket["status"] not in ("pending", "ready", "intake", "needs_input"):
        raise HTTPException(status_code=400, detail=f"Ticket is {ticket['status']}, must be pending/ready")

    if factory_idle_mode.is_idle():
        raise HTTPException(
            status_code=409,
            detail="Factory idle mode: all active projects need human input. Provide direction first.",
        )

    # Reviewer calibration gate DISABLED (2026-03-28): the LLM reviewer stage
    # was removed when the runner slimmed from 1750→46 lines. Pipeline now does
    # validate (tests) → merge with no LLM review step. The calibration canary
    # tests a prompt for a reviewer that no longer runs, so failures are false
    # positives that block all dispatch. Re-enable when LLM reviewer stage is
    # added to pipeline_runner.py.
    #
    # cal_state = reviewer_calibration.get_calibration_state()
    # if cal_state.get("consecutive_failures", 0) > 0:
    #     raise HTTPException(
    #         status_code=409,
    #         detail="Reviewer miscalibrated — run /api/reviewer-calibration to re-test.",
    #     )

    _validate_task_quality(ticket["task"].strip())

    if backlog.has_inflight_ticket(ticket["project"]):
        raise HTTPException(
            status_code=409,
            detail=f"Project {ticket['project']} already has an in-flight ticket",
        )

    if circuit_breaker.is_project_blocked(ticket["project"]):
        raise HTTPException(
            status_code=409,
            detail=f"Circuit breaker tripped for {ticket['project']}",
        )

    if ticket["project"] == "dispatch-factory" and meta_work_ratio.is_blocked(ticket.get("priority", "normal")):
        raise HTTPException(
            status_code=409,
            detail="Meta-work ratio exceeded — dispatch a product session first.",
        )

    active = artifacts.get_active_sessions()
    max_concurrent = heartbeat._state.get("max_concurrent", 3)
    if len(active) >= max_concurrent - 1 and backlog.has_eligible_higher_priority(ticket.get("priority", "normal")):
        raise HTTPException(
            status_code=409,
            detail="Higher-priority tickets are pending; dispatch those first",
        )


def _build_dispatch_cmd(ticket: dict) -> list[str]:
    """Build the dispatch CLI command from a ticket."""
    valid_flags = {"--no-merge", "--plan", "--no-plan", "--deploy-only", "--validate-only", "--force-deploy"}
    cmd = [settings.dispatch_bin, ticket["task"], "--project", ticket["project"]]
    task_type = ticket.get("task_type", "code")
    if task_type != "code":
        cmd.extend(["--type", task_type])
    cmd.extend(f for f in ticket.get("flags", []) if f in valid_flags)
    return cmd


@app.post("/api/dispatch")
async def unified_dispatch(req: DispatchRequest) -> dict:
    """Unified dispatch endpoint — any caller (UI, CLI, interactive, foreman)."""
    _require_controls()

    if req.ticket_id:
        tickets = backlog.list_tickets()
        ticket = next((t for t in tickets if t["id"] == req.ticket_id), None)
        if not ticket:
            raise HTTPException(status_code=404, detail="Ticket not found")
    elif req.task and req.project:
        if not PROJECT_NAME_RE.match(req.project):
            raise HTTPException(status_code=400, detail="Invalid project name")
        task = req.task.strip()
        if not task or len(task) > 500:
            raise HTTPException(status_code=400, detail="Task must be 1-500 characters")
        _validate_task_quality(task)
        for flag in req.flags:
            if flag not in ALLOWED_FLAGS:
                raise HTTPException(status_code=400, detail=f"Flag not allowed: {flag}")
        if req.priority not in ("low", "normal", "high", "urgent"):
            raise HTTPException(status_code=400, detail="Priority must be low/normal/high/urgent")

        ticket = backlog.create_ticket(
            task=task, project=req.project, priority=req.priority,
            flags=req.flags, source=req.source, task_type=req.task_type,
        )
    else:
        raise HTTPException(status_code=400, detail="Provide either ticket_id, or task + project")

    _run_dispatch_guards(ticket)
    cmd = _build_dispatch_cmd(ticket)
    result = foreman._dispatch_async(cmd, ticket["id"])

    return {
        "status": result["status"],
        "ticket_id": ticket["id"],
        "detail": result.get("detail", ""),
        "source": req.source,
    }


# ---------------------------------------------------------------------------
# Session registration (external workers)
# ---------------------------------------------------------------------------

@app.post("/api/sessions/{session_id}/register")
async def register_session(session_id: str, body: dict) -> dict:
    """Register an externally-spawned worker so the factory tracks it."""
    _require_controls()

    if not SESSION_ID_EXTERNAL_RE.match(session_id):
        raise HTTPException(status_code=400, detail="Invalid session ID format")

    project = body.get("project", "")
    if not project or not PROJECT_NAME_RE.match(project):
        raise HTTPException(status_code=400, detail="Valid project name required")

    task = body.get("task", "").strip()
    if not task:
        raise HTTPException(status_code=400, detail="Task description required")

    source = body.get("source", "external")
    session_type = body.get("type", "worker")

    import db
    with db.get_conn() as conn:
        existing = conn.execute("SELECT id, state FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if existing:
            return {"status": "already_registered", "session_id": session_id, "state": existing["state"]}

        alive = False
        try:
            result = subprocess.run(
                ["tmux", "has-session", "-t", session_id],
                capture_output=True, timeout=5,
            )
            alive = result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        db.upsert_session(
            conn, session_id,
            project=project, type=session_type, task=task[:200],
            state="running" if alive else "registered",
            has_log=0, mtime=time.time(), artifact_types="[]",
            summary=json.dumps({"source": source, "registered": True}),
        )

    existing_tickets = [t for t in backlog.list_tickets() if t.get("session_id") == session_id]
    ticket_id = None
    if not existing_tickets:
        ticket = backlog.create_ticket(task=task, project=project, source=source, status="dispatched")
        backlog.update_ticket(ticket["id"], {"session_id": session_id, "dispatched_at": time.time()})
        ticket_id = ticket["id"]
    else:
        ticket_id = existing_tickets[0]["id"]

    return {"status": "registered", "session_id": session_id, "ticket_id": ticket_id, "alive": alive}


# ---------------------------------------------------------------------------
# Heartbeat endpoints
# ---------------------------------------------------------------------------

@app.get("/api/heartbeat")
async def heartbeat_status() -> dict:
    return heartbeat.get_state()


@app.post("/api/heartbeat/auto-dispatch")
async def toggle_auto_dispatch(enabled: bool = True, max_concurrent: int = 3) -> dict:
    _require_controls()
    heartbeat._state["auto_dispatch_enabled"] = enabled
    heartbeat._state["max_concurrent"] = max_concurrent
    return {"auto_dispatch": enabled, "max_concurrent": max_concurrent}


@app.post("/api/sessions/gc")
async def run_session_gc() -> dict:
    """Manually trigger session garbage collection for zombie workers."""
    _require_controls()
    actions = heartbeat._gc_zombie_sessions()
    return {"actions": actions, "count": len(actions)}


@app.post("/api/sessions/{session_id}/abandon")
async def abandon_session(session_id: str) -> dict[str, str]:
    """Manually mark a session as abandoned."""
    _require_controls()
    _validate_session_id(session_id)
    session = artifacts.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if session["state"] == "abandoned":
        return {"status": "already_abandoned"}
    if not artifacts.abandon_session(session_id, reason="manually abandoned"):
        raise HTTPException(status_code=409, detail="Could not abandon session")
    return {"status": "abandoned"}


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------

@app.get("/api/circuit-breaker")
async def circuit_breaker_state() -> dict[str, dict]:
    """Return circuit breaker state for all projects."""
    return circuit_breaker.get_state()


@app.post("/api/circuit-breaker/{project}/reset")
async def reset_circuit_breaker(project: str) -> dict[str, str]:
    """Manually reset the circuit breaker for a project."""
    _require_controls()
    if not PROJECT_NAME_RE.match(project):
        raise HTTPException(status_code=400, detail="Invalid project name")
    if not circuit_breaker.reset_project(project):
        raise HTTPException(status_code=404, detail="Project not found in circuit breaker state")
    return {"status": "reset", "project": project}


# ---------------------------------------------------------------------------
# Healer circuit breaker
# ---------------------------------------------------------------------------


@app.get("/api/healer-circuit-breaker")
async def healer_circuit_breaker_state() -> dict[str, dict]:
    """Return healer circuit breaker state for all projects."""
    return healer_circuit_breaker.get_state()


@app.post("/api/healer-circuit-breaker/{project}/reset")
async def reset_healer_circuit_breaker(project: str) -> dict[str, str]:
    """Manually reset the healer circuit breaker for a project."""
    _require_controls()
    if not PROJECT_NAME_RE.match(project):
        raise HTTPException(status_code=400, detail="Invalid project name")
    if not healer_circuit_breaker.reset_project(project):
        raise HTTPException(status_code=404, detail="Project not found in healer circuit breaker state")
    return {"status": "reset", "project": project}


# ---------------------------------------------------------------------------
# Meta-work ratio
# ---------------------------------------------------------------------------


@app.get("/api/meta-work-ratio")
async def meta_work_ratio_state() -> dict:
    """Current meta-work ratio — how much of recent work is dispatch-factory."""
    return meta_work_ratio.get_ratio()


@app.get("/api/self-improvement")
async def self_improvement_state() -> dict:
    """Self-improvement ratio for the backlog UI — wraps meta-work-ratio with extra context."""
    ratio = meta_work_ratio.get_ratio()
    sessions = artifacts.list_sessions_with_timestamps()[:20]
    factory_sessions = [s for s in sessions if s["project"] == "dispatch-factory"]
    product_sessions = [s for s in sessions if s["project"] != "dispatch-factory"]

    # Count product dispatches since last factory dispatch
    product_since = 0
    for s in sessions:
        if s["project"] == "dispatch-factory":
            break
        product_since += 1

    last_factory_mtime = factory_sessions[0]["mtime"] if factory_sessions else None

    return {
        "product_dispatches_since_last_self_improvement": product_since,
        "total_product_dispatches": len(product_sessions),
        "total_self_improvement_dispatches": len(factory_sessions),
        "self_improvement_due": ratio["ratio"] < 0.1 and product_since >= 8,
        "last_self_improvement_at": last_factory_mtime,
        "last_updated": import_time(),
    }


def import_time() -> float:
    return __import__("time").time()


# ---------------------------------------------------------------------------
# Factory idle mode
# ---------------------------------------------------------------------------


@app.get("/api/factory-idle")
async def factory_idle_state() -> dict:
    """Factory idle mode state — whether all projects need human input."""
    return factory_idle_mode.get_state()


# ---------------------------------------------------------------------------
# Archived projects
# ---------------------------------------------------------------------------


@app.get("/api/archived-projects")
async def list_archived_projects() -> dict[str, dict]:
    """Return all archived projects with metadata."""
    return archived_projects.get_archived()


@app.post("/api/archived-projects/{project}")
async def archive_project(project: str, body: dict | None = None) -> dict[str, str]:
    """Archive a project — removes it from dispatch rotation and health checks."""
    _require_controls()
    if not PROJECT_NAME_RE.match(project):
        raise HTTPException(status_code=400, detail="Invalid project name")
    reason = (body or {}).get("reason", "")
    if not archived_projects.archive_project(project, reason=reason):
        raise HTTPException(status_code=409, detail="Project already archived")
    return {"status": "archived", "project": project}


@app.delete("/api/archived-projects/{project}")
async def unarchive_project(project: str) -> dict[str, str]:
    """Unarchive a project — restores it to dispatch rotation."""
    _require_controls()
    if not PROJECT_NAME_RE.match(project):
        raise HTTPException(status_code=400, detail="Invalid project name")
    if not archived_projects.unarchive_project(project):
        raise HTTPException(status_code=404, detail="Project not archived")
    return {"status": "unarchived", "project": project}


# ---------------------------------------------------------------------------
# Project health
# ---------------------------------------------------------------------------


@app.get("/api/project-health")
async def project_health_dashboard() -> list[dict]:
    """Per-project health metrics: deploys, failures, activity, open PRs."""
    import project_health as _ph

    return _ph.get_project_health()


# ---------------------------------------------------------------------------
# Healer effectiveness
# ---------------------------------------------------------------------------


@app.get("/api/healer-effectiveness")
async def healer_effectiveness() -> dict:
    """Healer effectiveness metrics: true success rate vs false confidence."""
    return artifacts.get_healer_effectiveness()


# ---------------------------------------------------------------------------
# Cleared healed sessions
# ---------------------------------------------------------------------------


@app.get("/api/cleared-healed-sessions")
async def list_cleared_healed_sessions() -> dict[str, dict]:
    """Return all cleared healed sessions with metadata."""
    return cleared_healed_sessions.get_cleared()


@app.post("/api/cleared-healed-sessions/{project}")
async def clear_project_healed_sessions(project: str, body: dict | None = None) -> dict:
    """Clear all healed-but-unverified sessions for a project.

    Finds healed+completed sessions for the project and marks them as
    reviewed/acknowledged so the healed_deploy_unverified alert stops firing.
    """
    _require_controls()
    if not PROJECT_NAME_RE.match(project):
        raise HTTPException(status_code=400, detail="Invalid project name")

    reason = (body or {}).get("reason", "manually cleared")

    # Find healed-unverified sessions for this project that aren't already cleared
    sessions = artifacts.list_sessions_with_timestamps()
    already_cleared = cleared_healed_sessions.get_cleared_ids()
    healed_unverified = [
        s for s in sessions
        if s["project"] == project
        and s.get("summary", {}).get("healed", False)
        and s["state"] == "completed"
        and s["id"] not in already_cleared
    ]
    session_ids = [s["id"] for s in healed_unverified]

    if not session_ids:
        return {"status": "no_sessions", "project": project, "cleared": 0}

    count = cleared_healed_sessions.clear_project_sessions(
        project, session_ids, reason=reason, source="api",
    )
    return {"status": "cleared", "project": project, "cleared": count, "session_ids": session_ids}


@app.post("/api/cleared-healed-sessions/_batch")
async def batch_clear_healed_sessions(body: dict | None = None) -> dict:
    """Clear all healed-but-unverified sessions across all projects at once.

    Useful for batch housekeeping — acknowledges all outstanding
    healed_deploy_unverified alerts in a single call.
    """
    _require_controls()
    reason = (body or {}).get("reason", "batch housekeeping")

    sessions = artifacts.list_sessions_with_timestamps()
    cleared_ids = cleared_healed_sessions.get_cleared_ids()

    # Find all healed-unverified sessions not yet cleared
    healed_unverified = [
        s for s in sessions
        if s.get("summary", {}).get("healed", False)
        and s["state"] == "completed"
        and s["id"] not in cleared_ids
    ]

    if not healed_unverified:
        return {"status": "no_sessions", "cleared": 0, "projects": {}}

    # Group by project and clear
    by_project: dict[str, list[str]] = {}
    for s in healed_unverified:
        by_project.setdefault(s["project"], []).append(s["id"])

    total = 0
    project_results: dict[str, int] = {}
    for project, sids in sorted(by_project.items()):
        count = cleared_healed_sessions.clear_project_sessions(
            project, sids, reason=reason, source="batch-api",
        )
        project_results[project] = count
        total += count

    return {"status": "cleared", "cleared": total, "projects": project_results}


# ---------------------------------------------------------------------------
# Paused projects
# ---------------------------------------------------------------------------


@app.get("/api/paused-projects")
async def list_paused_projects() -> dict[str, dict]:
    """Return all paused projects with metadata."""
    return paused_projects.get_paused()


@app.post("/api/paused-projects/{project}")
async def pause_project(project: str, body: dict | None = None) -> dict[str, str]:
    """Pause a project — suppresses neglect alerts while keeping it in health tracking."""
    _require_controls()
    if not PROJECT_NAME_RE.match(project):
        raise HTTPException(status_code=400, detail="Invalid project name")
    reason = (body or {}).get("reason", "")
    if not paused_projects.pause_project(project, reason=reason):
        raise HTTPException(status_code=409, detail="Project already paused")
    return {"status": "paused", "project": project}


@app.delete("/api/paused-projects/{project}")
async def unpause_project(project: str) -> dict[str, str]:
    """Unpause a project — restores neglect alert monitoring."""
    _require_controls()
    if not PROJECT_NAME_RE.match(project):
        raise HTTPException(status_code=400, detail="Invalid project name")
    if not paused_projects.unpause_project(project):
        raise HTTPException(status_code=404, detail="Project not paused")
    return {"status": "unpaused", "project": project}


# ---------------------------------------------------------------------------
# Empty backlog detection
# ---------------------------------------------------------------------------


@app.get("/api/empty-backlog")
async def empty_backlog_state() -> dict:
    """Projects with empty backlogs that need human direction."""
    return {
        "flagged": empty_backlog_detector.detect(),
        "flags": empty_backlog_detector.get_state(),
    }


@app.delete("/api/empty-backlog/{project}")
async def clear_empty_backlog_flag(project: str) -> dict[str, str]:
    """Clear the empty-backlog flag for a project."""
    _require_controls()
    if not PROJECT_NAME_RE.match(project):
        raise HTTPException(status_code=400, detail="Invalid project name")
    if not empty_backlog_detector.clear_flag(project):
        raise HTTPException(status_code=404, detail="No flag for this project")
    return {"status": "cleared", "project": project}


# ---------------------------------------------------------------------------
# Foreman — LLM-driven factory management with rotating lenses
# ---------------------------------------------------------------------------

@app.get("/api/foreman/lenses")
async def list_foreman_lenses() -> list[dict]:
    return foreman.list_lenses()


@app.get("/api/foreman/lenses/{lens_id}")
async def get_foreman_lens(lens_id: str) -> dict:
    lens = foreman.get_lens(lens_id)
    if lens is None:
        raise HTTPException(status_code=404, detail="Lens not found")
    return lens


@app.put("/api/foreman/lenses/{lens_id}")
async def save_foreman_lens(lens_id: str, body: dict) -> dict[str, str]:
    _require_controls()
    prompt = body.get("prompt", "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt cannot be empty")
    foreman.save_lens(lens_id, prompt)
    return {"status": "saved"}


@app.delete("/api/foreman/lenses/{lens_id}")
async def delete_foreman_lens(lens_id: str) -> dict[str, str]:
    _require_controls()
    if not foreman.delete_lens(lens_id):
        raise HTTPException(status_code=404, detail="Lens not found")
    return {"status": "deleted"}


@app.get("/api/foreman/rotation")
async def foreman_rotation() -> dict:
    return foreman.get_rotation_state()


@app.post("/api/foreman/run")
async def run_foreman_now(lens_id: str | None = None) -> dict:
    """Manually trigger a foreman cycle with a specific or next-in-rotation lens."""
    _require_controls()
    return foreman.run_foreman(lens_id=lens_id)


@app.get("/api/foreman/stream")
async def foreman_stream(after: int = 0) -> dict:
    """Poll for foreman thinking events (tool calls, intermediate text)."""
    events, next_line = foreman.get_stream_events(after_line=after)
    return {"events": events, "next": next_line}


@app.get("/api/foreman/decisions")
async def foreman_decisions(limit: int = 20) -> list[dict]:
    """Return recent foreman decisions — what was chosen and why."""
    log_path = Path(settings.artifacts_dir) / "foreman-decisions.jsonl"
    if not log_path.is_file():
        return []
    entries = []
    for line in log_path.read_text().strip().splitlines():
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries[-limit:]


@app.get("/api/foreman/prompts")
async def foreman_prompts(limit: int = 5) -> list[dict]:
    """Return recent foreman prompt/response pairs for inspection."""
    artifacts_dir = Path(settings.artifacts_dir)
    prompt_files = sorted(artifacts_dir.glob("foreman-*-prompt.md"), reverse=True)[:limit]
    results = []
    for pf in prompt_files:
        ts = pf.stem.split("-")[1]  # foreman-{ts}-prompt.md
        response_path = artifacts_dir / f"foreman-{ts}-response.json"
        stream_path = artifacts_dir / f"foreman-{ts}-stream.jsonl"
        entry = {
            "timestamp": int(ts),
            "prompt_path": str(pf),
            "prompt_size": pf.stat().st_size,
        }
        if response_path.is_file():
            try:
                entry["response"] = json.loads(response_path.read_text())
            except (json.JSONDecodeError, OSError):
                entry["response"] = None
        if stream_path.is_file():
            entry["stream_lines"] = len(stream_path.read_text().strip().splitlines())
        results.append(entry)
    return results


@app.get("/api/foreman/noticings")
async def foreman_noticings(limit: int = 20) -> list[dict]:
    """Return recent foreman noticings — half-formed observations."""
    return foreman.get_recent_noticings(limit=limit)


@app.get("/api/foreman/questions")
async def foreman_questions() -> list[dict]:
    """Return unanswered foreman questions awaiting human input."""
    return foreman.get_unanswered_questions()


@app.get("/api/foreman/threads")
async def list_foreman_threads() -> list[dict]:
    """Return all chat threads."""
    import db
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM foreman_threads ORDER BY last_message_at DESC").fetchall()
    return [dict(r) for r in rows]


@app.post("/api/foreman/threads")
async def create_foreman_thread(body: dict) -> dict:
    """Create a new chat thread."""
    import db
    import time
    import uuid
    title = (body.get("title") or "").strip() or f"Thread {time.strftime('%m/%d %H:%M')}"
    thread_id = uuid.uuid4().hex[:8]
    now = time.time()
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO foreman_threads (id, title, created_at, last_message_at) VALUES (?, ?, ?, ?)",
            (thread_id, title, now, now),
        )
    return {"id": thread_id, "title": title}


@app.get("/api/foreman/chat/history")
async def foreman_chat_history(thread_id: str = "default", limit: int = 50) -> list[dict]:
    """Return chat messages for a thread."""
    import db
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT role, text, actions, timestamp FROM foreman_chat WHERE thread_id = ? ORDER BY id DESC LIMIT ?",
            (thread_id, limit),
        ).fetchall()
    return [{"role": r["role"], "text": r["text"], "actions": json.loads(r["actions"]), "timestamp": r["timestamp"]} for r in reversed(rows)]


@app.post("/api/foreman/chat")
async def foreman_chat(body: dict) -> dict:
    """Chat with the foreman — run a foreman cycle with a human message injected."""
    _require_controls()
    message = (body.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message is required")
    thread_id = body.get("thread_id", "default")

    import db
    import time
    now = time.time()

    # Persist human message
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO foreman_chat (thread_id, role, text, actions, timestamp) VALUES (?, ?, ?, ?, ?)",
            (thread_id, "human", message, "[]", now),
        )

    # Run foreman in thread to avoid blocking other API requests
    import asyncio
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, lambda: foreman.run_foreman(human_message=message))

    # Persist foreman response
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO foreman_chat (thread_id, role, text, actions, timestamp) VALUES (?, ?, ?, ?, ?)",
            (thread_id, "foreman", result.get("assessment", ""), json.dumps(result.get("actions", [])), result.get("timestamp", now)),
        )
        # Update thread metadata
        conn.execute(
            """INSERT INTO foreman_threads (id, title, created_at, last_message_at, message_count)
               VALUES (?, ?, ?, ?, 2)
               ON CONFLICT(id) DO UPDATE SET
               last_message_at = excluded.last_message_at,
               message_count = message_count + 2""",
            (thread_id, message[:60], now, now),
        )

    return result


# ---------------------------------------------------------------------------
# Backward-compat aliases: /api/operator/* → /api/foreman/*
# ---------------------------------------------------------------------------

@app.get("/api/operator/lenses")
async def list_operator_lenses_compat() -> list[dict]:
    return foreman.list_lenses()


@app.get("/api/operator/lenses/{lens_id}")
async def get_operator_lens_compat(lens_id: str) -> dict:
    lens = foreman.get_lens(lens_id)
    if lens is None:
        raise HTTPException(status_code=404, detail="Lens not found")
    return lens


@app.put("/api/operator/lenses/{lens_id}")
async def save_operator_lens_compat(lens_id: str, body: dict) -> dict[str, str]:
    _require_controls()
    prompt = body.get("prompt", "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt cannot be empty")
    foreman.save_lens(lens_id, prompt)
    return {"status": "saved"}


@app.delete("/api/operator/lenses/{lens_id}")
async def delete_operator_lens_compat(lens_id: str) -> dict[str, str]:
    _require_controls()
    if not foreman.delete_lens(lens_id):
        raise HTTPException(status_code=404, detail="Lens not found")
    return {"status": "deleted"}


@app.get("/api/operator/rotation")
async def operator_rotation_compat() -> dict:
    return foreman.get_rotation_state()


@app.post("/api/operator/run")
async def run_operator_now_compat(lens_id: str | None = None) -> dict:
    """Backward-compat alias for /api/foreman/run."""
    _require_controls()
    return foreman.run_foreman(lens_id=lens_id)


# ---------------------------------------------------------------------------
# Intake — LLM-assisted ticket structuring
# ---------------------------------------------------------------------------

class IntakeRequest(BaseModel):
    input: str
    context: str = ""


@app.get("/api/intake/prompt")
async def get_intake_prompt() -> dict[str, str]:
    """Read the current intake system prompt."""
    return {"prompt": intake.PROMPT_FILE.read_text() if intake.PROMPT_FILE.is_file() else intake._DEFAULT_PROMPT}


@app.put("/api/intake/prompt")
async def set_intake_prompt(body: dict) -> dict[str, str]:
    """Update the intake system prompt."""
    _require_controls()
    text = body.get("prompt", "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Prompt cannot be empty")
    intake.PROMPT_FILE.write_text(text)
    return {"status": "saved"}


@app.post("/api/intake")
async def intake_structure(req: IntakeRequest) -> dict:
    """Send rough idea to LLM, get back structured ticket proposals."""
    _require_controls()
    text = req.input.strip()
    if not text or len(text) > 1000:
        raise HTTPException(status_code=400, detail="Input must be 1-1000 characters")
    return intake.structure_tickets(text, req.context)


# ---------------------------------------------------------------------------
# Pipeline definition
# ---------------------------------------------------------------------------

@app.get("/api/pipeline")
async def get_pipeline() -> dict:
    """Return the full pipeline definition."""
    return pipeline.get_pipeline()


@app.get("/api/pipeline/summary")
async def get_pipeline_summary() -> dict:
    """Return a compact pipeline summary for dashboard display."""
    return pipeline.get_pipeline_summary()


@app.get("/api/pipeline/stations/{station_id}")
async def get_pipeline_station(station_id: str) -> dict:
    station = pipeline.get_station(station_id)
    if station is None:
        raise HTTPException(status_code=404, detail="Station not found")
    return station


@app.get("/api/pipeline/stages/{stage_id}")
async def get_pipeline_stage_compat(stage_id: str) -> dict:
    """Backward-compat alias for /api/pipeline/stations/{station_id}."""
    station = pipeline.get_station(stage_id)
    if station is None:
        raise HTTPException(status_code=404, detail="Station not found")
    return station


@app.patch("/api/pipeline/stations/{station_id}")
async def update_pipeline_station(station_id: str, body: dict) -> dict:
    """Update mutable fields on a pipeline station."""
    _require_controls()
    result = pipeline.update_station(station_id, body)
    if result is None:
        raise HTTPException(status_code=404, detail="Station not found")
    if isinstance(result, list):
        raise HTTPException(status_code=400, detail="; ".join(result))
    return result


@app.patch("/api/pipeline/global")
async def update_pipeline_global(body: dict) -> dict:
    """Update global pipeline config."""
    _require_controls()
    result = pipeline.update_global(body)
    if isinstance(result, list):
        raise HTTPException(status_code=400, detail="; ".join(result))
    return result


@app.post("/api/pipeline/reset")
async def reset_pipeline() -> dict:
    """Reset pipeline to code defaults."""
    _require_controls()
    return pipeline.reset_pipeline()


# ---------------------------------------------------------------------------
# Review policy
# ---------------------------------------------------------------------------


@app.get("/api/review-policy")
async def get_review_policy() -> dict:
    """Return the active review policy (rejection criteria, healed-session rules)."""
    return review_policy.get_policy()


@app.get("/api/review-policy/prompt")
async def get_review_policy_prompt(is_healed: bool = False) -> dict[str, str]:
    """Return the policy addendum to inject into the reviewer prompt.

    The dispatch runner calls this before each review and appends the result
    to the reviewer's system prompt. Pass is_healed=true for healed sessions
    to activate extra scrutiny instructions.
    """
    return {"addendum": review_policy.get_reviewer_prompt_addendum(is_healed=is_healed)}


@app.get("/api/review-policy/stats")
async def get_review_stats() -> dict:
    """Reviewer verdict statistics — approval rate, healed-session blindness, etc."""
    return review_policy.get_reviewer_stats()


# ---------------------------------------------------------------------------
# Reviewer calibration — canary-based self-test
# ---------------------------------------------------------------------------


@app.get("/api/reviewer-calibration")
async def get_reviewer_calibration() -> dict:
    """Return the current reviewer calibration state (canary test results)."""
    return reviewer_calibration.get_calibration_state()


@app.get("/api/reviewer-calibration/canaries")
async def get_calibration_canaries() -> list[dict]:
    """Return the list of canary scenarios used for calibration."""
    return [
        {"id": c["id"], "name": c["name"], "task": c["task"], "expected_criterion": c["expected_criterion"]}
        for c in reviewer_calibration.CANARY_SCENARIOS
    ]


@app.get("/api/reviewer-calibration/diagnosis")
async def get_calibration_diagnosis() -> dict:
    """Diagnostic info about what calibration tests vs what the real reviewer does.

    Root cause of 100% APPROVE rate (2026-03-25): the dispatch binary's
    run_reviewer() never fetches /api/review-policy/prompt — it uses its own
    hardcoded prompt.  Prior calibration tested a simulated reviewer with the
    policy injected, which didn't match production.
    """
    state = reviewer_calibration.get_calibration_state()
    runs = state.get("runs", [])
    prompt_modes = {}
    for run in runs:
        mode = run.get("prompt_mode", "policy_simulated")
        prompt_modes[mode] = prompt_modes.get(mode, 0) + 1

    return {
        "root_cause": (
            "dispatch binary run_reviewer() never fetches /api/review-policy/prompt — "
            "uses hardcoded 'Be pragmatic' prompt. Prior calibration tested a simulated "
            "reviewer with the strict policy injected, not the real production prompt."
        ),
        "fix_applied": (
            "Calibration now uses the real dispatch reviewer prompt (matching "
            "run_reviewer() in the dispatch binary). Canaries that pass with the real "
            "prompt prove the reviewer works; failures prove it doesn't."
        ),
        "dispatch_binary_fetches_policy": False,
        "calibration_prompt_mode": "real_reviewer",
        "run_history_by_mode": prompt_modes,
        "recommendation": (
            "Update the dispatch binary to fetch /api/review-policy/prompt and inject "
            "the addendum into the reviewer prompt before each review."
        ),
    }


# ---------------------------------------------------------------------------
# Static files — serve frontend build in production
# ---------------------------------------------------------------------------

_frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if _frontend_dist.is_dir():
    app.mount("/", StaticFiles(directory=str(_frontend_dist), html=True), name="frontend")
