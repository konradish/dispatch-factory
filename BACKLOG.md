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

## Polish
- [ ] Favicon
- [ ] Empty state for History when no sessions exist yet (new user)
- [ ] Loading skeleton for session detail slide-over
- [ ] Keyboard shortcut for session detail (arrow keys to navigate between sessions)
