"""Retrieve candidate evidence for each plan item.

Deterministic and offline: paragraphs are scored by weighted token overlap, so
the same documents always produce the same candidate set — which is what makes
the verdict cache trustworthy.

The reverse pass answers the other half of the question: which completion
paragraphs correspond to no plan item at all (scope creep candidates).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Sequence

from .extract import PlanItem

TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[가-힣]+")
STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "is", "are", "was", "were",
    "for", "on", "with", "that", "this", "it", "as", "at", "by", "be", "not",
    "하는", "하고", "했다", "있다", "없다", "이다", "그리고", "그러나",
}
TOP_K = 5
EXTRA_THRESHOLD = 0.15
MIN_PARAGRAPH_WORDS = 4
# Scope-creep candidates come from prose about work, not from the scaffolding
# around it. Headings, front matter and link lines matched no plan item simply
# because they say nothing, and feeding them to the model made the answer noisy.
NOISE_PREFIXES = ("#", ">", "|", "```", "---", "===")
META_MARKERS = (
    "작성:", "작성일", "계획:", "코드 커밋", "검증:", "commit:", "author:", "date:",
    "다음 작업자", "첫 액션", "잔여", "후속", "todo", "next:",
)
MIN_EXTRA_WORDS = 8


@dataclass(frozen=True)
class Evidence:
    file: str
    line_start: int
    line_end: int
    quote: str
    score: float

    @property
    def key(self) -> str:
        """Location only — used for display and de-duplication."""
        return f"{self.file}:{self.line_start}-{self.line_end}"

    @property
    def content_key(self) -> str:
        """Location *and* content — used for cache fingerprints.

        Editing a paragraph without moving it must invalidate the verdict that
        was based on its previous wording, so the text is hashed in.
        """
        digest = hashlib.sha1(self.quote.encode("utf-8")).hexdigest()[:12]
        return f"{self.key}:{digest}"

    def as_dict(self) -> dict:
        return {
            "file": self.file,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "quote": self.quote,
        }


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in TOKEN_RE.findall(text) if t.lower() not in STOPWORDS]


def split_paragraphs(name: str, text: str) -> list[Evidence]:
    """Split a document into paragraph-sized evidence candidates."""
    out: list[Evidence] = []
    buffer: list[str] = []
    start = 1
    lines = text.replace("\r\n", "\n").split("\n")
    for no, line in enumerate(lines, start=1):
        if line.strip():
            if not buffer:
                start = no
            buffer.append(line)
            continue
        if buffer:
            out.append(_make_evidence(name, start, no - 1, buffer))
            buffer = []
    if buffer:
        out.append(_make_evidence(name, start, len(lines), buffer))
    return [e for e in out if len(e.quote.split()) >= MIN_PARAGRAPH_WORDS]


def _make_evidence(name: str, start: int, end: int, buffer: Sequence[str]) -> Evidence:
    return Evidence(file=name, line_start=start, line_end=end, quote="\n".join(buffer).strip(), score=0.0)


def _overlap(item_tokens: set[str], para_tokens: list[str]) -> float:
    """Fraction of the item's distinctive tokens present in the paragraph."""
    if not item_tokens:
        return 0.0
    para = set(para_tokens)
    return len(item_tokens & para) / len(item_tokens)


def rank_evidence(item: PlanItem, paragraphs: Sequence[Evidence], top_k: int = TOP_K) -> list[Evidence]:
    """Return the ``top_k`` paragraphs most likely to speak about ``item``."""
    item_tokens = set(tokenize(f"{item.title} {item.body}"))
    scored = [
        Evidence(e.file, e.line_start, e.line_end, e.quote, _overlap(item_tokens, tokenize(e.quote)))
        for e in paragraphs
    ]
    scored.sort(key=lambda e: (-e.score, e.file, e.line_start))
    return [e for e in scored[:top_k] if e.score > 0.0]


def is_prose(paragraph: Evidence) -> bool:
    """Does this paragraph make a statement, rather than structure a document?"""
    text = paragraph.quote.strip()
    if len(text.split()) < MIN_EXTRA_WORDS:
        return False
    first = text.splitlines()[0].strip()
    if first.startswith(NOISE_PREFIXES):
        return False
    # Front matter announces itself on the first line. Scanning the whole block
    # threw away real paragraphs because one later line happened to say
    # "검증:" — a multi-line bullet list is one block here.
    lowered = first.lower()
    return not any(marker in lowered for marker in META_MARKERS)


def extra_candidates(
    items: Sequence[PlanItem],
    paragraphs: Sequence[Evidence],
    threshold: float = EXTRA_THRESHOLD,
) -> list[Evidence]:
    """Completion prose that matches no plan item — scope creep candidates."""
    item_tokens = [set(tokenize(f"{i.title} {i.body}")) for i in items]
    out: list[Evidence] = []
    for para in paragraphs:
        if not is_prose(para):
            continue
        tokens = tokenize(para.quote)
        best = max((_overlap(t, tokens) for t in item_tokens), default=0.0)
        if best < threshold:
            out.append(Evidence(para.file, para.line_start, para.line_end, para.quote, best))
    return out
