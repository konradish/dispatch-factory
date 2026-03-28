"""SQLite database for dispatch-factory — replaces JSON file storage.

Provides:
- Ticket storage (replaces factory-backlog.json)
- Session metadata cache (replaces full directory scans)
- Auto-migration from JSON on first run
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from contextlib import contextmanager

from config import settings

logger = logging.getLogger("dispatch-factory.db")

DB_FILE = "factory.db"
_db_path: Path | None = None


def _get_db_path() -> Path:
    global _db_path
    if _db_path is None:
        _db_path = Path(settings.artifacts_dir) / DB_FILE
    return _db_path


@contextmanager
def get_conn():
    """Get a SQLite connection with WAL mode and row factory."""
    conn = sqlite3.connect(str(_get_db_path()), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create tables if they don't exist, then migrate from JSON if needed."""
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        # Add task_type column if missing (migration for existing DBs)
        try:
            conn.execute("SELECT task_type FROM tickets LIMIT 1")
        except Exception:
            conn.execute("ALTER TABLE tickets ADD COLUMN task_type TEXT NOT NULL DEFAULT 'code'")
            logger.info("Added task_type column to tickets table")
        # Add thread_id column to foreman_chat if missing
        try:
            conn.execute("SELECT thread_id FROM foreman_chat LIMIT 1")
        except Exception:
            conn.execute("ALTER TABLE foreman_chat ADD COLUMN thread_id TEXT NOT NULL DEFAULT 'default'")
            logger.info("Added thread_id column to foreman_chat table")
        # Create foreman_threads table if missing
        conn.execute("""CREATE TABLE IF NOT EXISTS foreman_threads (
            id TEXT PRIMARY KEY, title TEXT NOT NULL,
            created_at REAL NOT NULL, last_message_at REAL NOT NULL,
            message_count INTEGER NOT NULL DEFAULT 0, summary TEXT)""")
        # Create index on thread_id (after migration ensures column exists)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_thread ON foreman_chat(thread_id)")
    _migrate_from_json()
    _migrate_sessions_from_disk()


SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    id TEXT PRIMARY KEY,
    task TEXT NOT NULL,
    project TEXT NOT NULL,
    priority TEXT NOT NULL DEFAULT 'normal',
    task_type TEXT NOT NULL DEFAULT 'code',
    flags TEXT NOT NULL DEFAULT '[]',
    tags TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'pending',
    source TEXT NOT NULL DEFAULT 'manual',
    hold_reason TEXT,
    session_id TEXT,
    created_at REAL NOT NULL,
    dispatched_at REAL,
    completed_at REAL
);

CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
CREATE INDEX IF NOT EXISTS idx_tickets_project ON tickets(project);
CREATE INDEX IF NOT EXISTS idx_tickets_priority ON tickets(priority);

CREATE TABLE IF NOT EXISTS ticket_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id TEXT NOT NULL REFERENCES tickets(id),
    text TEXT NOT NULL,
    author TEXT NOT NULL DEFAULT 'human',
    timestamp REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_notes_ticket ON ticket_notes(ticket_id);

