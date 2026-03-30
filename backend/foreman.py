"""Factory foreman — LLM-driven heartbeat reasoning with rotating lenses.

Each heartbeat tick, the foreman picks the next lens in rotation,
builds a state snapshot, calls claude_reason, and executes approved actions.

Prompts live in foreman-prompts/ and are editable via the UI.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import artifacts
import backlog
import circuit_breaker
import healer_circuit_breaker
import meta_work_ratio
import pipeline
from config import settings

logger = logging.getLogger("dispatch-factory.foreman")

# Per-ticket locks to prevent concurrent dispatch of the same ticket.
# Guards the read-check-update race between status read and status write.
_dispatch_locks: dict[str, threading.Lock] = {}
_dispatch_locks_guard = threading.Lock()


def _get_ticket_lock(ticket_id: str) -> threading.Lock:
    """Get or create a per-ticket lock."""
    with _dispatch_locks_guard:
        if ticket_id not in _dispatch_locks:
            _dispatch_locks[ticket_id] = threading.Lock()
        return _dispatch_locks[ticket_id]


def _cleanup_ticket_lock(ticket_id: str) -> None:
    """Remove a ticket lock after dispatch completes (prevents unbounded growth)."""
    with _dispatch_locks_guard:
        _dispatch_locks.pop(ticket_id, None)


def _dispatch_async(cmd: list[str], ticket_id: str) -> dict:
    """Fire-and-forget dispatch via Popen. Returns immediately.

    Marks ticket as 'dispatching', spawns subprocess in background thread
    that waits for exit, parses session ID, and calls mark_dispatched.
    The heartbeat picks up completion artifacts independently.

    Uses a per-ticket lock + compare-and-swap to prevent duplicate dispatch
    when concurrent heartbeat/foreman cycles race on the same ticket.
    """
    lock = _get_ticket_lock(ticket_id)
    if not lock.acquire(blocking=False):
        logger.warning("duplicate dispatch blocked for ticket %s (lock held)", ticket_id)
        return {"status": "already_dispatching", "detail": "Another dispatch is in progress"}

    try:
        # Compare-and-swap: only proceed if ticket is still in a dispatchable state
        ticket = next((t for t in backlog.list_tickets() if t["id"] == ticket_id), None)
        if ticket and ticket.get("status") not in ("pending", "ready"):
            logger.warning(
                "duplicate dispatch blocked for ticket %s (status=%s)",
                ticket_id, ticket.get("status"),
            )
            lock.release()
            _cleanup_ticket_lock(ticket_id)
            return {"status": "already_dispatching", "detail": f"Ticket status is '{ticket.get('status')}', not pending/ready"}

        backlog.update_ticket(ticket_id, {"status": "dispatching", "dispatched_at": time.time()})
    except Exception:
        lock.release()
        _cleanup_ticket_lock(ticket_id)
        raise

    log_file = None
    try:
        log_path = Path(tempfile.gettempdir()) / f"dispatch-{ticket_id[:8]}.log"
        log_file = open(log_path, "w")
        proc = subprocess.Popen(
            cmd, stdout=log_file, stderr=subprocess.STDOUT,
        )
    except Exception as e:
        if log_file:
            log_file.close()
        backlog.update_ticket(ticket_id, {"status": "pending"})
        lock.release()
        _cleanup_ticket_lock(ticket_id)
        logger.error("dispatch Popen failed for %s: %s", ticket_id, e)
        return {"status": "error", "detail": str(e)}

    def _wait():
        dispatched_ok = False
        try:
            try:
                proc.wait(timeout=600)  # 10 min hard ceiling
            except subprocess.TimeoutExpired:
                proc.kill()
                logger.error("dispatch subprocess killed after 600s for ticket %s", ticket_id)
                backlog.update_ticket(ticket_id, {"status": "pending"})
                return
            finally:
                log_file.close()

            stdout = log_path.read_text()
            if proc.returncode == 0:
                match = re.search(r"session\s*:\s*([\w-]+)", stdout)
                if match:
                    session_id = match.group(1)
                    backlog.mark_dispatched(ticket_id, session_id)
                    dispatched_ok = True
                    logger.info("dispatch ok for %s → %s", ticket_id, session_id)
                else:
                    logger.error(
                        "dispatch exited 0 for %s but no session ID in output: %s",
                        ticket_id, stdout[:200],
                    )
                    backlog.update_ticket(ticket_id, {"status": "pending"})
            else:
                logger.error("dispatch failed for %s (rc=%d): %s", ticket_id, proc.returncode, stdout[:200])
                backlog.update_ticket(ticket_id, {"status": "pending"})
        except Exception as exc:
            logger.exception("unexpected error in _wait for %s", ticket_id, exc_info=exc)
            if not dispatched_ok:
                try:
                    backlog.update_ticket(ticket_id, {"status": "pending"})
                except Exception:
                    logger.exception("failed to reset ticket %s to pending", ticket_id)
        finally:
            lock.release()
            _cleanup_ticket_lock(ticket_id)

    threading.Thread(target=_wait, daemon=True, name=f"dispatch-{ticket_id[:8]}").start()
    return {"status": "ok", "detail": "dispatch started (async)"}

PROMPTS_DIR = Path(__file__).parent / "foreman-prompts"

# Rotation state
_rotation_index = 0
_last_result: dict = {}
_active_stream_path: str | None = None


def get_stream_events(after_line: int = 0) -> tuple[list[dict], int]:
    """Read new events from the foreman's active stream file. Returns (events, next_line)."""
    if not _active_stream_path:
        return [], after_line
    try:
        path = Path(_active_stream_path)
        if not path.is_file():
            return [], after_line
        lines = path.read_text().strip().split("\n")
        new_lines = lines[after_line:]
        events = []
        for line in new_lines:
            if line.strip():
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return events, len(lines)
    except OSError:
        return [], after_line


