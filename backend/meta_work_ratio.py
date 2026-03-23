"""Meta-work ratio circuit breaker — prevent dispatch-factory self-improvement spirals.

If more than 60% of the last 10 dispatched sessions are dispatch-factory tickets,
block further dispatch-factory work (except high-priority) until a product session
is dispatched. This prevents the factory from spiraling into indefinite
self-improvement when product backlogs are empty.
"""

from __future__ import annotations

import logging

import artifacts

logger = logging.getLogger("dispatch-factory.meta-work-ratio")

FACTORY_PROJECT = "dispatch-factory"
WINDOW_SIZE = 10
THRESHOLD = 0.6  # 60%


def _recent_dispatched_projects(window: int = WINDOW_SIZE) -> list[str]:
    """Return project names of the most recent dispatched sessions."""
    sessions = artifacts.list_sessions_with_timestamps()
    # Sessions are already sorted by recency (most recent first)
    return [s["project"] for s in sessions[:window]]


def get_ratio() -> dict:
    """Compute current meta-work ratio from recent session history."""
    projects = _recent_dispatched_projects()
    total = len(projects)
    if total == 0:
        return {
            "factory_count": 0,
            "total": 0,
            "ratio": 0.0,
            "blocked": False,
            "threshold": THRESHOLD,
            "window": WINDOW_SIZE,
        }

    factory_count = sum(1 for p in projects if p == FACTORY_PROJECT)
    ratio = factory_count / total

    return {
        "factory_count": factory_count,
        "total": total,
        "ratio": round(ratio, 2),
        "blocked": ratio > THRESHOLD,
        "threshold": THRESHOLD,
        "window": WINDOW_SIZE,
    }


def is_blocked(priority: str = "normal") -> bool:
    """Check if dispatch-factory work is blocked by meta-work ratio.

    Only urgent tickets (human-escalated) are exempt from the block.
    High-priority tickets are still subject to the ratio — auto-generated
    factory tickets often arrive as high-priority, which previously
    allowed them to bypass the breaker entirely.
    """
    if priority == "urgent":
        return False

    info = get_ratio()
    if info["blocked"]:
        logger.warning(
            "Meta-work ratio breaker: %d/%d recent sessions are dispatch-factory "
            "(%.0f%% > %.0f%% threshold) — blocking non-high-priority factory work",
            info["factory_count"],
            info["total"],
            info["ratio"] * 100,
            info["threshold"] * 100,
        )
    return info["blocked"]
