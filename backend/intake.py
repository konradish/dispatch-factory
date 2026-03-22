"""Intake chat — LLM-assisted ticket structuring.

Takes a rough idea from the user, asks the LLM to structure it into
a well-specified ticket, returns the proposal for approval.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from config import settings

# Projects list for context
def _get_projects() -> list[str]:
    try:
        result = subprocess.run(
            [settings.dispatch_bin, "--projects"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            projects = []
            for line in result.stdout.splitlines():
                line = line.strip()
                if line and not line.startswith(("path", "test", "aliases", "local_url", "smoke", "deploy", "known", "stage")):
                    projects.append(line)
            return projects
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return []


def structure_ticket(raw_input: str, context: str = "") -> dict:
    """Send rough idea to LLM, get back a structured ticket proposal.

    Returns dict with: task, project, priority, flags, reasoning
    """
    projects = _get_projects()
    projects_str = ", ".join(projects) if projects else "unknown"

    prompt = f"""You are a factory intake assistant. The user has a rough idea for work they want done.
Structure it into a clear, dispatchable ticket.

Available projects: {projects_str}

User's input: "{raw_input}"
{f"Additional context: {context}" if context else ""}

Respond with ONLY a JSON object (no markdown, no explanation):
{{
  "task": "Clear, actionable task description (1-2 sentences, under 200 chars). Be specific about what to build/fix/change.",
  "project": "one of the available project names, or 'unknown' if unclear",
  "priority": "low|normal|high|urgent",
  "flags": [],
  "reasoning": "Brief explanation of why you structured it this way and any assumptions you made",
  "questions": ["List any clarifying questions if the input is ambiguous. Empty list if clear enough."]
}}

Rules:
- If the project is obvious from context, pick it
- Default priority to "normal" unless urgency is indicated
- flags can include: --no-merge, --plan, --no-plan
- Use --plan for complex multi-file tasks, --no-plan for simple fixes
- If the input is too vague to dispatch, put your questions in the questions field
- task should be self-contained — a worker reading only the task should know what to do"""

    # Use claude_reason pattern — Agent SDK subprocess
    env = dict(__import__("os").environ)
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("CLAUDECODE", None)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as script:
        script.write("""
import sys, json, pathlib

prompt_path = sys.argv[1]
out_path = sys.argv[2]
max_turns = int(sys.argv[3])

prompt = pathlib.Path(prompt_path).read_text()

from claude_agent_sdk import query, ClaudeAgentOptions, TextBlock
options = ClaudeAgentOptions(max_turns=max_turns)
result = query(prompt=prompt, options=options)

text = ""
for block in result.result:
    if isinstance(block, TextBlock):
        text += block.text

pathlib.Path(out_path).write_text(json.dumps({"response": text}))
""")
        script_path = script.name

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as pf:
        pf.write(prompt)
        prompt_path = pf.name

    out_path = tempfile.mktemp(suffix=".json")

    try:
        r = subprocess.run(
            ["uvx", "--with", "claude-agent-sdk", "python", script_path, prompt_path, out_path, "1"],
            capture_output=True, text=True, timeout=60, env=env,
        )

        if r.returncode != 0:
            return {
                "task": raw_input,
                "project": "unknown",
                "priority": "normal",
                "flags": [],
                "reasoning": f"LLM structuring failed (exit {r.returncode}). Using raw input as task.",
                "questions": [],
                "error": r.stderr[-300:] if r.stderr else "unknown error",
            }

        raw = Path(out_path).read_text() if Path(out_path).exists() else ""
        if not raw:
            return {
                "task": raw_input,
                "project": "unknown",
                "priority": "normal",
                "flags": [],
                "reasoning": "LLM returned empty response. Using raw input as task.",
                "questions": [],
            }

        result_data = json.loads(raw)
        response_text = result_data.get("response", "")

        # Parse the JSON from the response (may have markdown wrapping)
        response_text = response_text.strip()
        if response_text.startswith("```"):
            lines = response_text.split("\n")
            response_text = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])

        ticket = json.loads(response_text)

        # Validate required fields
        return {
            "task": str(ticket.get("task", raw_input))[:500],
            "project": str(ticket.get("project", "unknown")),
            "priority": str(ticket.get("priority", "normal")),
            "flags": list(ticket.get("flags", [])),
            "reasoning": str(ticket.get("reasoning", "")),
            "questions": list(ticket.get("questions", [])),
        }

    except json.JSONDecodeError:
        return {
            "task": raw_input,
            "project": "unknown",
            "priority": "normal",
            "flags": [],
            "reasoning": "Could not parse LLM response as JSON. Using raw input.",
            "questions": [],
        }
    except subprocess.TimeoutExpired:
        return {
            "task": raw_input,
            "project": "unknown",
            "priority": "normal",
            "flags": [],
            "reasoning": "LLM timed out. Using raw input as task.",
            "questions": [],
        }
    finally:
        for p in [script_path, prompt_path, out_path]:
            try:
                Path(p).unlink(missing_ok=True)
            except OSError:
                pass
