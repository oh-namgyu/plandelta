"""Turn plan items plus candidate evidence into verdicts.

Two safety rails matter more than the prompt wording:

1. document text is fenced and declared to be data, never instructions;
2. every quote the model returns is checked against the source document, and an
   item claimed as delivered without a surviving quote is demoted to
   ``unknown`` rather than believed.

``unknown`` is not ``missed``. Failing to find evidence is a retrieval failure,
and scoring it as a broken promise would quietly slander the plan's author.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from .errors import SchemaViolation
from .prompts import MAX_BODY_CHARS, MAX_QUOTE_CHARS, build_extra_prompt, build_prompt
from .extract import PlanItem
from .matcher import Evidence

STATUSES = ("exceeded", "done", "partial", "missed", "extra", "unknown", "error", "out_of_scope")
CLAIM_STATUSES = ("exceeded", "done")
SHORTFALL_STATUSES = ("partial", "missed")
POINTS = {
    "exceeded": 5, "done": 3, "partial": 1, "missed": -2,
    "extra": 0, "unknown": 0, "error": 0, "out_of_scope": 0,
}
SCORED_STATUSES = ("exceeded", "done", "partial", "missed")
BATCH_SIZE = 10  # measured: 15-item batches of prose time out at 60s
BATCH_CHAR_BUDGET = 16000  # measured: a 9-item prose batch above this times out at 120s
# The scope-creep prompt carries worked examples, so it needs a tighter budget
# than the verdict pass: seventeen candidates in one call timed out at 120s.
# Splitting keeps every candidate — an earlier cap silently dropped a real
# finding, which is the failure this whole tool exists to avoid.
EXTRA_BATCH_CHARS = 9000
# What made a seventeen-candidate call slow was not the prompt — 6.4KB — but the
# reply: three booleans and a sentence per candidate. Bounding the count bounds
# the output, and splitting keeps every candidate.
MAX_EXTRA_PER_BATCH = 8

@dataclass
class Verdict:
    item: PlanItem
    status: str
    reason: str = ""
    evidence: list[Evidence] = field(default_factory=list)
    fingerprint: str = ""
    cached: bool = False

    @property
    def points(self) -> int:
        return POINTS[self.status]

    def as_dict(self) -> dict:
        return {
            **self.item.as_dict(),
            "status": self.status,
            "points": self.points,
            "reason": self.reason,
            "evidence": [e.as_dict() for e in self.evidence],
            "cached": self.cached,
        }


@dataclass
class ExtraFinding:
    evidence: Evidence
    reason: str = ""

    def as_dict(self) -> dict:
        return {"status": "extra", "points": 0, "reason": self.reason, **self.evidence.as_dict()}


def extra_batches(candidates: Sequence[Evidence]) -> list[list[Evidence]]:
    """Split candidates into prompt-sized groups, dropping none of them."""
    out: list[list[Evidence]] = []
    batch: list[Evidence] = []
    size = 0
    for candidate in candidates:
        cost = len(candidate.quote[:MAX_QUOTE_CHARS])
        if batch and (
            len(batch) >= MAX_EXTRA_PER_BATCH or size + cost > EXTRA_BATCH_CHARS
        ):
            out.append(batch)
            batch, size = [], 0
        batch.append(candidate)
        size += cost
    if batch:
        out.append(batch)
    return out


def parse_json_object(text: str) -> dict:
    """Pull the JSON object out of a model reply, tolerating stray prose."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n|\n```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise SchemaViolation("model reply contained no JSON object")
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise SchemaViolation(f"model reply was not valid JSON: {exc}") from exc


