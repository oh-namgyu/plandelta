"""Snapshot storage: SQLite, one transaction per snapshot, hash-keyed cache.

Three properties this module exists to guarantee:

- **A snapshot is all-or-nothing.** Rows are written inside a single
  transaction, so an interrupted run leaves no half-comparison behind.
- **Unchanged items cost nothing.** Verdicts are cached by
  ``item_fingerprint``, so re-running an unchanged pair issues zero model calls.
- **Backups are consistent.** WAL mode means copying the file is not enough, so
  backups go through SQLite's own backup API and are integrity-checked.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Sequence

from . import dbfile
from .judge import ExtraFinding, Verdict
from .matcher import Evidence

SCHEMA_VERSION = 2
DB_NAME = "snapshots.db"
BACKUP_KEEP = dbfile.BACKUP_KEEP

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pair_id TEXT NOT NULL,
    plan_hash TEXT NOT NULL,
    bundle_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    engine TEXT NOT NULL,
    model TEXT NOT NULL,
    totals TEXT NOT NULL,
    toolchain TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS items (
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    item_key TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    title TEXT NOT NULL,
    section TEXT NOT NULL,
    status TEXT NOT NULL,
    points INTEGER NOT NULL,
    reason TEXT NOT NULL,
    evidence TEXT NOT NULL,
    line_start INTEGER NOT NULL,
    line_end INTEGER NOT NULL,
    lineage TEXT NOT NULL DEFAULT 'same'
);
CREATE TABLE IF NOT EXISTS extras (
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    file TEXT NOT NULL, line_start INTEGER NOT NULL, line_end INTEGER NOT NULL,
    quote TEXT NOT NULL, reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS verdict_cache (
    fingerprint TEXT PRIMARY KEY,
    status TEXT NOT NULL, reason TEXT NOT NULL, evidence TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshots_pair ON snapshots(pair_id, id);
CREATE INDEX IF NOT EXISTS idx_items_snapshot ON items(snapshot_id);
"""


@dataclass(frozen=True)
class CachedVerdict:
    status: str
    reason: str
    evidence: list[Evidence]


