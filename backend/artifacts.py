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
    "-heal-verified.json": "heal_verified",
    "-fixer.json": "fixer",
    "-error.json": "error",
    "-abandoned.json": "abandoned",
    "-result.md": "result",
    "-validate.json": "validate",
    "-worker-done.json": "worker_done",
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
    if "abandoned" in artifacts:
        return "abandoned"
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
    if "worker_done" in artifacts:
        return "worker_done"
    # Has a log but no other artifacts — worker is running or just started
    return "running"


def _refresh_new_sessions() -> None:
    """Check for sessions on disk not yet in the cache and add them."""
    import db
    import time as _time
    artifacts_dir = _artifacts_path()
    if not artifacts_dir.is_dir():
        return

    with db.get_conn() as conn:
        cached_ids = {r[0] for r in conn.execute("SELECT id FROM sessions").fetchall()}

    # Scan for .log files (each represents a session)
    new_sessions = []
    for entry in artifacts_dir.iterdir():
        if not entry.name.endswith(".log"):
            continue
        m = SESSION_RE.match(entry.name)
        if not m:
            continue
        session_id = m.group(1)
        if session_id in cached_ids:
            continue
        # New session — add to cache
        parts = SESSION_PARTS_RE.match(session_id)
        project = parts.group(1) if parts else "unknown"
        stype = "deploy" if session_id.startswith("deploy-") else "validate" if session_id.startswith("validate-") else "worker"
        task = _extract_task(artifacts_dir, session_id)
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            mtime = _time.time()
        new_sessions.append((session_id, project, stype, task, "running", 1, mtime, "[]", "{}", _time.time()))

    if new_sessions:
        with db.get_conn() as conn:
            conn.executemany(
                """INSERT OR IGNORE INTO sessions (id, project, type, task, state, has_log, mtime, artifact_types, summary, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                new_sessions,
            )


def _update_session_state(session_id: str) -> None:
    """Re-scan artifacts for a single session and update its cache entry."""
    import db
    import time as _time
    artifacts_dir = _artifacts_path()
    if not artifacts_dir.is_dir():
        return

    artifact_types = []
    artifacts_data: dict = {}
    has_log = False
    mtime = 0.0

    for entry in artifacts_dir.iterdir():
        if not entry.name.startswith(session_id):
            continue
        suffix_part = entry.name[len(session_id):]
        if suffix_part and suffix_part[0] not in ('-', '.'):
            continue  # Prefix collision — not this session
        try:
            mt = entry.stat().st_mtime
            if mt > mtime:
                mtime = mt
        except OSError:
            pass
        for suffix, name in ARTIFACT_TYPES.items():
            if suffix_part == suffix:
                artifact_types.append(name)
                if name == "result":
                    artifacts_data[name] = True  # sentinel, don't parse as JSON
                else:
                    artifacts_data[name] = _read_json(entry)
                break
        if suffix_part == ".log":
            has_log = True

    state = _detect_session_state(artifacts_data)

    # Build summary
    reviewer = artifacts_data.get("reviewer")
    verifier = artifacts_data.get("verifier")
    healer = artifacts_data.get("healer")
    heal_verified = artifacts_data.get("heal_verified")
    summary = {
        "verdict": reviewer.get("verdict", "") if isinstance(reviewer, dict) else "",
        "deploy_status": verifier.get("status", "") if isinstance(verifier, dict) else "",
        "healed": healer is not None,
        "healer_action": healer.get("action", "") if isinstance(healer, dict) else "",
        "heal_verified": heal_verified.get("status", "") if isinstance(heal_verified, dict) else "",
    }

    with db.get_conn() as conn:
        conn.execute(
            """UPDATE sessions SET state=?, has_log=?, mtime=?, artifact_types=?, summary=?, updated_at=?
               WHERE id=?""",
            (state, int(has_log), mtime, json.dumps(artifact_types), json.dumps(summary), _time.time(), session_id),
        )


def list_sessions() -> list[dict]:
    """Return sessions from SQLite cache, refreshing new sessions first."""
    import db
    _refresh_new_sessions()
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, project, type, task, state, has_log, artifact_types FROM sessions ORDER BY id DESC"
        ).fetchall()
    return [
        {
            "id": r["id"],
            "project": r["project"],
            "type": r["type"],
            "task": r["task"],
            "state": r["state"],
            "has_log": bool(r["has_log"]),
            "artifact_types": json.loads(r["artifact_types"]),
        }
        for r in rows
    ]


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
        if suffix_part and suffix_part[0] not in ('-', '.'):
            continue  # Prefix collision — not this session

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


def abandon_session(session_id: str, reason: str = "no active worker") -> bool:
    """Mark a session as abandoned by writing an abandoned artifact."""
    artifacts_dir = _artifacts_path()
    if not artifacts_dir.is_dir():
        return False
    abandoned_file = artifacts_dir / f"{session_id}-abandoned.json"
    if abandoned_file.is_file():
        return False  # Already abandoned
    import time
    data = {
        "reason": reason,
        "timestamp": time.time(),
    }
    abandoned_file.write_text(json.dumps(data, indent=2))
    return True


def get_known_projects() -> list[str]:
    """Discover projects from artifacts + dispatch --projects, excluding archived."""
    import archived_projects

    projects: set[str] = set()

    # From artifacts
    artifacts_dir = _artifacts_path()
    if artifacts_dir.is_dir():
        for entry in artifacts_dir.iterdir():
            m = SESSION_RE.match(entry.name)
            if m:
                parts = SESSION_PARTS_RE.match(m.group(1))
                if parts:
                    projects.add(parts.group(1))

    # From dispatch CLI
    try:
        result = subprocess.run(
            [settings.dispatch_bin, "--projects"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                line = line.strip()
                # dispatch --projects outputs project names with leading spaces
                if line and not line.startswith(("path", "test", "aliases", "local_url", "smoke", "deploy", "known", "stage")):
                    projects.add(line)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Filter out archived projects
    archived = archived_projects.get_archived()
    projects -= set(archived)

    return sorted(projects)


def get_autopilot_state() -> dict | None:
    """Read autopilot-state.json if it exists."""
    path = _artifacts_path() / "autopilot-state.json"
    return _read_json(path)


def list_sessions_with_timestamps() -> list[dict]:
    """Return sessions with timestamps and summaries from SQLite cache (fast)."""
    import db
    import time as _time
    _refresh_new_sessions()
    # Refresh state for sessions marked "running" in cache
    active_ids = {s["id"] for s in get_active_sessions()}
    with db.get_conn() as conn:
        running = conn.execute("SELECT id FROM sessions WHERE state IN ('running', 'worker_done', 'planning') LIMIT 100").fetchall()
    for r in running:
        sid = r["id"]
        _update_session_state(sid)
        # If still "running" after re-scan but no tmux process → mark abandoned
        # But only if: no worker_done artifact exists, and session is old enough
        with db.get_conn() as conn:
            row = conn.execute("SELECT state FROM sessions WHERE id = ?", (sid,)).fetchone()
            if row and row["state"] in ("running", "planning") and sid not in active_ids:
                # Skip if worker_done artifact exists (worker finished, pipeline will pick it up)
                worker_done_file = _artifacts_path() / f"{sid}-worker-done.json"
                if worker_done_file.is_file():
                    continue
                # Age gate: only mark abandoned if log is old enough (matches _gc_zombie_sessions)
                from heartbeat import ZOMBIE_THRESHOLD_MINUTES
                log_path = _artifacts_path() / f"{sid}.log"
                if log_path.is_file():
                    try:
                        age_minutes = (_time.time() - log_path.stat().st_mtime) / 60
                    except OSError:
                        age_minutes = 0
                    if age_minutes < ZOMBIE_THRESHOLD_MINUTES:
                        continue
                abandon_session(sid, "zombie detected in list_sessions")
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM sessions ORDER BY mtime DESC"
        ).fetchall()
    return [db.row_to_session(r) for r in rows]


def get_brief() -> dict:
    """Build a brief: autopilot state + aggregate stats from SQLite cache."""
    import db
    state = get_autopilot_state() or {}

    with db.get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        deployed = conn.execute("SELECT COUNT(*) FROM sessions WHERE state = 'deployed'").fetchone()[0]
        completed = conn.execute("SELECT COUNT(*) FROM sessions WHERE state = 'completed'").fetchone()[0]
        failed = conn.execute("SELECT COUNT(*) FROM sessions WHERE state IN ('error', 'rolled_back')").fetchone()[0]
        healed = conn.execute("SELECT COUNT(*) FROM sessions WHERE summary LIKE '%\"healed\": true%'").fetchone()[0]

    # Per-project breakdown via SQL
    projects: dict[str, dict] = {}
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT project,
                      COUNT(*) as total,
                      SUM(CASE WHEN state = 'deployed' THEN 1 ELSE 0 END) as deployed,
                      SUM(CASE WHEN state = 'completed' THEN 1 ELSE 0 END) as completed,
                      SUM(CASE WHEN state IN ('error', 'rolled_back') THEN 1 ELSE 0 END) as failed
               FROM sessions GROUP BY project"""
        ).fetchall()
    for r in rows:
        projects[r["project"]] = {
            "deployed": r["deployed"], "completed": r["completed"],
            "failed": r["failed"], "total": r["total"],
        }

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


