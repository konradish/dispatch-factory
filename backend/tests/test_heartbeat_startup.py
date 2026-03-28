"""Tests for heartbeat startup completion-before-GC ordering.

Verifies that heartbeat_loop processes completions (via pipeline_runner)
BEFORE running zombie GC at startup, preventing the race condition where
sessions are marked abandoned before their -result.md artifacts are written.
"""

from __future__ import annotations

import asyncio
from unittest import mock

import heartbeat


def test_startup_processes_completions_before_gc() -> None:
    """Startup block must call scan_for_completions before _gc_zombie_sessions."""
    call_order: list[str] = []

    fake_completion = {"session_id": "test-123", "project": "myproject"}

    def _fake_scan() -> list[dict]:
        call_order.append("scan_for_completions")
        return [fake_completion]

    def _fake_process(completion: dict) -> list[str]:
        call_order.append("process_worker_completion")
        return [f"processed {completion['session_id']}"]

    def _fake_gc() -> list[str]:
        call_order.append("_gc_zombie_sessions")
        return []

    fake_module = mock.MagicMock()
    fake_module.scan_for_completions = _fake_scan
    fake_module.process_worker_completion = _fake_process

    with (
        mock.patch.dict(heartbeat._state, {"enabled": True, "interval_minutes": 1}),
        mock.patch.dict("sys.modules", {"pipeline_runner": fake_module}),
        mock.patch("heartbeat._gc_zombie_sessions", side_effect=_fake_gc),
    ):

        async def _run() -> None:
            # heartbeat_loop is infinite; cancel after startup completes
            task = asyncio.create_task(heartbeat.heartbeat_loop(interval=9999))
            # Yield control so the startup block runs before the first sleep
            await asyncio.sleep(0.1)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(_run())

    # The loop body may also run before cancellation — verify the startup
    # sequence (first 3 calls) has completions before GC.
    assert len(call_order) >= 3, f"Expected at least 3 calls, got: {call_order}"
    assert call_order[:3] == [
        "scan_for_completions",
        "process_worker_completion",
        "_gc_zombie_sessions",
    ], f"Expected completions before GC at startup, got: {call_order[:3]}"
