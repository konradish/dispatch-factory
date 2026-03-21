# X Post (thread)

## Tweet 1 (main)

I built a factory control plane for AI coding agents in one afternoon.

Dispatch tasks from the browser. Watch workers in real-time. Stream live tmux sessions via WebSocket.

Open source (Apache 2.0): github.com/konradish/dispatch-factory

Inspired by @jessmartin's Software Factory pattern. Thread 🧵

## Tweet 2

The architecture is three layers of alternating determinism:

Human operator (dashboard)
  └─ Deterministic pipeline (plan → code → review → deploy)
       └─ Non-deterministic LLM workers (Claude Code)

The key insight: the pipeline isn't scaffolding to discard. It's the control plane humans need.

## Tweet 3

The killer feature: live terminal embedding.

Each worker runs in a tmux session. ttyd bridges it to the browser via WebSocket. Click "Attach Terminal" → you're watching Claude Code work in real-time.

One binary install. Zero config. Read-only by default.

## Tweet 4

Security from commit 1:

• localhost-only (127.0.0.1)
• Controls disabled by default
• Terminal read-only by default
• Subprocess array args (no shell injection)
• No telemetry, no analytics

This isn't a SaaS. It's YOUR factory floor.

## Tweet 5

Stack: FastAPI + React + Vite + Tailwind + ttyd

Reads filesystem artifacts — no database needed. Your pipeline's JSON files ARE the API.

Works with any tmux-based runner, not just my dispatch tool. Named sessions + artifact files = dashboard.

github.com/konradish/dispatch-factory
