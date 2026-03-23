"""FastAPI application for dispatch-factory control plane."""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import artifacts
import backlog
import circuit_breaker
import heartbeat
import intake
import factory_operator
import pipeline
import terminal
from config import settings

logger = logging.getLogger("dispatch-factory")

# ---------------------------------------------------------------------------
# Validation patterns
# ---------------------------------------------------------------------------

SESSION_ID_RE = re.compile(r"^(?:worker|deploy|validate)-[a-z][a-z0-9-]*-\d+$")
PROJECT_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")
ALLOWED_FLAGS = frozenset(["--no-merge", "--plan", "--no-plan", "--deploy-only", "--validate-only"])

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

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("dispatch-factory starting")
    logger.info("  artifacts_dir: %s", settings.artifacts_dir)
    logger.info("  dispatch_bin:  %s", settings.dispatch_bin)
    logger.info("  controls:      %s", settings.enable_controls)
    logger.info("  terminal:      %s", settings.terminal.enabled)

    dispatch_path = Path(settings.dispatch_bin)
    if not dispatch_path.is_file():
        logger.warning("dispatch binary not found at %s", dispatch_path)

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
    return {"status": "ok"}


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
    _require_controls()

    # Validate project name.
    if not PROJECT_NAME_RE.match(req.project):
        raise HTTPException(status_code=400, detail="Invalid project name")

    # Validate task.
    task = req.task.strip()
    if not task or len(task) > 500:
        raise HTTPException(status_code=400, detail="Task must be 1-500 characters")
    _validate_task_quality(task)

    # Validate flags against allowlist.
    for flag in req.flags:
        if flag not in ALLOWED_FLAGS:
            raise HTTPException(status_code=400, detail=f"Flag not allowed: {flag}")

    cmd: list[str] = [settings.dispatch_bin, task, "--project", req.project]
    cmd.extend(req.flags)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="dispatch binary not found")
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="dispatch timed out")

    return {
        "status": "dispatched" if result.returncode == 0 else "error",
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


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
    valid_statuses = ("intake", "needs_input", "ready", "pending")
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


@app.post("/api/backlog/{ticket_id}/dispatch")
async def dispatch_backlog_ticket(ticket_id: str) -> dict:
    """Dispatch a pending backlog ticket immediately."""
    _require_controls()

    tickets = backlog.list_tickets()
    ticket = next((t for t in tickets if t["id"] == ticket_id), None)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if ticket["status"] not in ("pending", "ready"):
        raise HTTPException(status_code=400, detail=f"Ticket is {ticket['status']}, must be pending or ready")

    # Task quality gate: reject vague tasks before dispatch
    _validate_task_quality(ticket["task"].strip())

    # Pre-dispatch guard: reject if project already has an in-flight ticket
    if backlog.has_inflight_ticket(ticket["project"]):
        raise HTTPException(
            status_code=409,
            detail=f"Project {ticket['project']} already has an in-flight ticket",
        )

    # Circuit breaker: block dispatches to tripped projects
    if circuit_breaker.is_project_blocked(ticket["project"]):
        raise HTTPException(
            status_code=409,
            detail=f"Circuit breaker tripped for {ticket['project']} — fix deploy pipeline first",
        )

    # Priority inversion guard: block lower-priority dispatch when eligible
    # higher-priority tickets are pending and capacity is at max
    active = artifacts.get_active_sessions()
    max_concurrent = heartbeat._state.get("max_concurrent", 3)
    if len(active) >= max_concurrent - 1 and backlog.has_eligible_higher_priority(ticket.get("priority", "normal")):
        raise HTTPException(
            status_code=409,
            detail="Higher-priority tickets are pending; dispatch those first",
        )

    cmd: list[str] = [settings.dispatch_bin, ticket["task"], "--project", ticket["project"]]
    cmd.extend(ticket.get("flags", []))

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="dispatch binary not found")
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="dispatch timed out")

    if result.returncode == 0:
        match = re.search(r"session\s*:\s*([\w-]+)", result.stdout)
        session_id = match.group(1) if match else "unknown"
        backlog.mark_dispatched(ticket_id, session_id)
        return {"status": "dispatched", "session_id": session_id, "stdout": result.stdout}

    return {"status": "error", "stderr": result.stderr}


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
    from config import update_heartbeat_config
    update_heartbeat_config(auto_dispatch=enabled, max_concurrent=max_concurrent)
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
# Project health
# ---------------------------------------------------------------------------


@app.get("/api/project-health")
async def project_health_dashboard() -> list[dict]:
    """Per-project health metrics: deploys, failures, activity, open PRs."""
    import project_health as _ph

    return _ph.get_project_health()


# ---------------------------------------------------------------------------
# Operator — LLM-driven factory management with rotating lenses
# ---------------------------------------------------------------------------

@app.get("/api/operator/lenses")
async def list_operator_lenses() -> list[dict]:
    return factory_operator.list_lenses()


@app.get("/api/operator/lenses/{lens_id}")
async def get_operator_lens(lens_id: str) -> dict:
    lens = factory_operator.get_lens(lens_id)
    if lens is None:
        raise HTTPException(status_code=404, detail="Lens not found")
    return lens


@app.put("/api/operator/lenses/{lens_id}")
async def save_operator_lens(lens_id: str, body: dict) -> dict[str, str]:
    _require_controls()
    prompt = body.get("prompt", "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt cannot be empty")
    factory_operator.save_lens(lens_id, prompt)
    return {"status": "saved"}


@app.delete("/api/operator/lenses/{lens_id}")
async def delete_operator_lens(lens_id: str) -> dict[str, str]:
    _require_controls()
    if not factory_operator.delete_lens(lens_id):
        raise HTTPException(status_code=404, detail="Lens not found")
    return {"status": "deleted"}


@app.get("/api/operator/rotation")
async def operator_rotation() -> dict:
    return factory_operator.get_rotation_state()


@app.post("/api/operator/run")
async def run_operator_now(lens_id: str | None = None) -> dict:
    """Manually trigger an operator cycle with a specific or next-in-rotation lens."""
    _require_controls()
    return factory_operator.run_operator(lens_id=lens_id)


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


@app.get("/api/pipeline/stages/{stage_id}")
async def get_pipeline_stage(stage_id: str) -> dict:
    stage = pipeline.get_stage(stage_id)
    if stage is None:
        raise HTTPException(status_code=404, detail="Stage not found")
    return stage


# ---------------------------------------------------------------------------
# Static files — serve frontend build in production
# ---------------------------------------------------------------------------

_frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if _frontend_dist.is_dir():
    app.mount("/", StaticFiles(directory=str(_frontend_dist), html=True), name="frontend")
