# dispatch-factory

Real-time control plane for the dispatch SDLC pipeline. Renders pipeline state, embeds worker terminal sessions, and provides human steering controls.

## Architecture

- **Backend:** FastAPI (Python, uv-managed) — reads dispatch artifacts from filesystem, exposes REST + WebSocket
- **Frontend:** React + Vite + Tailwind — renders pipeline state, ticket creation, terminal views
- **Terminal:** ttyd (system package) — bridges tmux worker sessions to browser via WebSocket

## Security Model

- Binds to `127.0.0.1` only (local-first, not a SaaS)
- Read-only by default (`enable_controls = false` in config)
- Terminal read-only by default (`ttyd -R`)
- No secrets in repo — config in `.dispatch-factory.toml` (gitignored)
- Subprocess calls use array args, never string interpolation (prevents command injection)
- No telemetry, no analytics, no phone-home

## Development

```bash
make install    # Install backend + frontend deps
make dev        # Run backend + frontend in parallel
make lint       # Lint both
make test       # Test both
```

## Config

Copy `.dispatch-factory.example.toml` to `.dispatch-factory.toml` and adjust paths.

## Stack

- Python: `uv` (not pip, not venv)
- Node: npm
- Linting: ruff (Python), eslint (TypeScript)
- Backend framework: FastAPI + uvicorn
- Frontend framework: React 19 + Vite + Tailwind v4
