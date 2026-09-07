"""Command line entry point: ``pairs``, ``compare``, ``snapshots``."""

from __future__ import annotations

from typing import TYPE_CHECKING

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

from .compare import SCHEMA, compare_pair, is_unchanged, load_documents
from .discovery import Pair, discover
from .overrides import OVERRIDABLE
from .engines import DEFAULT_ENGINE, ENGINE_IDS, build_engine, require_consent
from .errors import PlandeltaError
from .hashing import bundle_hash, plan_hash, toolchain_id
from . import service
from .report import render_report
from .server import DEFAULT_PORT, serve
from .store import Store

if TYPE_CHECKING:  # imported for annotations only, so no import cycle
    from .engines import Engine



def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="directory holding documents")
    parser.add_argument("--json", action="store_true", help="machine-readable output")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="plandelta", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    pairs = sub.add_parser("pairs", help="list discovered plan/completion pairs")
    _add_common(pairs)

    compare = sub.add_parser("compare", help="compare pairs and store a snapshot")
    _add_common(compare)
    compare.add_argument("pair", nargs="?", help="pair id (default: every discovered pair)")
    compare.add_argument("--engine", default=DEFAULT_ENGINE, choices=ENGINE_IDS)
    compare.add_argument("--model", default="", help="pin the model (default: engine's default)")
    compare.add_argument("--base-url", default="", help="openai-compatible endpoint")
    compare.add_argument("--timeout", type=int, default=120, help="per-call timeout in seconds")
    compare.add_argument("--force", action="store_true", help="ignore cached verdicts")
    compare.add_argument("--no-extras", action="store_true", help="skip unplanned-work detection")
    compare.add_argument(
        "--no-prose-llm", action="store_true",
        help="keep every heading of a prose plan instead of asking which are promises",
    )
    compare.add_argument(
        "--scope", action="append", default=[], metavar="TEXT",
        help="only judge items whose section or title contains TEXT (repeatable)",
    )
    compare.add_argument("--report", type=Path, help="write an HTML report to this directory")
    compare.add_argument(
        "--yes-send-external", action="store_true",
        help="consent to sending document text to an external service",
    )

    ui = sub.add_parser("serve", help="review comparisons in a local web UI")
    _add_common(ui)
    ui.add_argument("--engine", default=DEFAULT_ENGINE, choices=ENGINE_IDS)
    ui.add_argument("--model", default="", help="pin the model (default: engine's default)")
    ui.add_argument("--base-url", default="", help="openai-compatible endpoint")
    ui.add_argument("--timeout", type=int, default=120, help="per-call timeout in seconds")
    ui.add_argument("--host", default="127.0.0.1", help="bind address (loopback by default)")
    ui.add_argument("--port", type=int, default=DEFAULT_PORT)
    ui.add_argument(
        "--yes-send-external", action="store_true",
        help="consent to sending document text to an external service",
    )

    override = sub.add_parser("override", help="record or revoke a human verdict for one item")
    _add_common(override)
    override.add_argument("pair", help="pair id")
    override.add_argument("item_key", help="item key (from compare --json)")
    override.add_argument("--status", default="", choices=("", *OVERRIDABLE))
    override.add_argument("--reason", default="", help="why the machine verdict was wrong")
    override.add_argument("--author", default="human")
    override.add_argument("--revoke", action="store_true", help="drop the active override")

    snapshots = sub.add_parser("snapshots", help="inspect stored comparison rounds")
    _add_common(snapshots)
    snapshots.add_argument("pair", help="pair id")
    snapshots.add_argument("--rm", type=int, metavar="ID", help="delete one snapshot")
    snapshots.add_argument("--backup", action="store_true", help="write a verified backup")
    return parser


def cmd_pairs(args: argparse.Namespace) -> int:
    result = discover(args.root)
    if args.json:
        print(json.dumps(result.as_dict(args.root), ensure_ascii=False, indent=2))
        return 0
    for pair in result.pairs:
        print(f"{pair.id}\t{pair.plan.name}\t{', '.join(p.name for p in pair.done)}")
    for entry in result.skipped:
        print(f"[skipped] {entry['id']}: {entry['reason']}", file=sys.stderr)
    return 0


def _select_pairs(args: argparse.Namespace) -> list[Pair]:
    pairs = discover(args.root).pairs
    if not args.pair:
        return pairs
    chosen = [p for p in pairs if p.id == args.pair]
    if not chosen:
        raise SystemExit(f"no such pair: {args.pair}")
    return chosen


