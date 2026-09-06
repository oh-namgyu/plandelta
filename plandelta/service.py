"""Read-side services shared by the CLI and the HTTP server.

Anything the UI needs that is a question about stored state — which pairs exist,
which ones drifted since their last snapshot, how a pair's completion rate moved
over time — lives here, so the server stays a transport layer.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

from .compare import is_unchanged, load_documents
from .discovery import Pair, discover
from .errors import PlandeltaError
from .hashing import bundle_hash, plan_hash


def scan(root: Path, store, toolchain: str) -> list[dict]:
    """List every pair with its last known result and whether it drifted.

    ``dirty`` answers "would comparing this pair do any work?" — true when the
    documents changed, when the toolchain moved on, or when the pair was never
    compared at all.
    """
    rows = []
    for pair in discover(root).pairs:
        rows.append(_pair_row(pair, root, store, toolchain))
    return rows


def _pair_row(pair: Pair, root: Path, store, toolchain: str) -> dict:
    latest = store.latest_snapshot(pair.id)
    row = {
        **pair.as_dict(root),
        "snapshot_id": latest["id"] if latest else None,
        "created_at": latest["created_at"] if latest else None,
        "totals": json.loads(latest["totals"]) if latest else None,
        "rounds": len(store.snapshots(pair.id)),
    }
    try:
        plan_text, documents = load_documents(pair)
        row["dirty"] = not is_unchanged(
            store, pair, plan_hash(plan_text), bundle_hash(list(documents.items())), toolchain
        )
    except PlandeltaError as exc:
        row["dirty"] = False
        row["error"] = exc.as_dict()["error"]
    return row


def trend(store, pair_id: str) -> list[dict]:
    """Completion rate and coverage per round, oldest first."""
    out = []
    for index, row in enumerate(store.snapshots(pair_id), start=1):
        totals = json.loads(row["totals"])
        out.append(
            {
                "round": index,
                "snapshot_id": row["id"],
                "created_at": row["created_at"],
                "model": row["model"],
                "rate": totals.get("rate", 0.0),
                "coverage": totals.get("coverage", 0.0),
                "counts": totals.get("counts", {}),
            }
        )
    return out


def lineage_summary(items: Sequence[dict]) -> dict:
    """How this round's item set differs from the previous one."""
    counts = {"same": 0, "renamed": 0, "added": 0}
    for item in items:
        key = item.get("lineage", "same")
        counts[key] = counts.get(key, 0) + 1
    return counts