def get_healer_effectiveness() -> dict:
    """Compute healer effectiveness metrics from session history.

    Categorizes healed sessions into:
    - deployed: healed AND verifier reported DEPLOYED (true success)
    - completed_unverified: healed, has result, but no DEPLOYED status (false confidence)
    - failed: healed but ended in error/rolled_back (known failure)
    """
    sessions = list_sessions_with_timestamps()

    healed_sessions = [s for s in sessions if s.get("summary", {}).get("healed", False)]
    total_healed = len(healed_sessions)

    deployed_verified = 0
    deployed_unverified = 0
    completed_unverified = 0
    failed = 0
    details: list[dict] = []

    for s in healed_sessions:
        summary = s.get("summary", {})
        state = s.get("state", "")
        deploy_status = summary.get("deploy_status", "")
        heal_verify_status = summary.get("heal_verified", "")

        if state == "deployed" or deploy_status == "DEPLOYED":
            if heal_verify_status == "passed":
                category = "deployed_verified"
                deployed_verified += 1
            else:
                category = "deployed_unverified"
                deployed_unverified += 1
        elif state in ("error", "rolled_back"):
            category = "failed"
            failed += 1
        elif state == "completed":
            category = "completed_unverified"
            completed_unverified += 1
        else:
            category = "other"

        details.append({
            "session": s["id"],
            "project": s["project"],
            "state": state,
            "deploy_status": deploy_status,
            "healer_action": summary.get("healer_action", ""),
            "heal_verified": heal_verify_status,
            "category": category,
        })

    # deployed_verified = truly confirmed working after healing
    # deployed_unverified = verifier said DEPLOYED but no post-heal check passed
    # completed_unverified = no DEPLOYED status at all
    # false_confidence_rate = sessions that look successful but aren't verified
    total_unverified = deployed_unverified + completed_unverified
    return {
        "total_healed": total_healed,
        "deployed_verified": deployed_verified,
        "deployed_unverified": deployed_unverified,
        "completed_unverified": completed_unverified,
        "failed": failed,
        "true_success_rate": round(deployed_verified / total_healed * 100, 1) if total_healed > 0 else 0,
        "false_confidence_rate": round(total_unverified / total_healed * 100, 1) if total_healed > 0 else 0,
        "sessions": details,
    }


