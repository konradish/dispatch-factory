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
import terminal
from config import settings

logger = logging.getLogger("dispatch-factory")

# ---------------------------------------------------------------------------
# Validation patterns
# ---------------------------------------------------------------------------

SESSION_ID_RE = re.compile(r"^(?:worker|deploy|validate)-[a-z][a-z0-9-]*-\d+$")
PROJECT_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")
ALLOWED_FLAGS = frozenset(["--no-merge", "--plan", "--no-plan", "--deploy-only", "--validate-only"])


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
    yield
    # Shutdown
    watcher_task.cancel()
    try:
        await watcher_task
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


# ---------------------------------------------------------------------------
# Read endpoints (always available)
# ---------------------------------------------------------------------------

@app.get("/api/sessions")
async def list_sessions() -> list[dict]:
    all_sessions = artifacts.list_sessions()
    active_ids = {s["id"] for s in artifacts.get_active_sessions()}
    # Only return sessions with a live tmux process
    return [s for s in all_sessions if s["id"] in active_ids]


@app.get("/api/sessions/active")
async def active_sessions() -> list[dict]:
    return artifacts.get_active_sessions()


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
            timeout=30,
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
# Static files — serve frontend build in production
# ---------------------------------------------------------------------------

_frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if _frontend_dist.is_dir():
    app.mount("/", StaticFiles(directory=str(_frontend_dist), html=True), name="frontend")