def list_lenses() -> list[dict]:
    """List all available foreman lenses (prompt files)."""
    if not PROMPTS_DIR.is_dir():
        return []
    lenses = []
    for f in sorted(PROMPTS_DIR.glob("*.md")):
        lenses.append({
            "id": f.stem,
            "name": f.stem.replace("-", " ").title(),
            "path": str(f),
            "prompt": f.read_text(),
        })
    return lenses


def get_lens(lens_id: str) -> dict | None:
    """Get a single lens by ID."""
    path = PROMPTS_DIR / f"{lens_id}.md"
    if not path.is_file():
        return None
    return {
        "id": path.stem,
        "name": path.stem.replace("-", " ").title(),
        "path": str(path),
        "prompt": path.read_text(),
    }


def save_lens(lens_id: str, prompt: str) -> None:
    """Save a lens prompt (create or update)."""
    PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    path = PROMPTS_DIR / f"{lens_id}.md"
    path.write_text(prompt)


def delete_lens(lens_id: str) -> bool:
    """Delete a lens."""
    path = PROMPTS_DIR / f"{lens_id}.md"
    if path.is_file():
        path.unlink()
        return True
    return False


def get_rotation_state() -> dict:
    """Current rotation state."""
    lenses = list_lenses()
    current = lenses[_rotation_index % len(lenses)] if lenses else None
    return {
        "index": _rotation_index,
        "current_lens": current["id"] if current else None,
        "total_lenses": len(lenses),
        "lens_order": [l["id"] for l in lenses],
        "last_result": _last_result,
    }


