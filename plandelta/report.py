"""Self-contained HTML report.

No CDN, no chart library, no inline ``style=`` attributes: one stylesheet block
of reusable classes and hand-drawn SVG. Every piece of document text goes
through ``html.escape`` — the documents are untrusted input, and a completion
report containing ``<script>`` must render as characters, not code.
"""

from __future__ import annotations

import html
import json
from typing import Sequence

from .judge import ExtraFinding, Verdict
from .scoring import Totals

STATUS_LABELS = {
    "exceeded": "Exceeded", "done": "Done", "partial": "Partial", "missed": "Missed",
    "unknown": "Unknown", "error": "Error", "extra": "Unplanned",
}
DONUT_ORDER = ("exceeded", "done", "partial", "missed", "unknown", "error")

_CSS = """
:root { color-scheme: light dark; --bg:#fff; --fg:#1a1c1f; --muted:#5b6472; --line:#dde1e7;
  --card:#f7f8fa; --exceeded:#0d9488; --done:#16a34a; --partial:#d97706; --missed:#dc2626;
  --unknown:#8b95a5; --error:#7c3aed; --extra:#2563eb; }
@media (prefers-color-scheme: dark) { :root { --bg:#14161a; --fg:#e8eaed; --muted:#9aa4b2;
  --line:#2b303a; --card:#1c1f26; } }
* { box-sizing: border-box; }
body { margin:0; padding:2rem 1.5rem; background:var(--bg); color:var(--fg); font:15px/1.6
  ui-sans-serif, -apple-system, "Segoe UI", Roboto, "Noto Sans KR", sans-serif; }
.wrap { max-width: 1100px; margin: 0 auto; }
h1 { font-size:1.5rem; margin:0 0 .25rem; } h2 { font-size:1.1rem; margin:2rem 0 .75rem; }
.sub { color:var(--muted); margin:0 0 1.5rem; font-size:.9rem; }
.kpis { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:.75rem; }
.kpi { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:.85rem 1rem; }
.kpi .label { color:var(--muted); font-size:.78rem; text-transform:uppercase; letter-spacing:.04em; }
.kpi .value { font-size:1.6rem; font-weight:650; }
.charts { display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:1rem;
  margin-top:1rem; }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:1rem; }
.legend { list-style:none; margin:.5rem 0 0; padding:0; font-size:.85rem; }
.legend li { display:flex; align-items:center; gap:.5rem; }
.swatch { width:.7rem; height:.7rem; border-radius:2px; display:inline-block; }
.item { border:1px solid var(--line); border-radius:10px; padding:.85rem 1rem; margin-bottom:.6rem;
  background:var(--card); }
.item-head { display:flex; align-items:baseline; gap:.6rem; flex-wrap:wrap; }
.title { font-weight:600; } .section { color:var(--muted); font-size:.8rem; }
.badge { font-size:.75rem; font-weight:650; padding:.12rem .5rem; border-radius:999px;
  color:#fff; white-space:nowrap; }
.points { font-variant-numeric:tabular-nums; color:var(--muted); font-size:.85rem; }
.reason { color:var(--muted); margin:.35rem 0 0; font-size:.9rem; }
blockquote { margin:.5rem 0 0; padding:.4rem .75rem; border-left:3px solid var(--line);
  white-space:pre-wrap; font-size:.88rem; overflow-x:auto; }
blockquote cite { display:block; color:var(--muted); font-size:.75rem; font-style:normal;
  margin-bottom:.25rem; }
.table-scroll { overflow-x:auto; }
footer { color:var(--muted); font-size:.8rem; margin-top:2.5rem; border-top:1px solid var(--line);
  padding-top:.75rem; }
"""

for _status in ("exceeded", "done", "partial", "missed", "unknown", "error", "extra"):
    _CSS += f".bg-{_status} {{ background: var(--{_status}); }}\n"


def _esc(text: str) -> str:
    return html.escape(str(text), quote=True)