def _compare_one(pair: Pair, engine: Engine, store: Store, args: argparse.Namespace) -> dict:
    if getattr(args, "scope", None):
        pair = replace(pair, scope=tuple(args.scope))
    plan_text, documents = load_documents(pair)
    p_hash = plan_hash(plan_text)
    b_hash = bundle_hash(list(documents.items()))
    chain = toolchain_id(engine.info.id, engine.info.model_id)
    if not args.force and is_unchanged(store, pair, p_hash, b_hash, chain):
        latest = store.latest_snapshot(pair.id)
        return {
            "schema": SCHEMA, "pair": pair.as_dict(args.root), "unchanged": True,
            "plan_hash": p_hash, "bundle_hash": b_hash,
            "generated_by": {"engine": latest["engine"], "model": latest["model"], "llm_calls": 0},
            "snapshot_id": latest["id"], "totals": json.loads(latest["totals"]),
            **service.snapshot_detail(store, latest["id"], pair.id),
        }

    result = compare_pair(
        pair, engine, store, force=args.force, find_extras=not args.no_extras,
        classify_prose=not args.no_prose_llm,
    )
    result.snapshot_id = store.write_snapshot(
        pair_id=pair.id, plan_hash=result.plan_hash, bundle_hash=result.bundle_hash,
        engine=engine.info.id, model=engine.resolved_model(), toolchain=chain,
        totals=result.totals.as_dict(),
        verdicts=result.verdicts, extras=result.extras, lineage=result.lineage,
    )
    if args.report:
        args.report.mkdir(parents=True, exist_ok=True)
        (args.report / f"{pair.id}.html").write_text(render_report(result), encoding="utf-8")
    return result.as_dict(args.root)


def cmd_compare(args: argparse.Namespace) -> int:
    engine = build_engine(
        args.engine, model=args.model, base_url=args.base_url, timeout=args.timeout
    )
    require_consent(args.root, engine.info, granted_now=args.yes_send_external)
    store = Store(args.root)
    try:
        results = [_compare_one(pair, engine, store, args) for pair in _select_pairs(args)]
    finally:
        store.close()

    if args.json:
        print(json.dumps(results if len(results) > 1 else results[0], ensure_ascii=False, indent=2))
        return 0
    for row in results:
        totals = row["totals"]
        mark = " (unchanged)" if row.get("unchanged") else ""
        print(
            f"{row['pair']['id']}: {totals['rate']:.1f}% complete, "
            f"{totals['coverage']:.1f}% evidence coverage, "
            f"{totals['scope_creep']} unplanned{mark}"
        )
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    engine = build_engine(
        args.engine, model=args.model, base_url=args.base_url, timeout=args.timeout
    )
    require_consent(args.root, engine.info, granted_now=args.yes_send_external)
    serve(args.root, engine, host=args.host, port=args.port)
    return 0


def cmd_override(args: argparse.Namespace) -> int:
    store = Store(args.root)
    try:
        if args.revoke:
            print("revoked" if store.revoke_override(args.pair, args.item_key) else "no override")
            return 0
        if not args.status:
            raise SystemExit("--status is required unless --revoke is given")
        store.set_override(args.pair, args.item_key, args.status, args.reason, args.author)
        rows = store.override_history(args.pair)
    finally:
        store.close()
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    print(f"{args.item_key} -> {args.status} (by {args.author})")
    return 0


def cmd_snapshots(args: argparse.Namespace) -> int:
    store = Store(args.root)
    try:
        if args.backup:
            print(f"backup written: {store.backup()}")
            return 0
        if args.rm is not None:
            print("deleted" if store.delete_snapshot(args.rm) else "no such snapshot")
            return 0
        rows = [
            {"id": r["id"], "created_at": r["created_at"], "engine": r["engine"],
             "model": r["model"], "totals": json.loads(r["totals"])}
            for r in store.snapshots(args.pair)
        ]
    finally:
        store.close()
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    for row in rows:
        print(f"#{row['id']}\t{row['created_at']}\t{row['totals']['rate']:.1f}%\t{row['model']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handlers = {
        "pairs": cmd_pairs, "compare": cmd_compare, "serve": cmd_serve,
        "override": cmd_override, "snapshots": cmd_snapshots,
    }
    try:
        return handlers[args.command](args)
    except PlandeltaError as exc:
        print(json.dumps(exc.as_dict(), ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
