# Add git commit hash to /api/health endpoint

## Summary

Added the running git commit hash to the `/api/health` endpoint response so the foreman can detect stale processes after merges. The commit hash is read once at module import time via `git rev-parse --short HEAD` and included as `git_commit` in the health response. This prevents the 8-cycle stale-process bug by enabling version comparison.

## Findings

### Before
The health endpoint (`backend/main.py:215-217`) returned only `{"status": "ok"}` — no way to detect whether the running process matches the current repo HEAD.

### Changes Made

1. **`_read_git_commit()` function** (`backend/main.py:163-172`): Reads `git rev-parse --short HEAD` with a 5-second timeout. Returns `"unknown"` on any failure (not in a git repo, git not installed, etc.). Uses array args per project security model — no shell injection risk.

2. **Module-level `_git_commit` variable** (`backend/main.py:175`): Captured once at import time, not on every request. This is correct because the commit hash can only change if the process is restarted.

3. **Health endpoint** (`backend/main.py:228-230`): Now returns `{"status": "ok", "git_commit": "abc1234"}`.

4. **Startup log** (`backend/main.py:185`): Logs the commit hash alongside other config values for debugging.

### Foreman Integration

The foreman can now:
```python
# Compare running version to repo HEAD
running = requests.get("http://localhost:8000/api/health").json()["git_commit"]
repo_head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
if running != repo_head:
    # Restart the factory process
```

### Test Impact
- All 42 previously-passing tests still pass
- 15 pre-existing failures (unrelated `backlog.settings` attribute errors) unchanged

## Recommendations

1. **Immediate**: Merge this PR — the change is minimal and self-contained
2. **Follow-up**: Add foreman logic to compare `git_commit` against repo HEAD and auto-restart stale processes
3. **Optional**: Consider adding `started_at` timestamp to the health endpoint for uptime monitoring

## References

- `backend/main.py:163-175` — `_read_git_commit()` and module-level capture
- `backend/main.py:185` — startup log line
- `backend/main.py:228-230` — updated health endpoint
- Project security model: subprocess uses array args, no shell interpolation (CLAUDE.md)
