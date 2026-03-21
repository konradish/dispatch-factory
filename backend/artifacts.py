"""Read dispatch artifacts from the filesystem."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from config import settings

# Artifact suffixes we care about, mapped to friendly names.
ARTIFACT_TYPES: dict[str, str] = {
    "-planner.json": "planner",
    "-reviewer.json": "reviewer",
    "-verifier.json": "verifier",
    "-monitor.json": "monitor",
    "-healer.json": "healer",
    "-fixer.json": "fixer",
    "-error.json": "error",
    "-result.md": "result",
    "-validate.json": "validate",
}

# Pattern that matches session prefixes in filenames.
# e.g. "worker-recipebrain-1430" or "deploy-electricapp-0847"
SESSION_RE = re.compile(r"^((?:worker|deploy|validate)-[a-z][a-z0-9-]*-\d+)")


def _artifacts_path() -> Path:
    return Path(settings.artifacts_dir)


def _read_json(path: Path) -> dict | None:
    """Read a JSON file, returning None on any error."""
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def _read_text(path: Path) -> str | None:
    """Read a text file, returning None on any error."""
    try:
        return path.read_text()
    except OSError:
        return None


def _detect_session_state(artifacts: dict[str, object]) -> str:
    """Derive a high-level state from whichever artifacts exist."""
    if "error" in artifacts:
        return "error"
    if "result" in artifacts:
        # Check verifier for deploy status
        verifier = artifacts.get("verifier")
        if isinstance(verifier, dict):
            status = verifier.get("status", "")
            if status == "DEPLOYED":
                return "deployed"
            if status == "ROLLBACK":
                return "rolled_back"
        return "completed"
    if "monitor" in artifacts:
        return "monitoring"
    if "verifier" in artifacts:
        return "verifying"
    if "reviewer" in artifacts:
        return "reviewing"
    if "planner" in artifacts:
        return "planning"
    # Has a log but no other artifacts — worker is running or just started
    return "running"


def list_sessions() -> list[dict]:
    """Scan artifacts directory and return a list of sessions with summary info."""
    artifacts_dir = _artifacts_path()
    if not artifacts_dir.is_dir():
        return []

    # Group files by session prefix.
    sessions: dict[str, dict] = {}
    for entry in artifacts_dir.iterdir():
        m = SESSION_RE.match(entry.name)
        if not m:
            continue
        session_id = m.group(1)
        if session_id not in sessions:
            sessions[session_id] = {"id": session_id, "artifacts": {}, "has_log": False}

        # Check for known artifact suffixes.
        suffix_part = entry.name[len(session_id):]
        for suffix, name in ARTIFACT_TYPES.items():
            if suffix_part == suffix:
                if name == "result":
                    sessions[session_id]["artifacts"][name] = True  # Don't inline full markdown
                else:
                    sessions[session_id]["artifacts"][name] = _read_json(entry)
                break

        if suffix_part == ".log":
            sessions[session_id]["has_log"] = True

    # Compute state for each session.
    result = []
    for sid in sorted(sessions, reverse=True):
        info = sessions[sid]
        info["state"] = _detect_session_state(info["artifacts"])
        # For the list view, strip bulky artifact bodies — keep just type keys.
        info["artifact_types"] = list(info["artifacts"].keys())
        del info["artifacts"]
        result.append(info)

    return result


def get_session(session_id: str) -> dict | None:
    """Return full detail for a single session, including artifact contents."""
    artifacts_dir = _artifacts_path()
    if not artifacts_dir.is_dir():
        return None

    prefix = session_id
    artifacts: dict[str, object] = {}
    has_log = False
    log_size = 0

    for entry in artifacts_dir.iterdir():
        if not entry.name.startswith(prefix):
            continue
        suffix_part = entry.name[len(prefix):]

        for suffix, name in ARTIFACT_TYPES.items():
            if suffix_part == suffix:
                if name == "result":
                    artifacts[name] = _read_text(entry)
                else:
                    artifacts[name] = _read_json(entry)
                break

        if suffix_part == ".log":
            has_log = True
            try:
                log_size = entry.stat().st_size
            except OSError:
                pass

    if not artifacts and not has_log:
        return None

    return {
        "id": session_id,
        "artifacts": artifacts,
        "has_log": has_log,
        "log_size": log_size,
        "state": _detect_session_state(artifacts),
    }


def get_active_sessions() -> list[dict]:
    """Parse tmux to find active worker/deploy sessions."""
    try:
        result = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []

    if result.returncode != 0:
        return []

    sessions = []
    for line in result.stdout.strip().splitlines():
        name = line.strip()
        if SESSION_RE.match(name):
            sessions.append({"id": name, "active": True})
    return sessions


def get_autopilot_state() -> dict | None:
    """Read autopilot-state.json if it exists."""
    path = _artifacts_path() / "autopilot-state.json"
    return _read_json(path)
