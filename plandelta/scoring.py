"""KPI aggregation.

One number would lie. A plan whose evidence was never found would otherwise
score the same as one that was genuinely abandoned, and a single "exceeded"
could paper over three misses. So the summary always ships four numbers:
completion rate, bonus, penalty and evidence coverage.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .judge import POINTS, SCORED_STATUSES, ExtraFinding, Verdict

FULL_CREDIT = POINTS["done"]


@dataclass
class Totals:
    counts: dict[str, int]
    rate: float
    points: int
    max_points: int
    bonus: int
    penalty: int
    coverage: float
    scope_creep: int
    out_of_scope: int = 0

    def as_dict(self) -> dict:
        return {
            "rate": self.rate,
            "points": self.points,
            "max_points": self.max_points,
            "bonus": self.bonus,
            "penalty": self.penalty,
            "coverage": self.coverage,
            "counts": self.counts,
            "scope_creep": self.scope_creep,
            "out_of_scope": self.out_of_scope,
        }


def summarize(verdicts: Sequence[Verdict], extras: Sequence[ExtraFinding] = ()) -> Totals:
    rows = [{"status": v.status, "points": v.points} for v in verdicts]
    return summarize_rows(rows, len(extras))


def summarize_rows(rows: Sequence[dict], scope_creep: int = 0) -> Totals:
    """Totals from plain ``{status, points}`` rows.

    Stored snapshots and human overrides both arrive as rows rather than
    verdicts, and the headline numbers have to be recomputed from whatever the
    reader is actually looking at — otherwise a corrected item changes colour in
    the list while the percentage above it still quotes the model.
    """
    counts = {status: 0 for status in POINTS}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    counts["extra"] = scope_creep

    scored = [r for r in rows if r["status"] in SCORED_STATUSES]
    points = sum(int(r["points"]) for r in scored)
    max_points = len(scored) * FULL_CREDIT
    rate = 0.0 if not max_points else max(0.0, min(100.0, 100.0 * points / max_points))

    # Coverage answers "of the items this run was asked to judge, how many did
    # it settle?" — so items ruled outside the round's scope are not part of the
    # question, any more than engine failures are.
    judged = [r for r in rows if r["status"] not in ("error", "out_of_scope")]
    coverage = 0.0 if not judged else 100.0 * len(scored) / len(judged)

    return Totals(
        counts=counts,
        rate=round(rate, 1),
        points=points,
        max_points=max_points,
        out_of_scope=sum(1 for r in rows if r["status"] == "out_of_scope"),
        bonus=sum(int(r["points"]) - FULL_CREDIT for r in rows if r["status"] == "exceeded"),
        penalty=sum(int(r["points"]) for r in rows if r["status"] == "missed"),
        coverage=round(coverage, 1),
        scope_creep=scope_creep,
    )