CREATE TABLE IF NOT EXISTS foreman_chat (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL DEFAULT 'default',
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    actions TEXT NOT NULL DEFAULT '[]',
    timestamp REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS foreman_threads (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at REAL NOT NULL,
    last_message_at REAL NOT NULL,
    message_count INTEGER NOT NULL DEFAULT 0,
    summary TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'worker',
    task TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'running',
    has_log INTEGER NOT NULL DEFAULT 0,
    mtime REAL NOT NULL DEFAULT 0,
    artifact_types TEXT NOT NULL DEFAULT '[]',
    summary TEXT NOT NULL DEFAULT '{}',
    updated_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project);
CREATE INDEX IF NOT EXISTS idx_sessions_state ON sessions(state);
CREATE INDEX IF NOT EXISTS idx_sessions_mtime ON sessions(mtime DESC);
"""


def _migrate_from_json() -> None:
    """Import tickets from factory-backlog.json if DB is empty."""
    with get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
        if count > 0:
            return  # Already has data

    json_path = Path(settings.artifacts_dir) / "factory-backlog.json"
    if not json_path.is_file():
        return

    try:
        tickets = json.loads(json_path.read_text())
    except (json.JSONDecodeError, OSError):
        return

    logger.info("Migrating %d tickets from JSON to SQLite", len(tickets))

    with get_conn() as conn:
        for t in tickets:
            conn.execute(
                """INSERT OR IGNORE INTO tickets
                   (id, task, project, priority, flags, tags, status, source,
                    hold_reason, session_id, created_at, dispatched_at, completed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    t["id"],
                    t.get("task", ""),
                    t.get("project", "unknown"),
                    t.get("priority", "normal"),
                    json.dumps(t.get("flags", [])),
                    json.dumps(t.get("tags", [])),
                    t.get("status", "pending"),
                    t.get("source", "manual"),
                    t.get("hold_reason"),
                    t.get("session_id"),
                    t.get("created_at", time.time()),
                    t.get("dispatched_at"),
                    t.get("completed_at"),
                ),
            )
            for note in t.get("notes", []):
                conn.execute(
                    """INSERT INTO ticket_notes (ticket_id, text, author, timestamp)
                       VALUES (?, ?, ?, ?)""",
                    (t["id"], note.get("text", ""), note.get("author", "system"), note.get("timestamp", 0)),
                )

    logger.info("Migration complete — %d tickets imported", len(tickets))


