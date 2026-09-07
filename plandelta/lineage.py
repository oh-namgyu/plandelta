"""Tracking an item from one round to the next.

Without this a reworded promise looks like one deletion plus one addition, and
the trend chart shows churn where there was an edit.
"""

from __future__ import annotations

import difflib
from typing import Sequence

from .extract import PlanItem

RENAME_RATIO = 0.8


def label(items: Sequence[PlanItem], previous: dict[str, str]) -> dict[str, str]:
    """Label each item ``same`` / ``renamed`` / ``added`` against last round.

    With no previous round there is nothing to have been added *to*, so a first
    comparison reports every item as unchanged rather than as new.
    """
    if not previous:
        return {item.key: "same" for item in items}
    out: dict[str, str] = {}
    unmatched = dict(previous)
    for item in items:
        if item.key in previous:
            out[item.key] = "same"
            unmatched.pop(item.key, None)
            continue
        best_key, best_ratio = "", 0.0
        for key, title in unmatched.items():
            ratio = difflib.SequenceMatcher(None, item.title.lower(), title.lower()).ratio()
            if ratio > best_ratio:
                best_key, best_ratio = key, ratio
        if best_ratio >= RENAME_RATIO:
            out[item.key] = "renamed"
            unmatched.pop(best_key, None)
        else:
            out[item.key] = "added"
    return out
