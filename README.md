# dispatch-factory

A real-time control plane for autonomous SDLC pipelines. Watch AI coding agents work, dispatch new tasks, and stream live terminal sessions — all from your browser.

![Active workers with terminal](https://github.com/user-attachments/assets/placeholder-factory-screenshot.png)

## What is this?

If you run AI coding agents (Claude Code, Cursor, Copilot, etc.) through a pipeline — planning, coding, reviewing, deploying — you need a way to see what's happening. `dispatch-factory` is that dashboard.

It implements the **Software Factory** pattern: a deterministic workflow pipeline wrapping non-deterministic LLM workers, with a human control plane on top.

```
You (browser) → dispatch-factory → dispatch CLI → tmux sessions → AI workers
```

### Features

- **Pipeline view** — See active workers with project, task description, and pipeline stage progress
- **Ticket creation** — Dispatch new tasks from the browser with project selection and flags
- **Live terminal** — Stream worker tmux sessions directly in the browser via ttyd
- **Auto-attach** — Dispatching a ticket automatically opens its terminal view
- **Read-only by default** — Controls and terminal input are gated behind explicit config flags

## Architecture

Three layers, alternating determinism:

```
Non-deterministic:  Human operator (you, via the dashboard)
  └─ Deterministic:  dispatch pipeline (Planner → Worker → Reviewer → Verifier → Monitor → Reporter)
       └─ Non-deterministic:  LLM workers (Claude Code, etc.)
```

The dashboard reads artifacts from the dispatch pipeline's output directory, cross-references with active tmux sessions, and renders the state in real-time.

### Stack

| Layer | Technology |
|-------|-----------|
| Frontend | React 19 + Vite + Tailwind v4 |
| Backend | FastAPI + uvicorn + watchfiles |
| Terminal | ttyd (WebSocket terminal) |
| Pipeline | [dispatch](https://github.com/your-org/dispatch) or any tmux-based runner |
| Package mgmt | uv (Python), npm (Node) |

## Quick Start

### Prerequisites

- Python 3.11+
- Node 18+
- [uv](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- [ttyd](https://github.com/tsl0922/ttyd/releases) (optional, for terminal embedding)
- A dispatch-like pipeline that runs workers in named tmux sessions

### Install

```bash
git clone https://github.com/konradodell/dispatch-factory.git
cd dispatch-factory
make install
```

### Configure

```bash
cp .dispatch-factory.example.toml .dispatch-factory.toml
```

Edit `.dispatch-factory.toml`:

```toml
[dispatch]
artifacts_dir = "~/.local/share/dispatch"   # Where your pipeline writes artifacts
dispatch_bin = "~/.local/bin/dispatch"       # Your dispatch CLI

[server]
host = "127.0.0.1"
port = 8420
enable_controls = true                      # Allow ticket creation / hold / kill

[terminal]
enabled = true                              # Requires ttyd installed
allow_input = false                         # Read-only terminal by default
```

### Run

```bash
make dev
```

Open http://localhost:5174

## How It Works

### Pipeline Detection

The backend scans your artifacts directory for files matching the pattern `{session-id}-{artifact}.json`:

```
worker-myproject-1430.log
worker-myproject-1430.prompt
worker-myproject-1430-planner.json
worker-myproject-1430-reviewer.json
worker-myproject-1430-verifier.json
worker-myproject-1430-result.md
```

Session IDs must match: `(worker|deploy|validate)-{project}-{timestamp}`

### Active Session Detection

Sessions appear as "active" only when they have a live process in tmux (not just a shell). The backend runs `tmux list-panes` and filters out sessions where the current command is `zsh`/`bash`/`sh`.

### Terminal Streaming

When you click "Attach Terminal", the backend spawns a `ttyd` instance pointed at the worker's tmux session:

```
Browser (xterm.js) ←WebSocket→ ttyd ←pty→ tmux attach -t worker-myproject-1430
```

Read-only by default (`ttyd -R`). Each session gets a dynamic port from the configured range.

### Ticket Creation

`POST /api/tickets` shells out to your dispatch CLI with subprocess array args (no string interpolation). Project names are validated against `^[a-z][a-z0-9-]*$`, flags against an allowlist.

## Security

This is a **local-first** tool. It's designed to run on the same machine as your pipeline.

- Binds to `127.0.0.1` only
- Controls disabled by default (`enable_controls = false`)
- Terminal read-only by default (`ttyd -R`)
- All subprocess calls use array args (no shell injection)
- Session IDs regex-validated, flags allowlisted
- No telemetry, no analytics, no external calls
- CORS locked to localhost origins

## Adapting to Your Pipeline

dispatch-factory doesn't require a specific pipeline tool. It needs:

1. **Named tmux sessions** matching `(worker|deploy|validate)-{project}-{timestamp}`
2. **Artifact files** in a directory, prefixed with the session ID
3. **A CLI** that accepts `"{task}" --project {name}` to create new work

If your pipeline writes JSON artifacts and runs workers in tmux, this dashboard will work.

## Background

This project implements [Jess Martin's Software Factory pattern](https://x.com/jessmartin) — the idea that deterministic workflows wrapping non-deterministic LLMs aren't scaffolding to discard, they're the control plane humans need to monitor and steer autonomous software production.

## License

Apache 2.0 — see [LICENSE](LICENSE)