class Store:
    def __init__(self, root: Path) -> None:
        self.dir = Path(root) / ".plandelta"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.dir.chmod(0o700)
        self.path = self.dir / DB_NAME
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._migrate()
        self._restrict_permissions()

    # -- lifecycle ---------------------------------------------------------

    def _migrate(self) -> None:
        self.conn.executescript(_SCHEMA)
        row = self.conn.execute("SELECT version FROM schema_version").fetchone()
        if row is None:
            self.conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
            self.conn.commit()
            return
        if row["version"] < SCHEMA_VERSION:
            self._upgrade(int(row["version"]))
            return
        if row["version"] > SCHEMA_VERSION:
            raise RuntimeError(
                f"database schema v{row['version']} is newer than this build (v{SCHEMA_VERSION})"
            )

    def _upgrade(self, from_version: int) -> None:
        """Sequential migrations. Never destructive."""
        if from_version < 2:
            columns = {r["name"] for r in self.conn.execute("PRAGMA table_info(snapshots)")}
            if "toolchain" not in columns:
                self.conn.execute(
                    "ALTER TABLE snapshots ADD COLUMN toolchain TEXT NOT NULL DEFAULT ''"
                )
        self.conn.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION,))
        self.conn.commit()

    def _restrict_permissions(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(f"{self.path}{suffix}")
            if candidate.exists():
                candidate.chmod(0o600)

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.conn
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise

    # -- cache -------------------------------------------------------------

    def cached_verdict(self, fingerprint: str) -> CachedVerdict | None:
        row = self.conn.execute(
            "SELECT status, reason, evidence FROM verdict_cache WHERE fingerprint = ?",
            (fingerprint,),
        ).fetchone()
        if row is None:
            return None
        evidence = [Evidence(**e, score=1.0) for e in json.loads(row["evidence"])]
        return CachedVerdict(status=row["status"], reason=row["reason"], evidence=evidence)

    def remember_verdict(self, conn: sqlite3.Connection, fingerprint: str, verdict: Verdict) -> None:
        conn.execute(
            "INSERT OR REPLACE INTO verdict_cache VALUES (?, ?, ?, ?, datetime('now'))",
            (
                fingerprint,
                verdict.status,
                verdict.reason,
                json.dumps([e.as_dict() for e in verdict.evidence], ensure_ascii=False),
            ),
        )

    # -- snapshots ---------------------------------------------------------

    def latest_snapshot(self, pair_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM snapshots WHERE pair_id = ? ORDER BY id DESC LIMIT 1", (pair_id,)
        ).fetchone()

    def snapshots(self, pair_id: str) -> list[sqlite3.Row]:
        return list(
            self.conn.execute("SELECT * FROM snapshots WHERE pair_id = ? ORDER BY id", (pair_id,))
        )

    def snapshot_detail(self, snapshot_id: int) -> dict:
        """Items and extras of one snapshot, shaped like a fresh comparison.

        An unchanged pair must still answer "what were the verdicts?" — the
        caller should never have to re-run the model to see them.
        """
        items = [
            {
                "item_key": row["item_key"], "title": row["title"], "section": row["section"],
                "line_start": row["line_start"], "line_end": row["line_end"],
                "status": row["status"], "points": row["points"], "reason": row["reason"],
                "evidence": json.loads(row["evidence"]), "cached": True, "lineage": row["lineage"],
            }
            for row in self.conn.execute(
                "SELECT * FROM items WHERE snapshot_id = ? ORDER BY rowid", (snapshot_id,)
            )
        ]
        extras = [
            {"status": "extra", "points": 0, "reason": row["reason"], "file": row["file"],
             "line_start": row["line_start"], "line_end": row["line_end"], "quote": row["quote"]}
            for row in self.conn.execute(
                "SELECT * FROM extras WHERE snapshot_id = ? ORDER BY rowid", (snapshot_id,)
            )
        ]
        return {"items": items, "extras": extras}

    def snapshot_has_errors(self, snapshot_id: int) -> bool:
        """Did this round leave any item unjudged because the engine failed?"""
        row = self.conn.execute(
            "SELECT 1 FROM items WHERE snapshot_id = ? AND status = 'error' LIMIT 1", (snapshot_id,)
        ).fetchone()
        return row is not None

    def previous_items(self, pair_id: str) -> dict[str, str]:
        """``item_key`` → title from the newest snapshot, for lineage matching."""
        snapshot = self.latest_snapshot(pair_id)
        if snapshot is None:
            return {}
        rows = self.conn.execute(
            "SELECT item_key, title FROM items WHERE snapshot_id = ?", (snapshot["id"],)
        )
        return {row["item_key"]: row["title"] for row in rows}

    def write_snapshot(
        self,
        *,
        pair_id: str,
        plan_hash: str,
        bundle_hash: str,
        engine: str,
        model: str,
        toolchain: str,
        totals: dict,
        verdicts: Sequence[Verdict],
        extras: Sequence[ExtraFinding],
        lineage: dict[str, str],
    ) -> int:
        """Persist one comparison round atomically and return its snapshot id."""
        with self.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO snapshots (pair_id, plan_hash, bundle_hash, created_at, engine, model,"
                " totals, toolchain) VALUES (?, ?, ?, datetime('now'), ?, ?, ?, ?)",
                (pair_id, plan_hash, bundle_hash, engine, model,
                 json.dumps(totals, ensure_ascii=False), toolchain),
            )
            snapshot_id = int(cursor.lastrowid)
            conn.executemany(
                "INSERT INTO items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        snapshot_id, v.item.key, v.fingerprint, v.item.title, v.item.section,
                        v.status, v.points, v.reason,
                        json.dumps([e.as_dict() for e in v.evidence], ensure_ascii=False),
                        v.item.line_start, v.item.line_end, lineage.get(v.item.key, "same"),
                    )
                    for v in verdicts
                ],
            )
            conn.executemany(
                "INSERT INTO extras VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        snapshot_id, x.evidence.file, x.evidence.line_start, x.evidence.line_end,
                        x.evidence.quote, x.reason,
                    )
                    for x in extras
                ],
            )
            for verdict in verdicts:
                if verdict.fingerprint and verdict.status != "error":
                    self.remember_verdict(conn, verdict.fingerprint, verdict)
        return snapshot_id

    def delete_snapshot(self, snapshot_id: int) -> bool:
        with self.transaction() as conn:
            cursor = conn.execute("DELETE FROM snapshots WHERE id = ?", (snapshot_id,))
            conn.execute("DELETE FROM items WHERE snapshot_id = ?", (snapshot_id,))
            conn.execute("DELETE FROM extras WHERE snapshot_id = ?", (snapshot_id,))
        return cursor.rowcount > 0

    # -- backup / restore --------------------------------------------------

    def backup(self) -> Path:
        return dbfile.backup(self.conn, self.path, BACKUP_KEEP)

    def restore(self, backup_path: Path) -> None:
        self.conn.close()
        dbfile.restore(self.path, backup_path)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self._restrict_permissions()