def donut_svg(counts: dict[str, int]) -> str:
    """Hand-drawn donut: one stroked arc per status, no chart library."""
    total = sum(counts.get(s, 0) for s in DONUT_ORDER)
    if not total:
        return '<p class="sub">No items to chart.</p>'
    radius, circumference, offset = 54.0, 2 * 3.14159265 * 54.0, 0.0
    arcs = []
    for status in DONUT_ORDER:
        value = counts.get(status, 0)
        if not value:
            continue
        length = circumference * value / total
        arcs.append(
            f'<circle class="arc" cx="70" cy="70" r="{radius}" fill="none" '
            f'stroke="var(--{status})" stroke-width="20" '
            f'stroke-dasharray="{length:.2f} {circumference - length:.2f}" '
            f'stroke-dashoffset="{-offset:.2f}" transform="rotate(-90 70 70)"></circle>'
        )
        offset += length
    legend = "".join(
        f'<li><span class="swatch bg-{s}"></span>{STATUS_LABELS[s]} — {counts.get(s, 0)}</li>'
        for s in DONUT_ORDER
        if counts.get(s, 0)
    )
    return (
        f'<svg viewBox="0 0 140 140" width="140" height="140" role="img" '
        f'aria-label="status distribution">{"".join(arcs)}</svg>'
        f'<ul class="legend">{legend}</ul>'
    )


def stack_svg(rows: Sequence[dict]) -> str:
    """One bar per item, coloured by status.

    Takes plain dicts (``status``/``title``/``points``) so the HTML report and
    the live UI draw the identical chart from the same code path.
    """
    if not rows:
        return '<p class="sub">No items.</p>'
    row_height, gap = 16, 4
    height = len(rows) * (row_height + gap)
    bars = []
    for index, row in enumerate(rows):
        status = row["status"]
        width = {"exceeded": 100, "done": 78, "partial": 40, "missed": 22}.get(status, 12)
        bars.append(
            f'<rect x="0" y="{index * (row_height + gap)}" width="{width}%" height="{row_height}" '
            f'rx="3" fill="var(--{status})"><title>'
            f'{_esc(row["title"])} — {STATUS_LABELS.get(status, status)} '
            f'({int(row["points"]):+d})</title></rect>'
        )
    return (
        f'<svg viewBox="0 0 100 {height}" width="100%" height="{height}" preserveAspectRatio="none" '
        f'role="img" aria-label="per-item scores">{"".join(bars)}</svg>'
    )


def trend_svg(points: Sequence[dict]) -> str:
    """Completion rate and evidence coverage across rounds, drawn by hand.

    One round is a dot, not a line — a single snapshot has no trend, and drawing
    a flat line through it would imply stability nobody measured.
    """
    if not points:
        return '<p class="sub">No rounds recorded yet.</p>'
    width, height, pad = 420, 160, 26
    span = max(len(points) - 1, 1)

    def coords(key: str) -> list[tuple[float, float]]:
        return [
            (
                pad + (width - 2 * pad) * index / span,
                height - pad - (height - 2 * pad) * min(float(p.get(key, 0)), 100.0) / 100.0,
            )
            for index, p in enumerate(points)
        ]

    layers = []
    for key, colour in (("coverage", "var(--unknown)"), ("rate", "var(--done)")):
        pts = coords(key)
        if len(pts) > 1:
            path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(pts))
            layers.append(f'<path d="{path}" fill="none" stroke="{colour}" stroke-width="2"/>')
        layers.extend(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{colour}"><title>'
            f'round {points[i]["round"]}: {key} {points[i].get(key, 0):.1f}%</title></circle>'
            for i, (x, y) in enumerate(pts)
        )
    axis = (
        f'<line x1="{pad}" y1="{height - pad}" x2="{width - pad}" y2="{height - pad}" '
        f'stroke="var(--line)"/>'
        f'<line x1="{pad}" y1="{pad}" x2="{pad}" y2="{height - pad}" stroke="var(--line)"/>'
    )
    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}" role="img" '
        f'aria-label="completion rate per round">{axis}{"".join(layers)}</svg>'
        f'<ul class="legend"><li><span class="swatch bg-done"></span>Completion rate</li>'
        f'<li><span class="swatch bg-unknown"></span>Evidence coverage</li></ul>'
    )