def _build_state_snapshot() -> str:
    """Build a text snapshot of factory state for the foreman."""
    # Active workers
    active = artifacts.get_active_sessions()
    active_str = json.dumps(active, indent=2) if active else "No active workers."

    # Recent sessions (last 20)
    recent = artifacts.list_sessions_with_timestamps()[:20]
    recent_summary = []
    for s in recent:
        summ = s.get("summary", {})
        recent_summary.append({
            "id": s["id"],
            "project": s["project"],
            "state": s["state"],
            "task": s.get("task", ""),
            "verdict": summ.get("verdict", ""),
            "deploy": summ.get("deploy_status", ""),
            "healed": summ.get("healed", False),
        })

    # Backlog
    pending = backlog.list_tickets(status="pending")
    dispatching = backlog.list_tickets(status="dispatching")
    dispatched = backlog.list_tickets(status="dispatched")
    on_hold = backlog.list_tickets(status="on_hold")

    # Meta-work ratio
    mwr = meta_work_ratio.get_ratio()

    # Direction
    direction_file = Path(settings.artifacts_dir) / "autopilot-direction.md"
    direction = direction_file.read_text().strip() if direction_file.is_file() else "No direction set."

    # Brief stats
    brief = artifacts.get_brief()
    stats = brief.get("stats", {})

    # Project health
    import project_health as _ph
    health = _ph.get_project_health()
    health_json = json.dumps(health, indent=2)
    neglected_names = [h["project"] for h in health if "neglected" in h.get("alerts", [])]
    broken_names = [h["project"] for h in health if "deploy_broken" in h.get("alerts", [])]
    neglected_line = ("Neglected projects (>7 days idle): " + ", ".join(neglected_names)) if neglected_names else "No neglected projects."
    broken_line = ("Deploy-broken projects: " + ", ".join(broken_names)) if broken_names else "No deploy-broken projects."

    # Meta-work warning
    meta_warning = ""
    if mwr["ratio"] > 0.4:
        meta_warning = f"""
### META-WORK WARNING
Factory self-improvement ratio: {mwr['factory_count']}/{mwr['total']} ({mwr['ratio']:.0%}) — threshold is {mwr['threshold']:.0%}.
{"BLOCKED: dispatch-factory tickets cannot be dispatched until product work lowers the ratio." if mwr["blocked"] else "CAUTION: approaching limit. Prioritize product work."}
DO NOT create new dispatch-factory tickets unless they are urgent fixes. Focus on dispatching product work.
"""

    on_hold_section = ""
    if on_hold:
        on_hold_section = f"""
### On Hold ({len(on_hold)} tickets)
These tickets are parked — waiting for human input, a time window, or an external dependency. Do NOT dispatch or recreate them.
{json.dumps(on_hold, indent=2)}
"""

    # Zombie tmux sessions
    zombies = artifacts.get_zombie_sessions()
    zombie_str = json.dumps(zombies, indent=2) if zombies else "None."

    # Failed session log tails (last 3 failed sessions, 20 lines each)
    failed_logs_section = ""
    failed_sessions = [s for s in recent if s.get("state") in ("error", "abandoned", "rolled_back")][:3]
    if failed_sessions:
        log_parts = []
        for s in failed_sessions:
            log_path = Path(settings.artifacts_dir) / f"{s['id']}.log"
            try:
                lines = log_path.read_text().splitlines()[-20:]
                log_parts.append(f"**{s['id']}** ({s.get('state', '?')}):\n```\n" + "\n".join(lines) + "\n```")
            except OSError:
                pass
        if log_parts:
            failed_logs_section = "\n### Recent Failure Logs\n" + "\n".join(log_parts)

    # Pipeline config
    pipeline_summary = pipeline.get_pipeline_summary()
    pipeline_overrides = " (HAS CUSTOM OVERRIDES)" if pipeline_summary.get("has_overrides") else ""
    pipeline_stations_brief = json.dumps([
        {"id": s["id"], "enabled": s["enabled"], "timeout": s.get("timeout")}
        for s in pipeline_summary["stages"]
    ], indent=2)

    # Memory: recent decisions and noticings for cross-cycle continuity
    recent_decisions = get_recent_decisions(limit=5)
    recent_noticings = get_recent_noticings(limit=10)
    unanswered = get_unanswered_questions()

    unanswered_section = ""
    if unanswered:
        unanswered_section = f"""
### Unanswered Questions ({len(unanswered)})
You asked these and haven't gotten a response yet. Do NOT re-ask — wait for the human.
If a question has been pending >24h, escalate via flag_human.
{json.dumps(unanswered, indent=2)}
"""

    return f"""## Factory State Snapshot

### Active Workers ({len(active)})
{active_str}
{unanswered_section}
### Zombie TMux Sessions ({len(zombies)})
Sessions where the worker exited but tmux session remains. Use kill_session to clean up.
{zombie_str}
{meta_warning}
### Recent Sessions (last 20)
{json.dumps(recent_summary, indent=2)}
{failed_logs_section}

### Backlog
Pending: {len(pending)} tickets
{json.dumps(pending, indent=2) if pending else "Empty."}

Dispatching: {len(dispatching)} tickets (async dispatch in progress — if stuck >10 min, use update_ticket to set status to "pending" or "failed")
{json.dumps(dispatching, indent=2) if dispatching else "None."}

In-flight: {len(dispatched)} tickets
{json.dumps(dispatched, indent=2) if dispatched else "None."}
{on_hold_section}
### Aggregate Stats
{json.dumps(stats, indent=2)}

### Project Health
{health_json}

{neglected_line}
{broken_line}

### Direction Vector
{direction}

### Recent Decisions (your last {len(recent_decisions)} cycles)
Review these to build on prior observations, not start fresh each time.
{json.dumps(recent_decisions, indent=2) if recent_decisions else "No prior decisions recorded yet."}

### Noticings (half-formed observations from prior cycles)
Patterns you've noticed but haven't acted on. Follow up if they still apply.
{json.dumps(recent_noticings, indent=2) if recent_noticings else "No noticings yet."}

### Pipeline Configuration{pipeline_overrides}
{pipeline_stations_brief}
Global: {json.dumps(pipeline_summary["global"])}
"""