def get_zombie_sessions() -> list[dict]:
    """Find tmux sessions matching dispatch pattern but running bare shells (dead workers)."""
    try:
        result = subprocess.run(
            ["tmux", "list-panes", "-a", "-F", "#{session_name}\t#{pane_current_command}"],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    import time
    zombies = []
    for line in result.stdout.strip().splitlines():
        parts = line.strip().split("\t", 1)
        if len(parts) != 2:
            continue
        name, cmd = parts
        if SESSION_RE.match(name) and cmd in SHELL_COMMANDS:
            log_path = _artifacts_path() / f"{name}.log"
            age_minutes = 0
            try:
                age_minutes = int((time.time() - log_path.stat().st_mtime) / 60)
            except OSError:
                pass
            zombies.append({"id": name, "command": cmd, "age_minutes": age_minutes})
    return zombies


def get_session_timeline(session_id: str) -> list[dict]:
    """Return session artifacts as timeline events with file mtime timestamps."""
    artifacts_dir = _artifacts_path()
    if not artifacts_dir.is_dir():
        return []
    prefix = session_id
    events: list[dict] = []
    for entry in artifacts_dir.iterdir():
        if not entry.name.startswith(prefix):
            continue
        suffix_part = entry.name[len(prefix):]
        if suffix_part and suffix_part[0] not in ('-', '.'):
            continue  # Prefix collision — not this session
        for suffix, name in ARTIFACT_TYPES.items():
            if suffix_part == suffix:
                try:
                    mtime = entry.stat().st_mtime
                except OSError:
                    continue
                data = _read_text(entry) if name == "result" else _read_json(entry)
                events.append({"type": name, "timestamp": mtime, "data": data})
                break
    events.sort(key=lambda e: e["timestamp"])
    return events


def get_activity_feed(limit: int = 100) -> list[dict]:
    """Merge factory log events with ticket lifecycle events."""
    events = get_factory_log(limit=0)
    import backlog as _backlog
    for t in _backlog.list_tickets():
        project = t.get("project", "unknown")
        tid = t["id"]
        if t.get("created_at"):
            events.append({"timestamp": t["created_at"], "session": "", "project": project, "type": "ticket_created", "description": t.get("task", "")[:100], "ticket_id": tid})
        if t.get("dispatched_at"):
            events.append({"timestamp": t["dispatched_at"], "session": t.get("session_id", ""), "project": project, "type": "ticket_dispatched", "description": f"Dispatched -> {t.get('session_id', '?')}", "ticket_id": tid})
        if t.get("completed_at"):
            status = t.get("status", "completed")
            events.append({"timestamp": t["completed_at"], "session": t.get("session_id", ""), "project": project, "type": f"ticket_{status}", "description": f"Ticket {status}", "ticket_id": tid})
    events.sort(key=lambda e: e["timestamp"], reverse=True)
    return events[:limit]


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
            "-heal-verified.json": ("heal_verified", ""),
            "-monitor.json": ("monitored", "Monitor completed"),
            "-error.json": ("error", ""),
            "-abandoned.json": ("abandoned", ""),
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
                elif data and event_type == "heal_verified":
                    desc = f"Heal verify: {data.get('status', '?')} — {data.get('reason', '')[:100]}"
                elif data and event_type == "error":
                    desc = f"Error: {data.get('error_class', '?')}"
                elif data and event_type == "abandoned":
                    desc = f"Abandoned: {data.get('reason', '?')}"

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
