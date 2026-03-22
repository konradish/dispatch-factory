# dispatch-factory

Browser control plane for AI coding agents. See what's running, dispatch work, stream live terminals.

![Active workers with terminal](https://github.com/user-attachments/assets/83a87bce-af08-4e99-afe5-3cdf209cfaee)

## 30-second pitch

You run AI coding agents overnight. They plan, code, review, deploy. But you have no dashboard — just CLI commands and push notifications. dispatch-factory is the missing control plane.

**What you get:**
- **Active view** — live workers with task, project, pipeline progress, tmux badge
- **Create Ticket / Backlog** — dispatch immediately or queue for later
- **History + Factory Log** — what shipped, what failed, what the healer fixed
- **Session detail** — click any session for the full pipeline story (verdict, deploy status, healer diagnosis)
- **Pipeline Def** — visual flow diagram of your SDLC pipeline stages
- **Live terminal** — stream worker tmux sessions in the browser via ttyd
- **Heartbeat** — periodic health check, stuck detection, optional auto-dispatch from backlog

## Quick Start

```bash
git clone https://github.com/konradish/dispatch-factory.git
cd dispatch-factory
make install
cp .dispatch-factory.example.toml .dispatch-factory.toml
# Edit: point artifacts_dir at your agent's output directory
make dev
# Open http://localhost:5174
```

Requires: Python 3.11+, Node 18+, [uv](https://docs.astral.sh/uv/), [ttyd](https://github.com/tsl0922/ttyd/releases) (optional).

## How it works

The dashboard reads artifact files from a directory. Any agent harness that writes files in this format gets the dashboard:

```
worker-myapp-1430.log              # Worker output
worker-myapp-1430-planner.json     # Stage artifacts (any stage name)
worker-myapp-1430-reviewer.json
worker-myapp-1430-verifier.json
worker-myapp-1430-result.md        # Final report
```

Active workers are detected via `tmux list-panes` — only sessions with a live process (not bare shell) show as active.

Terminal streaming: click "Attach Terminal" → backend spawns `ttyd -R` → iframe embeds the live tmux session via WebSocket.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full artifact protocol.

## Architecture

Three layers of alternating determinism ([Jess Martin's Software Factory pattern](https://x.com/jessmartin)):

```
Non-deterministic:  Human operator (you, via the dashboard)
  └─ Deterministic:  Pipeline (Planner → Worker → Reviewer → Verifier → Monitor → Reporter)
       └─ Non-deterministic:  LLM workers (Claude Code, Codex, etc.)
```

| Layer | Stack |
|-------|-------|
| Frontend | React 19, Vite, Tailwind v4 |
| Backend | FastAPI, uvicorn, watchfiles |
| Terminal | ttyd (WebSocket) |
| Real-time | WebSocket push on artifact changes, falls back to polling |

## Security

Local-first. Not a SaaS.

- Binds to `127.0.0.1` only
- Controls disabled by default
- Terminal read-only by default (`ttyd -R`)
- Subprocess array args everywhere (no shell injection)
- No telemetry, no analytics, no external calls

## Current status

Works today with the [dispatch](https://github.com/konradish/dispatch-factory#how-it-works) pipeline. Adapting to other agent harnesses (Codex, Copilot, aider) requires writing artifacts in the expected format — see [CONTRIBUTING.md](CONTRIBUTING.md).

**Roadmap:** Evolvable pipeline (stage definitions as data, not code) → LLM operator agent that tunes the pipeline based on outcomes → fully autonomous factory with human steering from the dashboard.

## License

Apache 2.0
