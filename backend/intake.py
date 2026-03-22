"""Intake chat — LLM-assisted ticket structuring.

Takes a rough idea from the user, asks the LLM to structure it into
one or more well-specified tickets, returns proposals for approval.

System prompt is loaded from intake-prompt.md (editable by the user).
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from config import settings

PROMPT_FILE = Path(__file__).parent / "intake-prompt.md"


def _get_project_details() -> str:
    """Get detailed project info from dispatch --projects."""
    try:
        result = subprocess.run(
            [settings.dispatch_bin, "--projects"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return "No project details available."


def _load_system_prompt() -> str:
    """Load system prompt from file, or use built-in default."""
    if PROMPT_FILE.is_file():
        return PROMPT_FILE.read_text()
    return _DEFAULT_PROMPT


_DEFAULT_PROMPT = """You are a factory intake assistant for an autonomous SDLC pipeline.
The user describes work they want done. Your job is to structure it into
clear, dispatchable tickets that a Claude Code worker can execute autonomously.

## Rules
- Each ticket must be self-contained — a worker reading ONLY the task should know what to do
- If the work naturally splits into multiple independent tasks, create multiple tickets
- If one ticket depends on another, note it in the reasoning
- Default priority to "normal" unless urgency is indicated
- flags can include: --no-merge (draft PR only), --plan (force planner), --no-plan (skip planner)
- Use --plan for complex multi-file tasks, --no-plan for simple fixes
- If the input is too vague to dispatch, ask clarifying questions
- Pick the most appropriate project for each ticket
- If a task spans multiple projects, create separate tickets per project

## Output format
Respond with ONLY a JSON object (no markdown fences, no explanation outside the JSON):
{
  "tickets": [
    {
      "task": "Clear, actionable description (1-3 sentences, under 300 chars)",
      "project": "project-name",
      "priority": "low|normal|high|urgent",
      "flags": [],
      "related_repos": []
    }
  ],
  "reasoning": "Brief explanation of how you decomposed the work and any assumptions",
  "questions": ["Clarifying questions if input is ambiguous. Empty list if clear enough."]
}
"""


def structure_tickets(raw_input: str, context: str = "") -> dict:
    """Send rough idea to LLM, get back structured ticket proposals.

    Returns dict with: tickets (list), reasoning, questions
    """
    project_details = _get_project_details()
    system_prompt = _load_system_prompt()

    prompt = f"""{system_prompt}

## Available projects
{project_details}

## User's request
"{raw_input}"
{f'''
## Conversation context
{context}''' if context else ""}
"""

    # Use claude_reason pattern — Agent SDK subprocess
    env = dict(__import__("os").environ)
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("CLAUDECODE", None)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as script:
        script.write("""
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
""")
        script_path = script.name

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as pf:
        pf.write(prompt)
        prompt_path = pf.name

    out_path = tempfile.mktemp(suffix=".json")

    fallback = {
        "tickets": [{"task": raw_input, "project": "unknown", "priority": "normal", "flags": [], "related_repos": []}],
        "reasoning": "",
        "questions": [],
    }

    try:
        r = subprocess.run(
            ["uvx", "--with", "claude-agent-sdk", "python", script_path, prompt_path, out_path, "1"],
            capture_output=True, text=True, timeout=90, env=env,
        )

        if r.returncode != 0:
            fallback["reasoning"] = f"LLM failed (exit {r.returncode}). Using raw input."
            fallback["error"] = r.stderr[-300:] if r.stderr else "unknown"
            return fallback

        raw = Path(out_path).read_text() if Path(out_path).exists() else ""
        if not raw:
            fallback["reasoning"] = "LLM returned empty response."
            return fallback

        result_data = json.loads(raw)
        response_text = result_data.get("response", "").strip()

        # Strip markdown code fences if present
        if response_text.startswith("```"):
            lines = response_text.split("\n")
            response_text = "\n".join(lines[1:-1] if lines[-1].strip().startswith("```") else lines[1:])

        parsed = json.loads(response_text)

        # Normalize — support both old single-ticket and new multi-ticket format
        if "tickets" in parsed:
            tickets = parsed["tickets"]
        elif "task" in parsed:
            tickets = [parsed]
        else:
            tickets = []

        # Validate each ticket
        clean_tickets = []
        for t in tickets:
            clean_tickets.append({
                "task": str(t.get("task", ""))[:500],
                "project": str(t.get("project", "unknown")),
                "priority": str(t.get("priority", "normal")),
                "flags": list(t.get("flags", [])),
                "related_repos": list(t.get("related_repos", [])),
            })

        return {
            "tickets": clean_tickets or fallback["tickets"],
            "reasoning": str(parsed.get("reasoning", "")),
            "questions": list(parsed.get("questions", [])),
        }

    except json.JSONDecodeError:
        fallback["reasoning"] = "Could not parse LLM response as JSON."
        return fallback
    except subprocess.TimeoutExpired:
        fallback["reasoning"] = "LLM timed out."
        return fallback
    finally:
        for p in [script_path, prompt_path, out_path]:
            try:
                Path(p).unlink(missing_ok=True)
            except OSError:
                pass


# Keep backward compat for single-ticket callers
def structure_ticket(raw_input: str, context: str = "") -> dict:
    """Legacy single-ticket interface."""
    result = structure_tickets(raw_input, context)
    ticket = result["tickets"][0] if result["tickets"] else {
        "task": raw_input, "project": "unknown", "priority": "normal", "flags": [],
    }
    return {**ticket, "reasoning": result["reasoning"], "questions": result["questions"]}
