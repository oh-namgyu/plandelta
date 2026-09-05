"""Extract plan items from a Markdown plan document.

Rule-first, on purpose: checkboxes and ordered lists are unambiguous, cheap and
reproducible, so the LLM never sees a document whose structure we can already
read. Only prose-only sections fall back to leaf headings.

An "item" is one promise the plan makes, with the section path it lives under
and the exact source lines it came from.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterator, Sequence

from .hashing import item_key

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
CHECKBOX_RE = re.compile(r"^\s*[-*+]\s+\[([ xX])\]\s+(.+)$")
ORDERED_RE = re.compile(r"^(\s*)(\d+)[.)]\s+(.+)$")
BULLET_RE = re.compile(r"^(\s*)[-*+]\s+(.+)$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")

MAX_ITEMS = 300


@dataclass(frozen=True)
class PlanItem:
    key: str
    title: str
    body: str
    section: str
    line_start: int
    line_end: int
    kind: str
    checked: bool = False

    def as_dict(self) -> dict:
        return {
            "item_key": self.key,
            "title": self.title,
            "section": self.section,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "kind": self.kind,
            "checked": self.checked,
        }


@dataclass
class _Line:
    no: int
    text: str
    section: str


def _walk(text: str) -> Iterator[_Line]:
    """Yield content lines tagged with their heading path, skipping code."""
    stack: list[str] = []
    in_fence = False
    for no, raw in enumerate(text.replace("\r\n", "\n").split("\n"), start=1):
        if FENCE_RE.match(raw):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        heading = HEADING_RE.match(raw)
        if heading:
            depth = len(heading.group(1))
            del stack[depth - 1 :]
            stack.append(heading.group(2).strip())
            yield _Line(no, raw, " > ".join(stack))
            continue
        yield _Line(no, raw, " > ".join(stack))


def _block_end(lines: Sequence[_Line], start_idx: int, indent: int) -> int:
    """Index of the last line belonging to the item started at ``start_idx``."""
    end = start_idx
    for idx in range(start_idx + 1, len(lines)):
        text = lines[idx].text
        if not text.strip():
            continue
        if HEADING_RE.match(text):
            break
        line_indent = len(text) - len(text.lstrip())
        starts_item = CHECKBOX_RE.match(text) or ORDERED_RE.match(text) or BULLET_RE.match(text)
        if line_indent <= indent and starts_item:
            break
        if line_indent <= indent and not text.startswith(" "):
            break
        end = idx
    return end


def _make_item(lines: Sequence[_Line], idx: int, title: str, kind: str, checked: bool) -> PlanItem:
    line = lines[idx]
    indent = len(line.text) - len(line.text.lstrip())
    end_idx = _block_end(lines, idx, indent)
    body = "\n".join(l.text for l in lines[idx : end_idx + 1]).strip()
    return PlanItem(
        key=item_key(line.section, title),
        title=title.strip(),
        body=body,
        section=line.section,
        line_start=line.no,
        line_end=lines[end_idx].no,
        kind=kind,
        checked=checked,
    )


def _structured_items(lines: Sequence[_Line]) -> list[PlanItem]:
    """Checkbox and top-level ordered-list items — the unambiguous ones."""
    items: list[PlanItem] = []
    for idx, line in enumerate(lines):
        checkbox = CHECKBOX_RE.match(line.text)
        if checkbox:
            items.append(
                _make_item(lines, idx, checkbox.group(2), "checkbox", checkbox.group(1) != " ")
            )
            continue
        ordered = ORDERED_RE.match(line.text)
        if ordered and len(ordered.group(1)) == 0:
            items.append(_make_item(lines, idx, ordered.group(3), "ordered", False))
    return items


def _heading_items(lines: Sequence[_Line]) -> list[PlanItem]:
    """Fallback for prose plans: only *leaf* headings of depth >= 2 are items.

    A heading with sub-headings is scaffolding — "2. Problem list" promises
    nothing, its children do. Measured: keeping parents made 4 of 5 non-promise
    headings in a prose plan look like requirements.
    """
    items: list[PlanItem] = []
    for idx, line in enumerate(lines):
        heading = HEADING_RE.match(line.text)
        if not heading or len(heading.group(1)) < 2:
            continue
        if _has_child_heading(lines, idx, len(heading.group(1))):
            continue
        body_end = _heading_block_end(lines, idx)
        body = "\n".join(l.text for l in lines[idx : body_end + 1]).strip()
        if len(body.split()) < 4:
            continue
        parent = " > ".join(line.section.split(" > ")[:-1])
        items.append(
            PlanItem(
                key=item_key(parent, heading.group(2)),
                title=heading.group(2).strip(),
                body=body,
                section=parent,
                line_start=line.no,
                line_end=lines[body_end].no,
                kind="heading",
            )
        )
    return items


def _has_child_heading(lines: Sequence[_Line], start_idx: int, depth: int) -> bool:
    """True when a deeper heading follows before the next sibling or uncle."""
    for idx in range(start_idx + 1, len(lines)):
        heading = HEADING_RE.match(lines[idx].text)
        if not heading:
            continue
        return len(heading.group(1)) > depth
    return False


def _heading_block_end(lines: Sequence[_Line], start_idx: int) -> int:
    for idx in range(start_idx + 1, len(lines)):
        if HEADING_RE.match(lines[idx].text):
            return idx - 1
    return len(lines) - 1


def extract_items(text: str) -> list[PlanItem]:
    """Extract plan items, rules first and headings only as a fallback."""
    lines = list(_walk(text))
    items = _structured_items(lines)
    if not items:
        items = _heading_items(lines)
    return _dedupe(items)[:MAX_ITEMS]


def _dedupe(items: Sequence[PlanItem]) -> list[PlanItem]:
    """Keep the first occurrence of each key; suffix later collisions."""
    seen: dict[str, int] = {}
    out: list[PlanItem] = []
    for item in items:
        count = seen.get(item.key, 0)
        seen[item.key] = count + 1
        if count == 0:
            out.append(item)
            continue
        out.append(
            PlanItem(
                key=f"{item.key}-{count}",
                title=item.title,
                body=item.body,
                section=item.section,
                line_start=item.line_start,
                line_end=item.line_end,
                kind=item.kind,
                checked=item.checked,
            )
        )
    return out