def _normalize_for_match(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def verify_quote(quote: str, documents: dict[str, str]) -> bool:
    """A quote counts only if it really occurs in the cited bundle."""
    needle = _normalize_for_match(quote)
    if len(needle) < 12:
        return False
    return any(needle in _normalize_for_match(body) for body in documents.values())


def _clean_evidence(raw: Iterable, documents: dict[str, str]) -> list[Evidence]:
    out: list[Evidence] = []
    for entry in raw or []:
        if not isinstance(entry, dict):
            continue
        quote = str(entry.get("quote", ""))
        if not verify_quote(quote, documents):
            continue
        out.append(
            Evidence(
                file=str(entry.get("file", "")),
                line_start=int(entry.get("line_start") or 0),
                line_end=int(entry.get("line_end") or 0),
                quote=quote,
                score=1.0,
            )
        )
    return out


def verdicts_from_reply(
    reply: str, items: Sequence[PlanItem], documents: dict[str, str]
) -> dict[int, Verdict]:
    """Validate a batch reply into verdicts keyed by item index."""
    payload = parse_json_object(reply)
    rows = payload.get("verdicts")
    if not isinstance(rows, list):
        raise SchemaViolation("reply has no 'verdicts' array")
    out: dict[int, Verdict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        index = row.get("index")
        status = str(row.get("status", "")).lower()
        if not isinstance(index, int) or not 0 <= index < len(items) or status not in STATUSES:
            continue
        evidence = _clean_evidence(row.get("evidence"), documents)
        status, evidence, note = _apply_evidence_rules(status, evidence, row, documents)
        out[index] = Verdict(
            item=items[index], status=status,
            reason=(note + str(row.get("reason", "")))[:400], evidence=evidence,
        )
    return out


def _apply_evidence_rules(
    status: str, evidence: list[Evidence], row: dict, documents: dict[str, str]
) -> tuple[str, list[Evidence], str]:
    """Every non-neutral verdict must be anchored to a quote that survives checking.

    A claim of delivery needs supporting evidence; a claim of shortfall needs a
    quote that states the shortfall. Whatever cannot be anchored becomes
    ``unknown`` — an abstention a person can resolve — rather than a guess.
    """
    if status in CLAIM_STATUSES and not evidence:
        return "unknown", [], "no verifiable evidence for the claim; "
    if status in SHORTFALL_STATUSES:
        quote = str(row.get("shortfall_quote") or "")
        if not verify_quote(quote, documents):
            return "unknown", evidence, "no verifiable quote stating a shortfall; "
        anchor = Evidence(
            file=str(row.get("shortfall_file", "")), line_start=0, line_end=0,
            quote=quote, score=1.0,
        )
        return status, [anchor, *[e for e in evidence if e.quote != quote]], ""
    return status, evidence, ""


def extras_from_reply(
    reply: str, documents: dict[str, str], candidates: Sequence[Evidence] = ()
) -> list[ExtraFinding]:
    """Read per-candidate decisions and keep the paragraphs marked unplanned.

    The quote comes from the candidate we sent, not from the reply, so a finding
    can never rest on a paraphrase — and cannot be silently dropped because the
    model retyped the sentence slightly differently.
    """
    payload = parse_json_object(reply)
    rows = payload.get("candidates")
    if not isinstance(rows, list):
        raise SchemaViolation("reply has no 'candidates' array")
    findings: list[ExtraFinding] = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("unplanned"):
            continue
        index = row.get("index")
        if not isinstance(index, int) or not 0 <= index < len(candidates):
            continue
        findings.append(
            ExtraFinding(evidence=candidates[index], reason=str(row.get("reason", ""))[:400])
        )
    return findings


__all__ = [
    "BATCH_CHAR_BUDGET", "BATCH_SIZE", "CLAIM_STATUSES", "EXTRA_BATCH_CHARS",
    "ExtraFinding", "MAX_BODY_CHARS", "MAX_EXTRA_PER_BATCH", "MAX_QUOTE_CHARS", "POINTS",
    "SCORED_STATUSES",
    "SHORTFALL_STATUSES", "STATUSES", "Verdict", "build_extra_prompt", "build_prompt",
    "extra_batches", "extras_from_reply", "parse_json_object", "verdicts_from_reply",
    "verify_quote",
]