def run_foreman(lens_id: str | None = None, human_message: str | None = None) -> dict:
    """Run one foreman cycle with the specified or next-in-rotation lens.

    If human_message is provided, it's injected as a direct instruction from
    the human operator. The foreman uses the triage lens by default for chat.
    """
    global _rotation_index, _last_result

    lenses = list_lenses()
    if not lenses:
        return {"error": "No foreman lenses configured"}

    # Pick lens — chat defaults to triage lens for responsiveness
    if human_message:
        lens = get_lens("triage") or lenses[0]
    elif lens_id:
        lens = get_lens(lens_id)
        if not lens:
            return {"error": f"Lens not found: {lens_id}"}
    else:
        lens = lenses[_rotation_index % len(lenses)]
        _rotation_index += 1

    # Build prompt
    state_snapshot = _build_state_snapshot()

    human_section = ""
    if human_message:
        human_section = f"""
### HUMAN MESSAGE (respond to this directly)
The human operator is talking to you. Address their request using the factory state above.
Respond with a clear assessment and take any actions needed.

**Human says:** {human_message}
"""

    full_prompt = f"""{lens['prompt']}

{state_snapshot}
{human_section}
"""

    logger.info("Running foreman with lens: %s%s", lens["id"], " (chat)" if human_message else "")

    # Call claude_reason
    result = _call_llm(full_prompt)

    if result is None:
        _last_result = {"lens": lens["id"], "error": "LLM call failed", "timestamp": time.time()}
        return _last_result

    # Parse and execute actions
    actions_taken = []
    for action in result.get("actions", []):
        action_result = _execute_action(action)
        actions_taken.append(action_result)

    _last_result = {
        "lens": lens["id"],
        "assessment": result.get("assessment", ""),
        "observations": result.get("observations", ""),
        "actions": actions_taken,
        "raw_actions": result.get("actions", []),
        "timestamp": time.time(),
    }

    # Log to factory log
    _write_foreman_log(_last_result)

    # Decision log — what the foreman chose and why (B2's [contra] trail)
    _write_decision_log({
        "timestamp": time.time(),
        "lens": lens["id"],
        "is_chat": bool(human_message),
        "assessment": result.get("assessment", ""),
        "observations": result.get("observations", ""),
        "actions_requested": result.get("actions", []),
        "actions_results": actions_taken,
    })

    return _last_result