def _kpis(totals: Totals) -> str:
    cells = [
        ("Completion rate", f"{totals.rate:.1f}%"),
        ("Evidence coverage", f"{totals.coverage:.1f}%"),
        ("Points", f"{totals.points} / {totals.max_points}"),
        ("Bonus / Penalty", f"{totals.bonus:+d} / {totals.penalty:+d}"),
        ("Unplanned work", str(totals.scope_creep)),
    ]
    return "".join(
        f'<div class="kpi"><div class="label">{_esc(label)}</div>'
        f'<div class="value">{_esc(value)}</div></div>'
        for label, value in cells
    )


def _evidence_html(verdict: Verdict) -> str:
    return "".join(
        f"<blockquote><cite>{_esc(e.file)}:{e.line_start}-{e.line_end}</cite>{_esc(e.quote)}</blockquote>"
        for e in verdict.evidence
    )


def _item_html(verdict: Verdict, lineage: str) -> str:
    tag = f' · <span class="section">{_esc(lineage)}</span>' if lineage and lineage != "same" else ""
    return (
        f'<article class="item"><div class="item-head">'
        f'<span class="badge bg-{verdict.status}">{STATUS_LABELS[verdict.status]}</span>'
        f'<span class="title">{_esc(verdict.item.title)}</span>'
        f'<span class="points">{verdict.points:+d}</span>'
        f'<span class="section">{_esc(verdict.item.section)}</span>{tag}</div>'
        f'<p class="reason">{_esc(verdict.reason)}</p>{_evidence_html(verdict)}</article>'
    )


def _extras_html(extras: Sequence[ExtraFinding]) -> str:
    if not extras:
        return ""
    body = "".join(
        f'<article class="item"><div class="item-head">'
        f'<span class="badge bg-extra">Unplanned</span>'
        f'<span class="title">{_esc(x.reason or "delivered but not planned")}</span></div>'
        f"<blockquote><cite>{_esc(x.evidence.file)}:{x.evidence.line_start}-"
        f"{x.evidence.line_end}</cite>{_esc(x.evidence.quote)}</blockquote></article>"
        for x in extras
    )
    return f"<h2>Unplanned work ({len(extras)})</h2>{body}"


def render_report(result) -> str:
    """Render one comparison result as a standalone HTML document."""
    totals = result.totals
    generated = result.engine
    items = "".join(_item_html(v, result.lineage.get(v.item.key, "same")) for v in result.verdicts)
    meta = _esc(
        json.dumps(
            {"plan_hash": result.plan_hash[:12], "bundle_hash": result.bundle_hash[:12],
             "llm_calls": result.llm_calls, **generated},
            ensure_ascii=False,
        )
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>plandelta — {_esc(result.pair.id)}</title>
<style>{_CSS}</style></head>
<body><div class="wrap">
<h1>{_esc(result.pair.title or result.pair.id)}</h1>
<p class="sub">plan {_esc(result.pair.plan.name)} vs
{_esc(", ".join(p.name for p in result.pair.done))}</p>
<div class="kpis">{_kpis(totals)}</div>
<div class="charts">
  <section class="card"><h2>Status distribution</h2>{donut_svg(totals.counts)}</section>
  <section class="card"><h2>Per-item score</h2><div class="table-scroll">{stack_svg([{"status": v.status, "title": v.item.title, "points": v.points} for v in result.verdicts])}</div></section>
</div>
<h2>Items ({len(result.verdicts)})</h2>
{items}
{_extras_html(result.extras)}
<footer>{meta}</footer>
</div></body></html>
"""
