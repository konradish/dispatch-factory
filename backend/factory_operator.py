"""Factory operator — LLM-driven heartbeat reasoning with rotating lenses.

Each heartbeat tick, the operator picks the next lens in rotation,
builds a state snapshot, calls claude_reason, and executes approved actions.

Prompts live in operator-prompts/ and are editable via the UI.
"""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
import time
from pathlib import Path

import artifacts
import backlog
import circuit_breaker
import factory_idle_mode
import meta_work_ratio
from config import settings

logger = logging.getLogger("dispatch-factory.operator")

PROMPTS_DIR = Path(__file__).parent / "operator-prompts"

# Rotation state
_rotation_index = 0
_last_result: dict = {}


def list_lenses() -> list[dict]:
    """List all available operator lenses (prompt files)."""
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
    """Build a text snapshot of factory state for the operator."""
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
    dispatched = backlog.list_tickets(status="dispatched")

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

    return f"""## Factory State Snapshot

### Active Workers ({len(active)})
{active_str}

### Recent Sessions (last 20)
{json.dumps(recent_summary, indent=2)}

### Backlog
Pending: {len(pending)} tickets
{json.dumps(pending, indent=2) if pending else "Empty."}

In-flight: {len(dispatched)} tickets
{json.dumps(dispatched, indent=2) if dispatched else "None."}

### Aggregate Stats
{json.dumps(stats, indent=2)}

### Project Health
{health_json}

{neglected_line}
{broken_line}

### Direction Vector
{direction}
"""


def run_operator(lens_id: str | None = None) -> dict:
    """Run one operator cycle with the specified or next-in-rotation lens."""
    global _rotation_index, _last_result

    lenses = list_lenses()
    if not lenses:
        return {"error": "No operator lenses configured"}

    # Pick lens
    if lens_id:
        lens = get_lens(lens_id)
        if not lens:
            return {"error": f"Lens not found: {lens_id}"}
    else:
        lens = lenses[_rotation_index % len(lenses)]
        _rotation_index += 1

    # Build prompt
    state_snapshot = _build_state_snapshot()
    full_prompt = f"""{lens['prompt']}

{state_snapshot}
"""

    logger.info("Running operator with lens: %s", lens["id"])

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
    _write_operator_log(_last_result)

    return _last_result


