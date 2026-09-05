import sqlite3
import tempfile
import unittest
from pathlib import Path

from plandelta.compare import compare_pair, is_unchanged
from plandelta.discovery import discover
from plandelta.engines.base import Engine, EngineInfo
from plandelta.errors import EngineUnavailable
from plandelta.judge import Verdict
from plandelta.store import Store

FIXTURES = Path(__file__).parent / "fixtures" / "sample_root"


class ScriptedEngine(Engine):
    """Replays canned replies and counts calls, so cache behaviour is testable."""

    def __init__(self, replies: list[str]) -> None:
        self.info = EngineInfo(id="scripted", model_id="test-model", data_path="local")
        self.replies = replies
        self.calls = 0

    def complete(self, prompt: str) -> str:
        self.calls += 1
        if not self.replies:
            raise EngineUnavailable("no scripted reply left")
        return self.replies.pop(0)


def all_done_reply(count: int, quote: str) -> str:
    rows = ",".join(
        '{"index": %d, "status": "done", "reason": "ok", "evidence":'
        ' [{"file": "f", "line_start": 1, "line_end": 1, "quote": "%s"}]}' % (i, quote)
        for i in range(count)
    )
    return '{"verdicts": [%s]}' % rows


class StoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root)
        self.pair = next(p for p in discover(FIXTURES).pairs if p.id == "prose-plan")

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def _run(self, engine: ScriptedEngine):
        result = compare_pair(self.pair, engine, self.store, find_extras=False)
        result.snapshot_id = self.store.write_snapshot(
            pair_id=self.pair.id, plan_hash=result.plan_hash, bundle_hash=result.bundle_hash,
            engine=engine.info.id, model=engine.info.model_id, totals=result.totals.as_dict(),
            verdicts=result.verdicts, extras=result.extras, lineage=result.lineage,
        )
        return result

    def test_second_run_uses_cache_and_calls_no_model(self) -> None:
        quote = "The ingest service reads CSV files"
        first = self._run(ScriptedEngine([all_done_reply(3, quote)]))
        self.assertEqual(first.llm_calls, 1)
        self.assertTrue(all(v.status == "done" for v in first.verdicts))

        second_engine = ScriptedEngine([])
        second = compare_pair(self.pair, second_engine, self.store, find_extras=False)
        self.assertEqual(second_engine.calls, 0)
        self.assertEqual(second.totals.rate, first.totals.rate)
        self.assertTrue(all(v.cached for v in second.verdicts))

    def test_unchanged_documents_are_detected(self) -> None:
        result = self._run(ScriptedEngine([all_done_reply(3, "The ingest service reads CSV files")]))
        self.assertTrue(is_unchanged(self.store, self.pair, result.plan_hash, result.bundle_hash))

    def test_editing_the_bundle_only_reruns_affected_items(self) -> None:
        quote = "The ingest service reads CSV files"
        self._run(ScriptedEngine([all_done_reply(3, quote)]))

        target = self.pair.done[0]
        original = target.read_text(encoding="utf-8")
        target.write_text(
            original.replace("Observability was deferred", "Observability now logs a summary line"),
            encoding="utf-8",
        )
        try:
            engine = ScriptedEngine([all_done_reply(1, quote)])
            result = compare_pair(self.pair, engine, self.store, find_extras=False)
            self.assertEqual(engine.calls, 1)
            self.assertEqual(sum(1 for v in result.verdicts if v.cached), 2)
        finally:
            target.write_text(original, encoding="utf-8")

    def test_snapshot_write_is_atomic(self) -> None:
        result = compare_pair(
            self.pair, ScriptedEngine([all_done_reply(3, "The ingest service reads CSV files")]),
            self.store, find_extras=False,
        )
        broken = list(result.verdicts)
        broken.append(Verdict(item=broken[0].item, status="done"))
        broken[-1].item = None  # type: ignore[assignment]
        with self.assertRaises(Exception):
            self.store.write_snapshot(
                pair_id=self.pair.id, plan_hash=result.plan_hash, bundle_hash=result.bundle_hash,
                engine="scripted", model="test-model", totals=result.totals.as_dict(),
                verdicts=broken, extras=[], lineage={},
            )
        self.assertEqual(self.store.snapshots(self.pair.id), [])

    def test_backup_and_restore_round_trip(self) -> None:
        self._run(ScriptedEngine([all_done_reply(3, "The ingest service reads CSV files")]))
        backup = self.store.backup()
        self.assertTrue(backup.exists())

        snapshot_id = self.store.snapshots(self.pair.id)[0]["id"]
        self.store.delete_snapshot(snapshot_id)
        self.assertEqual(self.store.snapshots(self.pair.id), [])

        self.store.restore(backup)
        self.assertEqual(len(self.store.snapshots(self.pair.id)), 1)
        status = self.store.conn.execute("PRAGMA integrity_check").fetchone()[0]
        self.assertEqual(status, "ok")

    def test_database_files_are_owner_only(self) -> None:
        self._run(ScriptedEngine([all_done_reply(3, "The ingest service reads CSV files")]))
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)

    def test_newer_schema_is_refused(self) -> None:
        self.store.close()
        conn = sqlite3.connect(self.root / ".plandelta" / "snapshots.db")
        conn.execute("UPDATE schema_version SET version = 99")
        conn.commit()
        conn.close()
        with self.assertRaises(RuntimeError):
            Store(self.root)


if __name__ == "__main__":
    unittest.main()
