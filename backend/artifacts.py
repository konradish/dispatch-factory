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

# Extract project name from session ID: "worker-recipebrain-1430" → "recipebrain"
# Handles hyphens in project names: "worker-voice-bridge-0738" → "voice-bridge"
SESSION_PARTS_RE = re.compile(r"^(?:worker|deploy|validate)-(.+)-(\d+)$")


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


def _extract_task(artifacts_dir: Path, session_id: str) -> str:
    """Extract task description from the .prompt file."""
    prompt_file = artifacts_dir / f"{session_id}.prompt"
    text = _read_text(prompt_file)
    if not text:
        return ""
    # Task is between "## Task" and "## Project"
    start = text.find("## Task")
    if start == -1:
        return ""
    start = text.index("\n", start) + 1
    end = text.find("## Project", start)
    if end == -1:
        end = text.find("##", start)
    task = text[start:end].strip() if end != -1 else text[start:].strip()
    # Cap at 200 chars
    return task[:200]


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
            parts = SESSION_PARTS_RE.match(session_id)
            project = parts.group(1) if parts else "unknown"
            session_type = "deploy" if session_id.startswith("deploy-") else "validate" if session_id.startswith("validate-") else "worker"
            sessions[session_id] = {
                "id": session_id,
                "project": project,
                "type": session_type,
                "task": _extract_task(artifacts_dir, session_id),
                "artifacts": {},
                "has_log": False,
            }

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


SHELL_COMMANDS = frozenset({"zsh", "bash", "sh", "fish"})


