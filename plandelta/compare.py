"""Compare one pair: plan items in, verdicts and totals out.

The expensive part (the model) is guarded twice. A pair whose documents are
unchanged does not produce a new snapshot at all, and within a pair only items
whose fingerprint changed are sent to the engine — everything else is served
from cache.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from . import judge, lineage, prose
from .discovery import Pair
from .engines import Engine
from .errors import DocTooLarge, EngineTimeout, PlandeltaError, SchemaViolation
from .extract import PlanItem, extract_items
from .hashing import bundle_hash, item_fingerprint, plan_hash, read_text
from .matcher import Evidence, extra_candidates, rank_evidence, split_paragraphs
from .scoring import Totals, summarize

if TYPE_CHECKING:  # imported for annotations only, so no import cycle
    from .store import Store


SCHEMA = 1
MAX_PLAN_BYTES = 200 * 1024
MAX_BUNDLE_BYTES = 500 * 1024


@dataclass
class ComparisonResult:
    pair: Pair
    plan_hash: str
    bundle_hash: str
    verdicts: list[judge.Verdict]
    extras: list[judge.ExtraFinding]
    totals: Totals
    engine: dict
    lineage: dict[str, str] = field(default_factory=dict)
    llm_calls: int = 0
    snapshot_id: int | None = None
    unchanged: bool = False
    dropped_headings: int = 0

    def as_dict(self, root: Path) -> dict:
        return {
            "schema": SCHEMA,
            "pair": self.pair.as_dict(root),
            "plan_hash": self.plan_hash,
            "bundle_hash": self.bundle_hash,
            "generated_by": {**self.engine, "llm_calls": self.llm_calls},
            "dropped_headings": self.dropped_headings,
            "snapshot_id": self.snapshot_id,
            "unchanged": self.unchanged,
            "totals": self.totals.as_dict(),
            "items": [
                {**v.as_dict(), "lineage": self.lineage.get(v.item.key, "same")} for v in self.verdicts
            ],
            "extras": [x.as_dict() for x in self.extras],
        }


def load_documents(pair: Pair) -> tuple[str, dict[str, str]]:
    """Read the pair off disk, refusing documents beyond the size budget."""
    plan_text = read_text(pair.plan)
    if len(plan_text.encode("utf-8")) > MAX_PLAN_BYTES:
        raise DocTooLarge(f"{pair.plan.name} exceeds {MAX_PLAN_BYTES} bytes")
    documents = {path.name: read_text(path) for path in pair.done}
    total = sum(len(text.encode("utf-8")) for text in documents.values())
    if total > MAX_BUNDLE_BYTES:
        raise DocTooLarge(f"bundle for {pair.id} exceeds {MAX_BUNDLE_BYTES} bytes")
    return plan_text, documents


def _paragraphs(documents: dict[str, str]) -> list[Evidence]:
    out: list[Evidence] = []
    for name, text in documents.items():
        out.extend(split_paragraphs(name, text))
    return out


def _judge_batch(
    engine: Engine, batch: Sequence[PlanItem], evidence: dict[str, list[Evidence]],
    documents: dict[str, str],
) -> dict[int, judge.Verdict]:
    """One batch, with a single retry — timeouts and malformed replies both recover."""
    for attempt in (1, 2):
        try:
            reply = engine.complete(judge.build_prompt(batch, evidence))
            return judge.verdicts_from_reply(reply, batch, documents)
        except (EngineTimeout, SchemaViolation):
            if attempt == 2:
                raise
    return {}  # pragma: no cover - unreachable


def _batches(
    pending: Sequence[PlanItem], evidence: dict[str, list[Evidence]]
) -> list[list[PlanItem]]:
    """Split by prompt size, not just item count.

    Measured: a nine-item batch from a prose plan (whole sections as item bodies)
    timed out twice at 120s, while ten short checkbox items finish in seconds.
    Item count alone is the wrong unit.
    """
    out: list[list[PlanItem]] = []
    batch: list[PlanItem] = []
    size = 0
    for item in pending:
        cost = len(item.body[: judge.MAX_BODY_CHARS]) + sum(
            len(e.quote[: judge.MAX_QUOTE_CHARS]) for e in evidence.get(item.key, [])
        )
        if batch and (len(batch) >= judge.BATCH_SIZE or size + cost > judge.BATCH_CHAR_BUDGET):
            out.append(batch)
            batch, size = [], 0
        batch.append(item)
        size += cost
    if batch:
        out.append(batch)
    return out


def _judge_items(
    engine: Engine, pending: Sequence[PlanItem], evidence: dict[str, list[Evidence]],
    documents: dict[str, str],
) -> tuple[dict[str, judge.Verdict], int]:
    """Judge uncached items in batches; a failed batch degrades to ``error``."""
    out: dict[str, judge.Verdict] = {}
    calls = 0
    for batch in _batches(pending, evidence):
        calls += 1
        try:
            got = _judge_batch(engine, batch, evidence, documents)
        except PlandeltaError as exc:
            for item in batch:
                out[item.key] = judge.Verdict(item=item, status="error", reason=str(exc)[:200])
            continue
        for index, item in enumerate(batch):
            out[item.key] = got.get(index) or judge.Verdict(
                item=item, status="unknown", reason="model returned no verdict for this item"
            )
    return out, calls


def _find_extras(
    engine: Engine, items: Sequence[PlanItem], paragraphs: Sequence[Evidence],
    documents: dict[str, str],
) -> tuple[list[judge.ExtraFinding], int]:
    candidates = extra_candidates(items, paragraphs)
    if not candidates:
        return [], 0
    findings: list[judge.ExtraFinding] = []
    calls = 0
    for batch in judge.extra_batches(candidates):
        calls += 1
        try:
            reply = engine.complete(judge.build_extra_prompt(items, batch))
            findings.extend(judge.extras_from_reply(reply, documents, batch))
        except PlandeltaError:
            continue
    return findings, calls


def _fingerprints(
    items: Sequence[PlanItem], evidence: dict[str, list[Evidence]], engine: Engine
) -> dict[str, str]:
    return {
        item.key: item_fingerprint(
            item_body=item.body,
            evidence_keys=[e.content_key for e in evidence[item.key]],
            engine_id=engine.info.id,
            model_id=engine.info.model_id,
        )
        for item in items
    }


def _load_cached(
    items: Sequence[PlanItem], fingerprints: dict[str, str], store: Store | None, force: bool
) -> tuple[dict[str, judge.Verdict], list[PlanItem]]:
    """Split items into "already judged" and "needs the model"."""
    verdicts: dict[str, judge.Verdict] = {}
    pending: list[PlanItem] = []
    for item in items:
        cached = store.cached_verdict(fingerprints[item.key]) if store and not force else None
        if cached is None:
            pending.append(item)
            continue
        verdicts[item.key] = judge.Verdict(
            item=item, status=cached.status, reason=cached.reason, evidence=cached.evidence,
            cached=True,
        )
    return verdicts, pending


def compare_pair(
    pair: Pair,
    engine: Engine,
    store: Store | None = None,
    *,
    force: bool = False,
    find_extras: bool = True,
    classify_prose: bool = True,
) -> ComparisonResult:
    """Run one comparison, reusing cached verdicts wherever inputs are unchanged."""
    plan_text, documents = load_documents(pair)
    p_hash = plan_hash(plan_text)
    b_hash = bundle_hash([(name, text) for name, text in documents.items()])

    items = extract_items(plan_text)
    scaffolding = (
        prose.scaffolding_keys(items, engine, store, p_hash) if classify_prose else set()
    )
    in_scope = [
        i for i in items if i.key not in scaffolding and pair.in_scope(i.section, i.title)
    ]
    paragraphs = _paragraphs(documents)
    evidence = {item.key: rank_evidence(item, paragraphs) for item in in_scope}
    fingerprints = _fingerprints(in_scope, evidence, engine)
    verdicts, pending = _load_cached(in_scope, fingerprints, store, force)
    # Items the round was never meant to answer for cost nothing and accuse
    # nobody: no model call, no score, counted on their own.
    judged_keys = {i.key for i in in_scope}
    for item in items:
        if item.key in judged_keys:
            continue
        reason = (
            "reads as document scaffolding rather than a promise"
            if item.key in scaffolding
            else "outside the declared scope of this round"
        )
        verdicts[item.key] = judge.Verdict(item=item, status="out_of_scope", reason=reason)

    judged, calls = _judge_items(engine, pending, evidence, documents) if pending else ({}, 0)
    verdicts.update(judged)
    extras, extra_calls = ([], 0)
    if find_extras and pending:
        extras, extra_calls = _find_extras(engine, in_scope or items, paragraphs, documents)

    ordered = [verdicts[item.key] for item in items if item.key in verdicts]
    for verdict in ordered:
        # Out-of-scope items were never fingerprinted; leaving it blank also
        # keeps them out of the verdict cache, which is what we want.
        verdict.fingerprint = fingerprints.get(verdict.item.key, "")

    lineage_map = lineage.label(items, store.previous_items(pair.id) if store else {})
    return ComparisonResult(
        pair=pair, plan_hash=p_hash, bundle_hash=b_hash, verdicts=ordered, extras=list(extras),
        totals=summarize(ordered, extras), engine=engine.info.as_dict(),
        lineage=lineage_map, llm_calls=calls + extra_calls,
        dropped_headings=len(scaffolding),
    )


def is_unchanged(
    store: Store | None, pair: Pair, p_hash: str, b_hash: str, toolchain: str
) -> bool:
    """True when the newest snapshot describes these documents *and* this toolchain.

    The toolchain check is what makes the shortcut safe: same documents judged by
    a different prompt, extractor or model is not the same answer.
    """
    latest = store.latest_snapshot(pair.id) if store else None
    if not latest:
        return False
    if store.snapshot_has_errors(latest["id"]):
        # A timeout is not an answer. Freezing a failed round until the documents
        # happen to change would make a transient outage permanent.
        return False
    return (
        latest["plan_hash"] == p_hash
        and latest["bundle_hash"] == b_hash
        and (latest["toolchain"] or "") == toolchain
    )