def _migrate_sessions_from_disk() -> None:
    """Scan artifacts directory and populate sessions cache."""
    import re

    with get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        if count > 0:
            return  # Already populated

    artifacts_dir = Path(settings.artifacts_dir)
    if not artifacts_dir.is_dir():
        return

    SESSION_RE = re.compile(r"^((?:worker|deploy|validate)-[a-z][a-z0-9-]*-\d+)")
    SESSION_PARTS_RE = re.compile(r"^(?:worker|deploy|validate)-(.+)-(\d+)$")
    ARTIFACT_TYPES = {
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
    }

    sessions: dict[str, dict] = {}
    for entry in artifacts_dir.iterdir():
        m = SESSION_RE.match(entry.name)
        if not m:
            continue
        session_id = m.group(1)
        if session_id not in sessions:
            parts = SESSION_PARTS_RE.match(session_id)
            project = parts.group(1) if parts else "unknown"
            stype = "deploy" if session_id.startswith("deploy-") else "validate" if session_id.startswith("validate-") else "worker"
            sessions[session_id] = {
                "id": session_id,
                "project": project,
                "type": stype,
                "task": "",
                "artifact_types": [],
                "has_log": False,
                "mtime": 0.0,
                "artifacts": {},
            }

        suffix_part = entry.name[len(session_id):]
        try:
            mt = entry.stat().st_mtime
            if mt > sessions[session_id]["mtime"]:
                sessions[session_id]["mtime"] = mt
        except OSError:
            pass

        for suffix, name in ARTIFACT_TYPES.items():
            if suffix_part == suffix:
                sessions[session_id]["artifact_types"].append(name)
                # Read artifact data for summary
                try:
                    if name != "result":
                        data = json.loads(entry.read_text())
                        sessions[session_id]["artifacts"][name] = data
                except (json.JSONDecodeError, OSError):
                    pass
                break

        if suffix_part == ".log":
            sessions[session_id]["has_log"] = True
            # Extract task from .prompt file
            prompt_file = artifacts_dir / f"{session_id}.prompt"
            if prompt_file.is_file():
                try:
                    text = prompt_file.read_text()
                    start = text.find("## Task")
                    if start != -1:
                        start = text.index("\n", start) + 1
                        end = text.find("## Project", start)
                        if end == -1:
                            end = text.find("##", start)
                        task = text[start:end].strip() if end != -1 else text[start:].strip()
                        sessions[session_id]["task"] = task[:200]
                except OSError:
                    pass

    logger.info("Caching %d sessions from disk to SQLite", len(sessions))

    with get_conn() as conn:
        for s in sessions.values():
            # Derive state
            arts = s["artifacts"]
            if "abandoned" in arts:
                state = "abandoned"
            elif "error" in arts:
                state = "error"
            elif "result" in s["artifact_types"]:
                verifier = arts.get("verifier")
                if isinstance(verifier, dict):
                    vs = verifier.get("status", "")
                    if vs == "DEPLOYED":
                        state = "deployed"
                    elif vs == "ROLLBACK":
                        state = "rolled_back"
                    else:
                        state = "completed"
                else:
                    state = "completed"
            elif "monitor" in arts:
                state = "monitoring"
            elif "verifier" in arts:
                state = "verifying"
            elif "reviewer" in arts:
                state = "reviewing"
            elif "planner" in arts:
                state = "planning"
            else:
                state = "running"

            # Build summary
            reviewer = arts.get("reviewer")
            verifier = arts.get("verifier")
            healer = arts.get("healer")
            heal_verified = arts.get("heal_verified")
            summary = {
                "verdict": reviewer.get("verdict", "") if isinstance(reviewer, dict) else "",
                "feedback": (reviewer.get("feedback", "") if isinstance(reviewer, dict) else "")[:200],
                "deploy_status": verifier.get("status", "") if isinstance(verifier, dict) else "",
                "stages": verifier.get("stages", {}) if isinstance(verifier, dict) else {},
                "healed": healer is not None,
                "healer_action": healer.get("action", "") if isinstance(healer, dict) else "",
                "healer_diagnosis": (healer.get("diagnosis", "") if isinstance(healer, dict) else "")[:200],
                "heal_verified": heal_verified.get("status", "") if isinstance(heal_verified, dict) else "",
            }

            conn.execute(
                """INSERT OR IGNORE INTO sessions
                   (id, project, type, task, state, has_log, mtime, artifact_types, summary, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    s["id"], s["project"], s["type"], s["task"],
                    state, int(s["has_log"]), s["mtime"],
                    json.dumps(s["artifact_types"]), json.dumps(summary),
                    time.time(),
                ),
            )

    logger.info("Session cache populated — %d sessions", len(sessions))


# --- Ticket helpers (used by backlog.py) ---

def row_to_ticket(row: sqlite3.Row, notes: list[dict] | None = None) -> dict:
    """Convert a SQLite Row to the ticket dict format the rest of the code expects."""
    d = dict(row)
    d["flags"] = json.loads(d["flags"])
    d["tags"] = json.loads(d["tags"])
    if notes is not None:
        d["notes"] = notes
    return d


def get_ticket_notes(conn: sqlite3.Connection, ticket_id: str) -> list[dict]:
    """Fetch notes for a ticket."""
    rows = conn.execute(
        "SELECT text, author, timestamp FROM ticket_notes WHERE ticket_id = ? ORDER BY timestamp",
        (ticket_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# --- Session helpers (used by artifacts.py) ---

def row_to_session(row: sqlite3.Row) -> dict:
    """Convert a SQLite Row to the session dict format."""
    d = dict(row)
    d["artifact_types"] = json.loads(d["artifact_types"])
    d["summary"] = json.loads(d.get("summary", "{}"))
    d["has_log"] = bool(d["has_log"])
    return d


def upsert_session(conn: sqlite3.Connection, session_id: str, **kwargs) -> None:
    """Insert or update a session in the cache."""
    existing = conn.execute("SELECT id FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if existing:
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        conn.execute(f"UPDATE sessions SET {sets}, updated_at = ? WHERE id = ?", [*kwargs.values(), time.time(), session_id])
    else:
        kwargs["id"] = session_id
        kwargs["updated_at"] = time.time()
        cols = ", ".join(kwargs.keys())
        placeholders = ", ".join("?" for _ in kwargs)
        conn.execute(f"INSERT INTO sessions ({cols}) VALUES ({placeholders})", list(kwargs.values()))
