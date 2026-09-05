"""Backup and restore for the SQLite snapshot database.

Kept apart from the query layer because the correctness argument here is about
files, not rows: a WAL database cannot be backed up by copying the file, and a
restore that overwrites the live database before verifying the source can lose
everything.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

BACKUP_KEEP = 3


def _integrity(conn: sqlite3.Connection) -> str:
    return str(conn.execute("PRAGMA integrity_check").fetchone()[0])


def rotate(path: Path, keep: int = BACKUP_KEEP) -> None:
    """Shift bak1..bakN along, dropping the oldest."""
    for index in range(keep, 0, -1):
        source = Path(f"{path}.bak{index}")
        if not source.exists():
            continue
        if index == keep:
            source.unlink()
        else:
            source.rename(Path(f"{path}.bak{index + 1}"))


def backup(conn: sqlite3.Connection, path: Path, keep: int = BACKUP_KEEP) -> Path:
    """Write a consistent, integrity-checked backup through SQLite's own API."""
    rotate(path, keep)
    target = Path(f"{path}.bak1")
    with sqlite3.connect(target) as dest:
        conn.backup(dest)
        status = _integrity(dest)
    if status != "ok":
        target.unlink(missing_ok=True)
        raise RuntimeError(f"backup failed integrity_check: {status}")
    target.chmod(0o600)
    return target


def restore(path: Path, source: Path) -> None:
    """Restore atomically: verify a temp copy, keep the old file, then swap."""
    temp = Path(f"{path}.restoring")
    with sqlite3.connect(source) as src, sqlite3.connect(temp) as dest:
        src.backup(dest)
        status = _integrity(dest)
    if status != "ok":
        temp.unlink(missing_ok=True)
        raise RuntimeError(f"restore source failed integrity_check: {status}")
    Path(path).replace(Path(f"{path}.pre-restore"))
    temp.replace(path)