def _call_llm(prompt: str) -> dict | None:
    """Call Claude via Agent SDK with memory and project context enabled."""
    env = dict(__import__("os").environ)
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("CLAUDECODE", None)

    # Factory project dir — enables CLAUDE.md, auto-memory, project context
    # Hardcoded because uvicorn --reload can make __file__ resolve strangely
    factory_dir = "/mnt/c/projects/dispatch-factory"

    script_content = """
import asyncio, sys, json, pathlib

async def main():
    prompt_path = sys.argv[1]
    out_path = sys.argv[2]
    max_turns = int(sys.argv[3])
    cwd = sys.argv[4] if len(sys.argv) > 4 else None
    stream_path = sys.argv[5] if len(sys.argv) > 5 else None

    prompt = pathlib.Path(prompt_path).read_text()

    from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, TextBlock
    options = ClaudeAgentOptions(
        max_turns=max_turns,
        cwd=cwd,
        setting_sources=["user", "project"],
    )

    result_parts = []
    result_fallback = None
    sf = open(stream_path, "a") if stream_path else None

    async for msg in query(prompt=prompt, options=options):
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    result_parts.append(block.text)
                    if sf:
                        sf.write(json.dumps({"type": "text", "content": block.text}) + chr(10))
                        sf.flush()
                elif hasattr(block, "name"):
                    if sf:
                        tool_input = getattr(block, "input", {})
                        sf.write(json.dumps({"type": "tool_use", "tool": block.name, "input": tool_input}) + chr(10))
                        sf.flush()
        elif type(msg).__name__ == 'ToolResultMessage':
            if sf:
                sf.write(json.dumps({"type": "tool_result", "content": "done"}) + chr(10))
                sf.flush()
        if type(msg).__name__ == 'ResultMessage' and hasattr(msg, 'result'):
            result_fallback = msg.result

    text = chr(10).join(result_parts) if result_parts else (result_fallback or '')
    pathlib.Path(out_path).write_text(json.dumps({"response": text}))
    if sf:
        sf.write(json.dumps({"type": "done"}) + chr(10))
        sf.close()

asyncio.run(main())
"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as sf:
        sf.write(script_content)
        script_path = sf.name

    # Persist prompt and response in artifacts dir (not temp) for visibility
    artifacts_dir = Path(settings.artifacts_dir)
    ts = int(time.time())
    prompt_path = str(artifacts_dir / f"foreman-{ts}-prompt.md")
    out_path = str(artifacts_dir / f"foreman-{ts}-response.json")
    stream_path = str(artifacts_dir / f"foreman-{ts}-stream.jsonl")
    Path(prompt_path).write_text(prompt)

    # Store stream path so the API can read it
    global _active_stream_path
    _active_stream_path = stream_path

    try:
        r = subprocess.run(
            ["uvx", "--with", "claude-agent-sdk", "python", script_path, prompt_path, out_path, "100", factory_dir, stream_path],
            capture_output=True, text=True, timeout=600, env=env,
        )

        if r.returncode != 0:
            logger.error("Foreman LLM failed: %s", r.stderr[-300:])
            return None

        raw = Path(out_path).read_text() if Path(out_path).exists() else ""
        if not raw:
            return None

        response_text = json.loads(raw).get("response", "").strip()

        # Strip markdown code fences
        if response_text.startswith("```"):
            lines = response_text.split("\n")
            response_text = "\n".join(lines[1:-1] if lines[-1].strip().startswith("```") else lines[1:])

        # Try to parse as JSON first
        try:
            return json.loads(response_text)
        except json.JSONDecodeError:
            pass

        # Try to extract JSON from within the response (foreman may have mixed text + JSON)
        import re
        json_match = re.search(r'\{[^{}]*"assessment"[^{}]*\}', response_text, re.DOTALL)
        if not json_match:
            # Try multiline JSON with nested objects
            json_match = re.search(r'\{[\s\S]*"assessment"[\s\S]*\}', response_text)
        if json_match:
            try:
                return json.loads(json_match.group(0))
            except json.JSONDecodeError:
                pass

        # Fallback: treat the entire response as the assessment (foreman gave prose, not JSON)
        logger.warning("Foreman returned non-JSON response (%d chars), using as assessment", len(response_text))
        return {
            "assessment": response_text[:500],
            "observations": "",
            "actions": [],
        }

    except subprocess.TimeoutExpired:
        logger.error("Foreman LLM timed out (600s)")
        return None
    except Exception as e:
        logger.error("Foreman LLM error: %s", e)
        return None
    finally:
        _active_stream_path = None
        # Only delete the temp script — prompt, response, stream are persistent artifacts
        try:
            Path(script_path).unlink(missing_ok=True)
        except OSError:
            pass


def _execute_action(action: dict) -> dict:
    """Execute a single foreman action. Returns result dict."""
    action_type = action.get("type", "do_nothing")

    if action_type == "do_nothing":
        return {"type": "do_nothing", "status": "ok"}

    elif action_type == "dispatch":
        # Foreman is GOD MODE — no circuit breaker, no meta-work ratio, no idle mode blocks.
        # These guards only apply to heartbeat auto-dispatch, not foreman decisions.
        ticket_id = action.get("ticket_id", "")
        ticket = next((t for t in backlog.list_tickets() if t["id"] == ticket_id), None)
        if not ticket:
            return {"type": "dispatch", "status": "error", "detail": f"Ticket {ticket_id} not found"}
        # Only guard: don't dispatch if project already has an in-flight ticket
        if backlog.has_inflight_ticket(ticket["project"]):
            return {"type": "dispatch", "status": "blocked", "detail": f"Project {ticket['project']} already has an in-flight ticket"}
        # Reset circuit breaker if tripped (foreman decided to dispatch, so it's intentional)
        if circuit_breaker.is_project_blocked(ticket["project"]):
            circuit_breaker.reset_project(ticket["project"])
        import heartbeat as _hb
        active = artifacts.get_active_sessions()
        max_concurrent = _hb._state.get("max_concurrent", 3)
        if len(active) >= max_concurrent - 1 and backlog.has_eligible_higher_priority(ticket.get("priority", "normal")):
            return {"type": "dispatch", "status": "blocked", "detail": "Higher-priority tickets are pending; dispatch those first"}
        # Healer circuit breaker: disable healer for projects in spiral
        if healer_circuit_breaker.is_healer_blocked(ticket["project"]):
            flags = ticket.get("flags", [])
            if "--no-heal" not in flags:
                ticket.setdefault("flags", []).append("--no-heal")
        # Actually dispatch — filter to known CLI flags only
        valid_flags = {"--no-merge", "--plan", "--no-plan", "--deploy-only", "--validate-only", "--force-deploy"}
        cmd = [settings.dispatch_bin, ticket["task"], "--project", ticket["project"]]
        task_type = ticket.get("task_type", "code")
        if task_type != "code":
            cmd.extend(["--type", task_type])
        cmd.extend(f for f in ticket.get("flags", []) if f in valid_flags)
        result = _dispatch_async(cmd, ticket_id)
        return {"type": "dispatch", "ticket_id": ticket_id, **result}

    elif action_type == "create_ticket":
        task = action.get("task", "")
        project = action.get("project", "unknown")
        priority = action.get("priority", "normal")
        status = action.get("status", "pending")
        # Foreman is GOD MODE — no meta-work ratio block for ticket creation
        if task:
            # Allow foreman to create tickets directly as on_hold
            if status == "on_hold":
                ticket = backlog.create_ticket(task, project, priority, source="foreman", status="on_hold")
            else:
                ticket = backlog.create_ticket(task, project, priority, source="foreman")
            return {"type": "create_ticket", "status": "ok", "ticket_id": ticket["id"]}
        return {"type": "create_ticket", "status": "error", "detail": "No task provided"}

    elif action_type == "reprioritize":
        ticket_id = action.get("ticket_id", "")
        new_priority = action.get("priority", "normal")
        # Foreman is GOD MODE — no priority restrictions
        result = backlog.update_ticket(ticket_id, {"priority": new_priority})
        if result:
            return {"type": "reprioritize", "status": "ok", "ticket_id": ticket_id, "priority": new_priority}
        return {"type": "reprioritize", "status": "error", "detail": f"Ticket {ticket_id} not found"}

    elif action_type == "flag_human":
        reason = action.get("reason", "No reason given")
        return {"type": "flag_human", "status": "ok", "reason": reason}

    elif action_type == "cancel_ticket":
        ticket_id = action.get("ticket_id", "")
        result = backlog.update_ticket(ticket_id, {"status": "cancelled"})
        if result:
            return {"type": "cancel_ticket", "status": "ok", "ticket_id": ticket_id}
        return {"type": "cancel_ticket", "status": "error", "detail": f"Ticket {ticket_id} not found"}

    elif action_type == "reset_circuit_breaker":
        project = action.get("project", "")
        if not project:
            return {"type": action_type, "status": "error", "detail": "project required"}
        if circuit_breaker.reset_project(project):
            return {"type": action_type, "status": "ok", "project": project}
        return {"type": action_type, "status": "error", "detail": f"No circuit breaker state for '{project}'"}

    elif action_type == "update_ticket":
        ticket_id = action.get("ticket_id", "")
        updates = action.get("updates", {})
        if not ticket_id or not updates:
            return {"type": action_type, "status": "error", "detail": "ticket_id and updates required"}
        # Allow updating: project, priority, status, task, tags
        allowed = {"project", "priority", "status", "task", "tags"}
        filtered = {k: v for k, v in updates.items() if k in allowed}
        if not filtered:
            return {"type": action_type, "status": "error", "detail": f"No allowed fields in updates (allowed: {allowed})"}
        # Foreman is GOD MODE — no priority restrictions
        result = backlog.update_ticket(ticket_id, filtered)
        if result:
            return {"type": action_type, "status": "ok", "ticket_id": ticket_id, "detail": f"Updated: {list(filtered.keys())}"}
        return {"type": action_type, "status": "error", "detail": f"Ticket {ticket_id} not found"}

    elif action_type == "unpause_project":
        import paused_projects
        project = action.get("project", "")
        if not project:
            return {"type": action_type, "status": "error", "detail": "project required"}
        if paused_projects.unpause_project(project):
            return {"type": action_type, "status": "ok", "project": project}
        return {"type": action_type, "status": "error", "detail": f"Project '{project}' not paused"}

    elif action_type == "pause_project":
        import paused_projects
        project = action.get("project", "")
        reason = action.get("reason", "foreman decision")
        if not project:
            return {"type": action_type, "status": "error", "detail": "project required"}
        if paused_projects.pause_project(project, reason=reason):
            return {"type": action_type, "status": "ok", "project": project}
        return {"type": action_type, "status": "error", "detail": f"Project '{project}' already paused"}

    elif action_type == "update_direction":
        direction_text = action.get("direction", "").strip()
        if not direction_text:
            return {"type": action_type, "status": "error", "detail": "direction text required"}
        direction_path = Path(settings.artifacts_dir) / "autopilot-direction.md"
        direction_path.write_text(direction_text)
        return {"type": action_type, "status": "ok", "detail": "Direction vector updated"}

    elif action_type == "update_pipeline_station":
        station_id = action.get("station_id", "")
        updates = action.get("updates", {})
        if not station_id or not updates:
            return {"type": action_type, "status": "error", "detail": "station_id and updates required"}
        result = pipeline.update_station(station_id, updates)
        if result is None:
            return {"type": action_type, "status": "error", "detail": f"Station '{station_id}' not found"}
        if isinstance(result, list):
            return {"type": action_type, "status": "error", "detail": "; ".join(result)}
        return {"type": action_type, "status": "ok", "station_id": station_id, "detail": f"Updated: {list(updates.keys())}"}

    elif action_type == "update_pipeline_global":
        updates = action.get("updates", {})
        if not updates:
            return {"type": action_type, "status": "error", "detail": "updates required"}
        result = pipeline.update_global(updates)
        if isinstance(result, list):
            return {"type": action_type, "status": "error", "detail": "; ".join(result)}
        return {"type": action_type, "status": "ok", "detail": f"Updated global: {list(updates.keys())}"}

    elif action_type == "kill_session":
        session_id = action.get("session_id", "")
        if not session_id or not artifacts.SESSION_RE.match(session_id):
            return {"type": action_type, "status": "error", "detail": "Invalid session_id"}
        # Guard: don't kill actively running workers
        active_ids = {s["id"] for s in artifacts.get_active_sessions()}
        if session_id in active_ids:
            return {"type": action_type, "status": "blocked", "detail": f"Session '{session_id}' has an active worker — refusing to kill"}
        # Kill tmux session if it exists, and update cache
        try:
            subprocess.run(["tmux", "kill-session", "-t", session_id], capture_output=True, timeout=10)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass  # Session may already be gone
        # Update cache to mark as abandoned
        import db
        import time as _time
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE sessions SET state = 'abandoned', updated_at = ? WHERE id = ? AND state = 'running'",
                (_time.time(), session_id),
            )
        return {"type": action_type, "status": "ok", "session_id": session_id}

    elif action_type == "add_ticket_note":
        ticket_id = action.get("ticket_id", "")
        text = action.get("text", "")
        if not ticket_id or not text:
            return {"type": action_type, "status": "error", "detail": "ticket_id and text required"}
        result = backlog.add_note(ticket_id, text, author="foreman")
        if result:
            return {"type": action_type, "status": "ok", "ticket_id": ticket_id}
        return {"type": action_type, "status": "error", "detail": f"Ticket {ticket_id} not found"}

    elif action_type == "spawn_worker":
        # Spawn a worker to fix a pipeline/factory issue. The foreman describes the task
        # and the worker (cy -p) implements it. For self-improvement of the factory.
        task = action.get("task", "")
        project = action.get("project", "dispatch-factory")
        task_type = action.get("task_type", "code")
        if not task:
            return {"type": action_type, "status": "error", "detail": "task required"}
        # Create ticket and dispatch immediately
        ticket = backlog.create_ticket(task, project, priority="high", source="foreman", task_type=task_type)
        ticket_id = ticket["id"]
        cmd = [settings.dispatch_bin, task, "--project", project]
        if task_type != "code":
            cmd.extend(["--type", task_type])
        result = _dispatch_async(cmd, ticket_id)
        return {"type": action_type, "ticket_id": ticket_id, **result}

    elif action_type == "notice":
        # B2's "beta deposit" — half-formed observations that don't require action.
        # Stored in noticings log, surfaced to strategy/direction lenses on future cycles.
        text = action.get("text", "")
        if not text:
            return {"type": action_type, "status": "error", "detail": "text required"}
        _write_noticings_log(text)
        return {"type": action_type, "status": "ok", "detail": f"Noticed: {text[:80]}"}

    elif action_type == "ask_human":
        # Async question loop: post question, don't block, pick up answer next cycle.
        # Creates needs_input ticket + posts to #factory chat.
        question = action.get("question", action.get("text", ""))
        context = action.get("context", "")
        project = action.get("project", "")
        if not question:
            return {"type": action_type, "status": "error", "detail": "question required"}

        # Create needs_input ticket
        ticket = backlog.create_ticket(
            task=f"[QUESTION] {question}",
            project=project or "dispatch-factory",
            priority="high",
            source="foreman",
            status="needs_input",
            task_type="question",
        )
        if context:
            backlog.add_note(ticket["id"], f"Context: {context}", author="foreman")

        # Post to #factory chat so human sees it across channels
        _post_to_chat(question, context, ticket["id"])

        return {
            "type": action_type, "status": "ok",
            "ticket_id": ticket["id"],
            "detail": f"Question posted: {question[:80]}",
        }

    return {"type": action_type, "status": "unknown_action"}


def _post_to_chat(question: str, context: str, ticket_id: str) -> None:
    """Post a foreman question to the #factory chat channel."""
    body = f"**Question from Foreman** (ticket {ticket_id[:8]})\n\n{question}"
    if context:
        body += f"\n\n_Context: {context}_"
    body += "\n\nReply here or add a note to the ticket in the factory UI."

    try:
        import urllib.request
        payload = json.dumps({
            "name": "chat__send",
            "arguments": {
                "from": "foreman",
                "channel": "factory",
                "body": body,
            },
        })
        req = urllib.request.Request(
            "http://chat.localhost/call",
            data=payload.encode(),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        logger.warning("Failed to post question to chat: %s", e)


def _read_chat_replies(since_timestamp: float) -> list[dict]:
    """Read replies from #factory chat since a given timestamp."""
    try:
        import urllib.request
        payload = json.dumps({
            "name": "chat__history",
            "arguments": {
                "channel": "factory",
                "as": "foreman",
                "limit": 20,
            },
        })
        req = urllib.request.Request(
            "http://chat.localhost/call",
            data=payload.encode(),
            headers={"Content-Type": "application/json"},
        )
        resp = urllib.request.urlopen(req, timeout=5)
        data = json.loads(resp.read())
        # Parse the nested response
        content = data.get("content", [{}])
        messages_json = content[0].get("text", "{}") if content else "{}"
        messages_data = json.loads(messages_json)
        messages = messages_data.get("messages", [])

        # Filter to non-foreman replies after the timestamp
        from datetime import datetime
        replies = []
        for msg in messages:
            if msg.get("from") == "foreman":
                continue
            # Parse ISO timestamp
            created = msg.get("created_at", "")
            try:
                dt = datetime.fromisoformat(created)
                msg_ts = dt.timestamp()
            except (ValueError, TypeError):
                continue
            if msg_ts > since_timestamp:
                replies.append({"from": msg["from"], "body": msg["body"], "timestamp": msg_ts})
        return replies
    except Exception as e:
        logger.warning("Failed to read chat replies: %s", e)
        return []


def get_unanswered_questions() -> list[dict]:
    """Return needs_input question tickets that haven't been answered yet.

    A question is 'answered' if it has a note from someone other than 'foreman',
    or if there's a reply in #factory chat after the ticket was created.
    """
    questions = []
    for ticket in backlog.list_tickets(status="needs_input"):
        if ticket.get("task_type") != "question":
            continue

        # Check for answer via ticket notes
        notes = ticket.get("notes", [])
        human_notes = [n for n in notes if n.get("author") != "foreman"]
        if human_notes:
            continue  # Answered via ticket note

        # Check for answer via #factory chat
        created_at = ticket.get("created_at", 0)
        chat_replies = _read_chat_replies(created_at)
        if chat_replies:
            # Attach the reply to the ticket as a note so it persists
            for reply in chat_replies:
                backlog.add_note(
                    ticket["id"],
                    f"[via chat, {reply['from']}] {reply['body']}",
                    author=reply["from"],
                )
            continue  # Now answered

        questions.append({
            "ticket_id": ticket["id"],
            "question": ticket["task"].replace("[QUESTION] ", ""),
            "project": ticket["project"],
            "asked_at": created_at,
            "age_minutes": (time.time() - created_at) / 60,
        })

    return questions


def _write_noticings_log(text: str) -> None:
    """Append to the foreman noticings log — half-formed observations.

    B2's 'beta deposit': things the foreman notices but can't name yet.
    Surfaced to strategy/direction lenses on future cycles.
    """
    log_path = Path(settings.artifacts_dir) / "foreman-noticings.jsonl"
    entry = {"timestamp": time.time(), "text": text}
    try:
        with open(log_path, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        logger.error("Failed to write noticings log")


def get_recent_noticings(limit: int = 10) -> list[dict]:
    """Return recent noticings for inclusion in state snapshot."""
    log_path = Path(settings.artifacts_dir) / "foreman-noticings.jsonl"
    if not log_path.is_file():
        return []
    entries = []
    for line in log_path.read_text().strip().splitlines():
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries[-limit:]


def get_recent_decisions(limit: int = 5) -> list[dict]:
    """Return recent decisions for inclusion in state snapshot."""
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


def _write_foreman_log(result: dict) -> None:
    """Append foreman result to the factory foreman log."""
    log_path = Path(settings.artifacts_dir) / "factory-foreman-log.jsonl"
    try:
        with open(log_path, "a") as f:
            f.write(json.dumps(result) + "\n")
    except OSError:
        logger.error("Failed to write foreman log")


def _write_decision_log(entry: dict) -> None:
    """Append to the foreman decision log — what was chosen and why.

    This is the [contra] trail: reviewing past decisions reveals where
    judgment was wrong. Separate from the foreman log (which tracks
    outcomes) — this tracks reasoning.
    """
    log_path = Path(settings.artifacts_dir) / "foreman-decisions.jsonl"
    try:
        with open(log_path, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        logger.error("Failed to write decision log")
