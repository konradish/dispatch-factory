# Contributing

dispatch-factory is currently coupled to one specific pipeline ([dispatch](https://github.com/konradish/dispatch-factory#how-it-works)). The architecture is harness-agnostic in principle but not yet in practice.

## What we'd love help with

**Adapter layer for other agent harnesses.** If you run Codex, Copilot Workspace, aider, or any AI coding agent in tmux sessions, you could make it work by writing artifacts in our format (see below). We'd accept PRs that:
- Add adapter examples for specific harnesses
- Generalize the session ID regex and artifact detection
- Make the pipeline definition configurable per-project

**UI improvements.** The frontend is React + Vite + Tailwind v4. Standard stuff.

**Pipeline engine.** The big one: replacing the hardcoded 4,242-line dispatch script with a data-driven pipeline runner that reads stage definitions from config. This is the path to an evolvable, LLM-steerable pipeline.

## Artifact Protocol

To make your agent harness work with dispatch-factory, write files to a single directory:

```
{session-id}.meta.json     # Required: session metadata
{session-id}.log           # Optional: worker output log
{session-id}-{stage}.json  # Optional: per-stage artifacts
{session-id}-result.md     # Optional: final report
```

### Session ID format
```
{type}-{project}-{timestamp}
```
- type: `worker`, `deploy`, `validate` (or extend the regex)
- project: lowercase alphanumeric with hyphens
- timestamp: any numeric identifier (we use HHMM)

Example: `worker-myapp-1430`

### meta.json (minimum viable)
```json
{
  "task": "Add user authentication",
  "project": "myapp",
  "status": "running",
  "started_at": 1774185093
}
```

### Stage artifacts
Any JSON file matching `{session-id}-{stagename}.json` will be detected. Known stage names get rich rendering:

| Stage | Expected fields | Rendered as |
|-------|----------------|-------------|
| `planner` | `scope`, `steps`, `risks` | Checklist |
| `reviewer` | `verdict`, `feedback` | Verdict badge + text |
| `verifier` | `status`, `stages` | Pass/fail table |
| `healer` | `action`, `diagnosis` | Yellow alert |
| `monitor` | any | Key-value display |

Unknown stage names render as raw JSON.

### tmux integration
Name your tmux sessions to match the session ID. The dashboard detects active workers by checking `tmux list-panes` for sessions with a non-shell process running.

### Terminal streaming
Install [ttyd](https://github.com/tsl0922/ttyd) and enable in config. The dashboard spawns `ttyd -R` per session for read-only browser terminal access.

## Dev setup

```bash
git clone https://github.com/konradish/dispatch-factory.git
cd dispatch-factory
make install
cp .dispatch-factory.example.toml .dispatch-factory.toml
# Edit config: point artifacts_dir at your agent's output directory
make dev
# Open http://localhost:5174
```

## Code style
- Python: `ruff check` (F/E9 rules only)
- TypeScript: strict mode, no external component libraries
- Keep dependencies minimal — every dep is attack surface
