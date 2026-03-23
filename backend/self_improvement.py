"""Factory self-improvement ratio — ensure dispatch-factory gets regular maintenance.

After every 8 product sessions dispatched, the next dispatch must target the
dispatch-factory project. This prevents the factory from neglecting its own
operational health while churning through product work.

State is persisted to a JSON file alongside other operator state.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from config import settings

logger = logging.getLogger("dispatch-factory.self-improvement")

STATE_FILE = "self-improvement-ratio.json"
PRODUCT_DISPATCHES_BEFORE_SELF_IMPROVEMENT = 8
FACTORY_PROJECT = "dispatch-factory"


def _state_path() -> Path:
    return Path(settings.artifacts_dir) / STATE_FILE


def _read_state() -> dict:
    path = _state_path()
    if not path.is_file():
        return _default_state()
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return _default_state()


def _default_state() -> dict:
    return {
        "product_dispatches_since_last_self_improvement": 0,
        "total_product_dispatches": 0,
        "total_self_improvement_dispatches": 0,
        "self_improvement_due": False,
        "last_self_improvement_at": None,
        "last_updated": time.time(),
    }


def _write_state(state: dict) -> None:
    path = _state_path()
    path.write_text(json.dumps(state, indent=2))


def get_state() -> dict:
    """Return current self-improvement ratio state."""
    return _read_state()


def is_self_improvement_due() -> bool:
    """Check if the next dispatch must be a self-improvement (dispatch-factory) ticket."""
    state = _read_state()
    return state["product_dispatches_since_last_self_improvement"] >= PRODUCT_DISPATCHES_BEFORE_SELF_IMPROVEMENT


def record_dispatch(project: str) -> list[str]:
    """Record a dispatch and update ratio tracking. Returns list of actions/messages."""
    actions: list[str] = []
    state = _read_state()

    if project == FACTORY_PROJECT:
        state["total_self_improvement_dispatches"] += 1
        state["product_dispatches_since_last_self_improvement"] = 0
        state["self_improvement_due"] = False
        state["last_self_improvement_at"] = time.time()
        actions.append("self-improvement: ratio counter reset (dispatch-factory ticket dispatched)")
        logger.info("Self-improvement dispatch recorded, counter reset")
    else:
        state["total_product_dispatches"] += 1
        state["product_dispatches_since_last_self_improvement"] += 1
        count = state["product_dispatches_since_last_self_improvement"]

        if count >= PRODUCT_DISPATCHES_BEFORE_SELF_IMPROVEMENT:
            state["self_improvement_due"] = True
            actions.append(
                f"self-improvement: due after {count} product dispatches — "
                "next dispatch must be a dispatch-factory ticket"
            )
            logger.info(
                "Self-improvement due: %d product dispatches since last factory ticket",
                count,
            )
        else:
            remaining = PRODUCT_DISPATCHES_BEFORE_SELF_IMPROVEMENT - count
            actions.append(f"self-improvement: {remaining} product dispatches until next factory ticket")

    state["last_updated"] = time.time()
    _write_state(state)
    return actions


def should_block_dispatch(project: str) -> bool:
    """Check if a non-factory dispatch should be blocked because self-improvement is due."""
    if project == FACTORY_PROJECT:
        return False
    return is_self_improvement_due()