def _call_llm(prompt: str) -> dict | None:
    """Call claude_reason and parse JSON response."""
    env = dict(__import__("os").environ)
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("CLAUDECODE", None)

    script_content = """
import asyncio, sys, json, pathlib

async def main():
    prompt_path = sys.argv[1]
    out_path = sys.argv[2]
    max_turns = int(sys.argv[3])

    prompt = pathlib.Path(prompt_path).read_text()

    from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, TextBlock
    options = ClaudeAgentOptions(max_turns=max_turns)

    result_parts = []
    result_fallback = None
    async for msg in query(prompt=prompt, options=options):
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, TextBlock):
                    result_parts.append(block.text)
        if type(msg).__name__ == 'ResultMessage' and hasattr(msg, 'result'):
            result_fallback = msg.result

    text = chr(10).join(result_parts) if result_parts else (result_fallback or '')
    pathlib.Path(out_path).write_text(json.dumps({"response": text}))

asyncio.run(main())
"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as sf:
        sf.write(script_content)
        script_path = sf.name
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as pf:
        pf.write(prompt)
        prompt_path = pf.name
    out_path = tempfile.mktemp(suffix=".json")

    try:
        r = subprocess.run(
            ["uvx", "--with", "claude-agent-sdk", "python", script_path, prompt_path, out_path, "1"],
            capture_output=True, text=True, timeout=90, env=env,
        )

        if r.returncode != 0:
            logger.error("Operator LLM failed: %s", r.stderr[-300:])
            return None

        raw = Path(out_path).read_text() if Path(out_path).exists() else ""
        if not raw:
            return None

        response_text = json.loads(raw).get("response", "").strip()
        if response_text.startswith("```"):
            lines = response_text.split("\n")
            response_text = "\n".join(lines[1:-1] if lines[-1].strip().startswith("```") else lines[1:])

        return json.loads(response_text)

    except (json.JSONDecodeError, subprocess.TimeoutExpired) as e:
        logger.error("Operator LLM error: %s", e)
        return None
    finally:
        for p in [script_path, prompt_path, out_path]:
            try:
                Path(p).unlink(missing_ok=True)
            except OSError:
                pass


def _execute_action(action: dict) -> dict:
    """Execute a single operator action. Returns result dict."""
    action_type = action.get("type", "do_nothing")

    if action_type == "do_nothing":
        return {"type": "do_nothing", "status": "ok"}

    elif action_type == "dispatch":
        # Factory idle mode: hard stop — no dispatches when all projects need human input
        if factory_idle_mode.is_idle():
            return {"type": "dispatch", "status": "blocked", "detail": "Factory idle mode — all active projects need human input"}
        ticket_id = action.get("ticket_id", "")
        ticket = next((t for t in backlog.list_tickets() if t["id"] == ticket_id), None)
        if not ticket:
            return {"type": "dispatch", "status": "error", "detail": f"Ticket {ticket_id} not found"}
        # Pre-dispatch guard: reject if project already has an in-flight ticket
        if backlog.has_inflight_ticket(ticket["project"]):
            return {"type": "dispatch", "status": "blocked", "detail": f"Project {ticket['project']} already has an in-flight ticket"}
        # Circuit breaker: block dispatches to tripped projects
        if circuit_breaker.is_project_blocked(ticket["project"]):
            return {"type": "dispatch", "status": "blocked", "detail": f"Circuit breaker tripped for {ticket['project']}"}
        # Meta-work ratio: block dispatch-factory work when ratio is too high
        if ticket["project"] == "dispatch-factory" and meta_work_ratio.is_blocked(ticket.get("priority", "normal")):
            return {"type": "dispatch", "status": "blocked", "detail": "Meta-work ratio exceeded — dispatch a product session first"}
        # Priority inversion guard: block lower-priority dispatch when capacity is at max
        import heartbeat as _hb
        active = artifacts.get_active_sessions()
        max_concurrent = _hb._state.get("max_concurrent", 3)
        if len(active) >= max_concurrent - 1 and backlog.has_eligible_higher_priority(ticket.get("priority", "normal")):
            return {"type": "dispatch", "status": "blocked", "detail": "Higher-priority tickets are pending; dispatch those first"}
        # Actually dispatch
        cmd = [settings.dispatch_bin, ticket["task"], "--project", ticket["project"]]
        cmd.extend(ticket.get("flags", []))
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if r.returncode == 0:
                import re
                match = re.search(r"session\s*:\s*([\w-]+)", r.stdout)
                session_id = match.group(1) if match else "unknown"
                backlog.mark_dispatched(ticket_id, session_id)
                return {"type": "dispatch", "status": "ok", "ticket_id": ticket_id, "session_id": session_id}
            return {"type": "dispatch", "status": "error", "detail": r.stderr[:200]}
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            return {"type": "dispatch", "status": "error", "detail": str(e)}

    elif action_type == "create_ticket":
        task = action.get("task", "")
        project = action.get("project", "unknown")
        priority = action.get("priority", "normal")
        if task:
            ticket = backlog.create_ticket(task, project, priority, source="operator")
            return {"type": "create_ticket", "status": "ok", "ticket_id": ticket["id"]}
        return {"type": "create_ticket", "status": "error", "detail": "No task provided"}

    elif action_type == "reprioritize":
        ticket_id = action.get("ticket_id", "")
        new_priority = action.get("priority", "normal")
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

    return {"type": action_type, "status": "unknown_action"}


def _write_operator_log(result: dict) -> None:
    """Append operator result to the factory operator log."""
    log_path = Path(settings.artifacts_dir) / "factory-operator-log.jsonl"
    try:
        with open(log_path, "a") as f:
            f.write(json.dumps(result) + "\n")
    except OSError:
        logger.error("Failed to write operator log")
