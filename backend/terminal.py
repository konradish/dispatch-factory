"""Manage ttyd processes for browser-based terminal embedding."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from config import settings


@dataclass
class TtydInstance:
    process: subprocess.Popen
    port: int
    session_name: str


# Module-level registry — ephemeral, lost on restart.
_instances: dict[str, TtydInstance] = {}


def _next_port() -> int | None:
    """Find the next available port in the configured range."""
    used = {inst.port for inst in _instances.values()}
    for port in range(settings.terminal.port_range_start, settings.terminal.port_range_end + 1):
        if port not in used:
            return port
    return None


def start_ttyd(session_name: str) -> int | None:
    """Spawn a ttyd process attached to a tmux session. Returns port or None on failure."""
    # Already running?
    if session_name in _instances:
        inst = _instances[session_name]
        if inst.process.poll() is None:
            return inst.port
        # Process died — clean up and re-spawn.
        del _instances[session_name]

    port = _next_port()
    if port is None:
        return None

    # Prefer tailing the log file — Claude Code's --print mode writes there,
    # not to the tmux pty. Falls back to tmux attach if no log exists.
    log_file = Path(settings.artifacts_dir) / f"{session_name}.log"

    cmd = [settings.terminal.ttyd_bin]
    if settings.terminal.read_only:
        cmd.append("-R")
    cmd.extend(["-p", str(port)])

    cmd.extend(["tmux", "attach", "-t", session_name])

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, OSError):
        return None

    _instances[session_name] = TtydInstance(process=proc, port=port, session_name=session_name)
    return port


def stop_ttyd(session_name: str) -> bool:
    """Kill the ttyd process for a session. Returns True if it was running."""
    inst = _instances.pop(session_name, None)
    if inst is None:
        return False
    if inst.process.poll() is None:
        inst.process.terminate()
        try:
            inst.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            inst.process.kill()
    return True


def list_ttyd() -> dict[str, int]:
    """Return {session_name: port} for all live ttyd instances."""
    # Prune dead processes.
    dead = [name for name, inst in _instances.items() if inst.process.poll() is not None]
    for name in dead:
        del _instances[name]
    return {name: inst.port for name, inst in _instances.items()}
