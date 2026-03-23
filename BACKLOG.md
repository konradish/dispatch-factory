# dispatch-factory backlog

## Bugs
- [ ] Verifier `reason` field is truncated — dispatch cuts beginning of `gh pr merge` stderr output (dispatch bug, not factory)
- [ ] Vite HMR doesn't detect file changes on WSL Windows mounts — requires manual restart

## Features
- [ ] Direction vector input from the UI (currently display-only)
- [ ] Project filter on History and Factory Log views
- [ ] Auto-cleanup stale ttyd processes on backend shutdown
- [ ] Browser notification when worker enters healer (not just start/finish)
- [ ] Session detail: "Re-dispatch" button (dispatch same task again)
- [ ] Session detail: "View Log" button (tail the .log file)
- [ ] Dispatch runner output tee for real-time terminal streaming (dispatch change: cy -p output goes to tmux pty AND a clean log file)
- [ ] `make dev` as a single command users actually use (currently manual nohup)
- [ ] `/api/projects` should also return project metadata (smoke URLs, deploy status)

## Multi-Machine Agent Aggregation (GitHub #2)

**v1 — Foundation** (agent protocol + remote session discovery):
- [ ] Agent registration API (`POST /api/agents/register`, `GET /api/agents`, heartbeat + TTL expiry)
- [ ] Remote session discovery (proxy session list from registered agents, aggregate into unified response)
- [ ] Frontend: show remote sessions in Pipeline dashboard tagged with machine name
- [ ] Config: `[agents]` section in `.dispatch-factory.toml` (disabled by default)

**v2+ — Full vision** (deferred):
- [ ] Canvas layout: infinite 2D canvas with drag-and-drop terminal/widget arrangement
- [ ] Terminal relay: WebSocket proxy for remote terminal I/O
- [ ] Resource monitoring: CPU/memory/disk metrics from remote agents
- [ ] Fleet management: cross-machine dispatch, role-based grouping

## Polish
- [ ] Favicon
- [ ] Empty state for History when no sessions exist yet (new user)
- [ ] Loading skeleton for session detail slide-over
- [ ] Keyboard shortcut for session detail (arrow keys to navigate between sessions)