def get_active_sessions() -> list[dict]:
    """Parse tmux to find dispatch sessions where a worker process is still running.

    Sessions that dropped back to a bare shell (zsh/bash) are considered dead
    and excluded. Only sessions running claude/cy/node/python/etc are reported.
    """
    try:
        result = subprocess.run(
            [
                "tmux", "list-panes", "-a",
                "-F", "#{session_name}\t#{pane_current_command}",
            ],
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
        parts = line.strip().split("\t", 1)
        if len(parts) != 2:
            continue
        name, cmd = parts
        if SESSION_RE.match(name) and cmd not in SHELL_COMMANDS:
            sessions.append({"id": name, "active": True, "command": cmd})
    return sessions


def get_autopilot_state() -> dict | None:
    """Read autopilot-state.json if it exists."""
    path = _artifacts_path() / "autopilot-state.json"
    return _read_json(path)


def list_sessions_with_timestamps() -> list[dict]:
    """Like list_sessions but includes timestamps and artifact summaries for history view."""
    artifacts_dir = _artifacts_path()
    if not artifacts_dir.is_dir():
        return []

    sessions: dict[str, dict] = {}
    for entry in artifacts_dir.iterdir():
        m = SESSION_RE.match(entry.name)
        if not m:
            continue
        session_id = m.group(1)
        if session_id not in sessions:
            parts = SESSION_PARTS_RE.match(session_id)
            project = parts.group(1) if parts else "unknown"
            session_type = "deploy" if session_id.startswith("deploy-") else "validate" if session_id.startswith("validate-") else "worker"
            sessions[session_id] = {
                "id": session_id,
                "project": project,
                "type": session_type,
                "task": _extract_task(artifacts_dir, session_id),
                "artifacts": {},
                "has_log": False,
                "mtime": 0.0,
            }

        suffix_part = entry.name[len(session_id):]

        # Track latest mtime across all files for this session
        try:
            mt = entry.stat().st_mtime
            if mt > sessions[session_id]["mtime"]:
                sessions[session_id]["mtime"] = mt
        except OSError:
            pass

        for suffix, name in ARTIFACT_TYPES.items():
            if suffix_part == suffix:
                data = _read_json(entry) if name != "result" else True
                sessions[session_id]["artifacts"][name] = data
                break

        if suffix_part == ".log":
            sessions[session_id]["has_log"] = True

    result = []
    for sid in sorted(sessions, key=lambda s: sessions[s]["mtime"], reverse=True):
        info = sessions[sid]
        info["state"] = _detect_session_state(info["artifacts"])

        # Extract key facts from artifacts for the history view
        reviewer = info["artifacts"].get("reviewer")
        verifier = info["artifacts"].get("verifier")
        healer = info["artifacts"].get("healer")

        info["summary"] = {
            "verdict": reviewer.get("verdict", "") if isinstance(reviewer, dict) else "",
            "feedback": (reviewer.get("feedback", "") if isinstance(reviewer, dict) else "")[:200],
            "deploy_status": verifier.get("status", "") if isinstance(verifier, dict) else "",
            "stages": verifier.get("stages", {}) if isinstance(verifier, dict) else {},
            "healed": healer is not None,
            "healer_action": healer.get("action", "") if isinstance(healer, dict) else "",
            "healer_diagnosis": (healer.get("diagnosis", "") if isinstance(healer, dict) else "")[:200],
        }

        info["artifact_types"] = list(info["artifacts"].keys())
        del info["artifacts"]
        result.append(info)

    return result


def get_brief() -> dict:
    """Build a brief: autopilot state + aggregate stats from recent sessions."""
    state = get_autopilot_state() or {}
    sessions = list_sessions_with_timestamps()

    # Aggregate stats
    total = len(sessions)
    deployed = sum(1 for s in sessions if s["state"] == "deployed")
    completed = sum(1 for s in sessions if s["state"] == "completed")
    failed = sum(1 for s in sessions if s["state"] in ("error", "rolled_back"))
    healed = sum(1 for s in sessions if s.get("summary", {}).get("healed", False))

    # Per-project breakdown
    projects: dict[str, dict] = {}
    for s in sessions:
        p = s["project"]
        if p not in projects:
            projects[p] = {"deployed": 0, "completed": 0, "failed": 0, "total": 0}
        projects[p]["total"] += 1
        if s["state"] == "deployed":
            projects[p]["deployed"] += 1
        elif s["state"] == "completed":
            projects[p]["completed"] += 1
        elif s["state"] in ("error", "rolled_back"):
            projects[p]["failed"] += 1

    # Direction vector
    direction_file = _artifacts_path() / "autopilot-direction.md"
    direction = _read_text(direction_file) or ""

    return {
        "autopilot": state,
        "direction": direction.strip(),
        "stats": {
            "total_sessions": total,
            "deployed": deployed,
            "completed": completed,
            "failed": failed,
            "healed": healed,
            "success_rate": round((deployed + completed) / total * 100, 1) if total > 0 else 0,
        },
        "projects": projects,
    }


def get_factory_log(limit: int = 100) -> list[dict]:
    """Build a timeline of factory events from artifact files."""
    artifacts_dir = _artifacts_path()
    if not artifacts_dir.is_dir():
        return []

    events: list[dict] = []

    for entry in artifacts_dir.iterdir():
        m = SESSION_RE.match(entry.name)
        if not m:
            continue
        session_id = m.group(1)
        suffix_part = entry.name[len(session_id):]
        parts = SESSION_PARTS_RE.match(session_id)
        project = parts.group(1) if parts else "unknown"

        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue

        # Map artifact suffixes to event types
        event_map = {
            "-planner.json": ("planned", "Planner completed"),
            "-reviewer.json": ("reviewed", ""),
            "-verifier.json": ("verified", ""),
            "-healer.json": ("healed", ""),
            "-monitor.json": ("monitored", "Monitor completed"),
            "-error.json": ("error", ""),
            "-result.md": ("completed", "Pipeline finished"),
        }

        if suffix_part in event_map:
            event_type, default_desc = event_map[suffix_part]
            desc = default_desc

            # Enrich with artifact content
            if suffix_part.endswith(".json"):
                data = _read_json(entry)
                if data and event_type == "reviewed":
                    desc = f"Reviewer: {data.get('verdict', '?')}"
                elif data and event_type == "verified":
                    desc = f"Verifier: {data.get('status', '?')}"
                    stages = data.get("stages", {})
                    if stages:
                        desc += f" ({', '.join(f'{k}={v}' for k, v in stages.items())})"
                elif data and event_type == "healed":
                    desc = f"Healer: {data.get('action', '?')} — {data.get('diagnosis', '')[:100]}"
                elif data and event_type == "error":
                    desc = f"Error: {data.get('error_class', '?')}"

            events.append({
                "timestamp": mtime,
                "session": session_id,
                "project": project,
                "type": event_type,
                "description": desc,
            })
        elif suffix_part == ".log":
            events.append({
                "timestamp": mtime,
                "session": session_id,
                "project": project,
                "type": "dispatched",
                "description": _extract_task(artifacts_dir, session_id)[:100] or "Worker started",
            })

    events.sort(key=lambda e: e["timestamp"], reverse=True)
    return events[:limit]
