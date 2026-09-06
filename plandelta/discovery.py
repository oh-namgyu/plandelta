"""Pair discovery: which plan document pairs with which completion bundle.

Two modes, in priority order:

1. A ``plandelta.pairs.json`` manifest in the root, when present.
2. Deterministic globs: every ``*_final.md`` (falling back to ``*_draft.md``
   for slugs that have no final) is a plan, and its bundle is whichever of
   ``<slug>_completion.md`` / ``<slug>_verify.md`` / ``<slug>_status.json``
   actually exist.

Discovery never calls an LLM and never reads outside ``root``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from .errors import PairAmbiguous, PathDenied

MANIFEST_NAME = "plandelta.pairs.json"
DEFAULT_PLAN_GLOBS = ("*_final.md", "*_draft.md")
# A bundle may only contain documents that *report what was delivered*.
#
# Measured the hard way: an earlier default pulled in `_verify.md`, which in the
# author's corpus is a pre-implementation critique of the plan. The judge then
# read "the rollback procedure is undefined" — a complaint about the plan — as
# proof that rollback did not ship, and produced false `missed` verdicts. Review
# notes, approval metadata and plan critiques are not delivery evidence; add
# them explicitly through a manifest if your corpus uses them differently.
DEFAULT_PRIMARY_SUFFIXES = ("_completion.md",)
DEFAULT_SUPPLEMENTARY_SUFFIXES: tuple[str, ...] = ()


@dataclass(frozen=True)
class Pair:
    """One plan document plus the completion documents that answer it.

    ``scope`` narrows which promises this round is answerable for. A completion
    report that covers phase one should not make phase two look abandoned, so
    plan items outside the declared scope are set aside rather than scored.
    An empty scope means the whole plan.
    """

    id: str
    plan: Path
    done: tuple[Path, ...]
    title: str = ""
    scope: tuple[str, ...] = ()

    def as_dict(self, root: Path) -> dict:
        return {
            "id": self.id,
            "title": self.title or self.id,
            "plan": _relative(self.plan, root),
            "done": [_relative(p, root) for p in self.done],
            "scope": list(self.scope),
        }

    def in_scope(self, section: str, title: str) -> bool:
        """True when no scope is declared, or the item sits inside one."""
        if not self.scope:
            return True
        haystack = f"{section} {title}".lower()
        return any(needle.lower() in haystack for needle in self.scope)


def _relative(path: Path, root: Path) -> str:
    """Path relative to root, resolving symlinked roots (``/var`` on macOS)."""
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


@dataclass
class Discovery:
    """Discovery result: usable pairs plus the reasons others were dropped."""

    pairs: list[Pair] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)

    def as_dict(self, root: Path) -> dict:
        return {
            "root": str(root),
            "count": len(self.pairs),
            "pairs": [p.as_dict(root) for p in self.pairs],
            "skipped": self.skipped,
        }


def resolve_in_root(root: Path, candidate: Path) -> Path:
    """Resolve ``candidate`` and refuse anything outside ``root``.

    Symlinks are resolved *before* the containment check, so a link pointing out
    of the tree is rejected rather than followed.
    """
    real_root = root.resolve()
    real = (real_root / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
    if real != real_root and real_root not in real.parents:
        raise PathDenied(f"path escapes root: {candidate}")
    return real


def _slug_of(path: Path, plan_globs: Sequence[str]) -> str:
    """Strip the plan suffix (``_final.md``) to get the pair slug."""
    name = path.name
    for pattern in plan_globs:
        suffix = pattern.lstrip("*")
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return path.stem


def _from_manifest(root: Path, manifest: Path) -> Discovery:
    entries = json.loads(manifest.read_text(encoding="utf-8"))
    result = Discovery()
    for entry in entries:
        plan = resolve_in_root(root, Path(entry["plan"]))
        done = tuple(resolve_in_root(root, Path(p)) for p in entry.get("done", []))
        missing = [p for p in (plan, *done) if not p.is_file()]
        if missing:
            result.skipped.append(
                {"id": entry.get("id", plan.name), "reason": "missing file",
                 "files": [str(p) for p in missing]}
            )
            continue
        if not done:
            result.skipped.append({"id": entry.get("id", plan.name), "reason": "empty bundle"})
            continue
        result.pairs.append(
            Pair(
                id=entry.get("id", plan.stem), plan=plan, done=done,
                title=entry.get("title", ""), scope=tuple(entry.get("scope", []) or []),
            )
        )
    return result


def discover(
    root: Path,
    *,
    plan_globs: Sequence[str] = DEFAULT_PLAN_GLOBS,
    primary_suffixes: Sequence[str] = DEFAULT_PRIMARY_SUFFIXES,
    supplementary_suffixes: Sequence[str] = DEFAULT_SUPPLEMENTARY_SUFFIXES,
) -> Discovery:
    """Find every comparable pair under ``root``."""
    root = root.resolve()
    manifest = root / MANIFEST_NAME
    if manifest.is_file():
        return _from_manifest(root, manifest)

    # Group plan candidates by slug so we can detect ambiguity and apply the
    # final-over-draft preference deterministically.
    by_slug: dict[str, dict[str, list[Path]]] = {}
    for pattern in plan_globs:
        for path in sorted(root.glob(pattern)):
            slug = _slug_of(path, plan_globs)
            by_slug.setdefault(slug, {}).setdefault(pattern, []).append(path)

    result = Discovery()
    for slug in sorted(by_slug):
        plan = _pick_plan(slug, by_slug[slug], plan_globs)
        primary = _existing(root, slug, primary_suffixes)
        if not primary:
            result.skipped.append({"id": slug, "reason": "no primary completion document"})
            continue
        done = primary + _existing(root, slug, supplementary_suffixes)
        result.pairs.append(Pair(id=slug, plan=plan, done=done, title=slug))
    return result


def _existing(root: Path, slug: str, suffixes: Sequence[str]) -> tuple[Path, ...]:
    return tuple(p for p in (root / f"{slug}{s}" for s in suffixes) if p.is_file())


def _pick_plan(slug: str, found: dict[str, list[Path]], plan_globs: Sequence[str]) -> Path:
    """Choose the plan document for a slug, preferring earlier globs."""
    for pattern in plan_globs:
        paths = found.get(pattern) or []
        if len(paths) > 1:
            raise PairAmbiguous(f"{slug}: {len(paths)} plan candidates for {pattern}")
        if paths:
            return paths[0]
    raise PairAmbiguous(f"{slug}: no plan candidate")
