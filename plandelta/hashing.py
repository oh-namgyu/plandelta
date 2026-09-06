"""Normalization and stable hashing.

Every identity in plandelta comes from here:

- ``plan_hash`` / ``bundle_hash`` describe *documents* (snapshot metadata).
- ``item_key`` describes *which requirement* a row is, across revisions.
- ``item_fingerprint`` describes *whether a verdict may be reused* from cache.

The last one is deliberately wider than the document hashes: a verdict is only
reusable when the item text, its retrieved evidence, the prompt, the model and
the algorithm versions are all unchanged.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Iterable, Sequence

# Algorithm versions. Bump when behaviour changes in a way that invalidates
# previously cached verdicts.
EXTRACTOR_VERSION = "2"  # v2: leaf headings only in the prose fallback
MATCHER_VERSION = "1"
PROMPT_VERSION = "3"  # v3: abstain unless a shortfall quote anchors partial/missed
OUTPUT_SCHEMA = "2"  # v2: reply carries shortfall_quote

_EMPHASIS_RE = re.compile(r"[*_`~]+")
_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_WS_RE = re.compile(r"[ \t]+")


def normalize_text(text: str) -> str:
    """Normalize a document for hashing: LF endings, no trailing blanks."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    return "\n".join(lines).strip() + "\n"


def normalize_title(text: str) -> str:
    """Normalize a heading/item title: drop markup, collapse whitespace."""
    text = _LINK_RE.sub(r"\1", text)
    text = _EMPHASIS_RE.sub("", text)
    text = text.replace("[ ]", "").replace("[x]", "").replace("[X]", "")
    text = _WS_RE.sub(" ", text.replace("\n", " "))
    return text.strip().lower()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def plan_hash(text: str) -> str:
    return sha256_text(normalize_text(text))


def bundle_hash(files: Sequence[tuple[str, str]]) -> str:
    """Hash a completion bundle.

    ``files`` is a sequence of ``(relative_path, raw_text)``.  Paths are sorted
    so that file order never affects the hash, and both the path and the content
    take part so that adding/removing/renaming a file is visible.
    """
    parts = [
        f"{path}:{sha256_text(normalize_text(text))}\n"
        for path, text in sorted(files, key=lambda pair: pair[0])
    ]
    return sha256_text("".join(parts))


def item_key(section_path: str, title: str) -> str:
    """Stable identity of a plan item across revisions."""
    raw = f"{normalize_title(section_path)}/{normalize_title(title)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def item_fingerprint(
    *,
    item_body: str,
    evidence_keys: Iterable[str],
    engine_id: str,
    model_id: str,
) -> str:
    """Cache key for a single verdict.

    Includes everything that can change a verdict: the item text, the retrieved
    evidence set, the prompt template, the model and the algorithm versions.
    """
    payload = "\n".join(
        [
            normalize_text(item_body),
            *sorted(evidence_keys),
            PROMPT_VERSION,
            EXTRACTOR_VERSION,
            MATCHER_VERSION,
            engine_id,
            model_id,
            OUTPUT_SCHEMA,
        ]
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def toolchain_id(engine_id: str, model_id: str) -> str:
    """Identity of everything except the documents.

    Stored with each snapshot so that upgrading plandelta, changing the prompt or
    switching model invalidates "these documents are unchanged, reuse the last
    answer" — otherwise a tool upgrade silently serves stale verdicts.
    """
    return (
        f"{EXTRACTOR_VERSION}.{MATCHER_VERSION}.{PROMPT_VERSION}.{OUTPUT_SCHEMA}"
        f":{engine_id}:{model_id}"
    )


def read_text(path: Path) -> str:
    """Read a document, tolerating the odd non-UTF8 byte."""
    return path.read_text(encoding="utf-8", errors="replace")
