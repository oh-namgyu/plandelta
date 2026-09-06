"""Human verdicts layered over machine ones.

The model's verdict is never edited. An override is a later, separate statement
about the same item, so the machine judgement stays readable and auditable and a
person can disagree with it on the record. Superseded overrides are revoked, not
deleted, so the history of who decided what remains.

This is what makes ``unknown`` useful rather than merely honest: the tool
abstains, a person settles it, and the settlement survives the next comparison.
"""

from __future__ import annotations

import sqlite3
from typing import Iterable, Sequence

from .judge import POINTS, STATUSES

OVERRIDABLE = tuple(status for status in STATUSES if status not in ("extra", "error"))


def validate(status: str) -> str:
    if status not in OVERRIDABLE:
        raise ValueError(f"cannot override to {status!r} (expected one of {', '.join(OVERRIDABLE)})")
    return status


def set_override(
    conn: sqlite3.Connection, pair_id: str, item_key: str, status: str, reason: str, author: str
) -> int:
    """Revoke any active override for the item, then record the new one."""
    validate(status)
    conn.execute(
        "UPDATE overrides SET revoked_at = datetime('now')"
        " WHERE pair_id = ? AND item_key = ? AND revoked_at IS NULL",
        (pair_id, item_key),
    )
    cursor = conn.execute(
        "INSERT INTO overrides (pair_id, item_key, status, reason, author, created_at)"
        " VALUES (?, ?, ?, ?, ?, datetime('now'))",
        (pair_id, item_key, status, reason, author),
    )
    return int(cursor.lastrowid)


def revoke(conn: sqlite3.Connection, pair_id: str, item_key: str) -> bool:
    cursor = conn.execute(
        "UPDATE overrides SET revoked_at = datetime('now')"
        " WHERE pair_id = ? AND item_key = ? AND revoked_at IS NULL",
        (pair_id, item_key),
    )
    return cursor.rowcount > 0


def active(conn: sqlite3.Connection, pair_id: str) -> dict[str, dict]:
    rows = conn.execute(
        "SELECT * FROM overrides WHERE pair_id = ? AND revoked_at IS NULL ORDER BY id", (pair_id,)
    )
    return {
        row["item_key"]: {
            "status": row["status"], "reason": row["reason"],
            "author": row["author"], "created_at": row["created_at"],
        }
        for row in rows
    }


def history(conn: sqlite3.Connection, pair_id: str) -> list[dict]:
    return [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM overrides WHERE pair_id = ? ORDER BY id", (pair_id,)
        )
    ]


def apply(items: Sequence[dict], overrides: dict[str, dict]) -> list[dict]:
    """Return item rows with active overrides applied, keeping the original.

    Each overridden row keeps ``machine_status``/``machine_points`` so the UI can
    show what the model said and what a person decided instead.
    """
    out: list[dict] = []
    for item in items:
        override = overrides.get(item.get("item_key", ""))
        if not override:
            out.append({**item, "override": None})
            continue
        out.append({
            **item,
            "machine_status": item["status"],
            "machine_points": item["points"],
            "status": override["status"],
            "points": POINTS.get(override["status"], 0),
            "override": override,
        })
    return out


def count(items: Iterable[dict]) -> int:
    return sum(1 for item in items if item.get("override"))
