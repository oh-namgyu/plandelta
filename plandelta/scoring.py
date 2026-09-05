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
        }


def summarize(verdicts: Sequence[Verdict], extras: Sequence[ExtraFinding] = ()) -> Totals:
    counts = {status: 0 for status in POINTS}
    for verdict in verdicts:
        counts[verdict.status] = counts.get(verdict.status, 0) + 1
    counts["extra"] = len(extras)

    scored = [v for v in verdicts if v.status in SCORED_STATUSES]
    points = sum(v.points for v in scored)
    max_points = len(scored) * FULL_CREDIT
    rate = 0.0 if not max_points else max(0.0, min(100.0, 100.0 * points / max_points))

    judged = [v for v in verdicts if v.status != "error"]
    coverage = 0.0 if not judged else 100.0 * len(scored) / len(judged)

    return Totals(
        counts=counts,
        rate=round(rate, 1),
        points=points,
        max_points=max_points,
        bonus=sum(v.points - FULL_CREDIT for v in verdicts if v.status == "exceeded"),
        penalty=sum(v.points for v in verdicts if v.status == "missed"),
        coverage=round(coverage, 1),
        scope_creep=len(extras),
    )
