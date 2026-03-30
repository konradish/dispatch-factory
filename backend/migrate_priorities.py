"""One-time migration: normalize P0/P1/P2/P3 priority aliases to canonical values."""

from __future__ import annotations

import db

_PRIORITY_MAP = {'P0': 'urgent', 'P1': 'high', 'P2': 'normal', 'P3': 'low'}


def migrate():
    with db.get_conn() as conn:
        for alias, canonical in _PRIORITY_MAP.items():
            conn.execute(
                "UPDATE tickets SET priority = ? WHERE priority = ?",
                (canonical, alias),
            )


if __name__ == "__main__":
    migrate()
    print("Priority migration complete.")
