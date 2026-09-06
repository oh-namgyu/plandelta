from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from plandelta import overrides, service
from plandelta.compare import compare_pair
from plandelta.discovery import discover
from plandelta.hashing import toolchain_id
from plandelta.store import Store
from test_store import ScriptedEngine, all_done_reply

FIXTURES = Path(__file__).parent / "fixtures" / "sample_root"
QUOTE = "The ingest service reads CSV files"


class OverrideTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root)
        self.pair = next(p for p in discover(FIXTURES).pairs if p.id == "prose-plan")
        engine = ScriptedEngine([all_done_reply(3, QUOTE)])
        result = compare_pair(self.pair, engine, self.store, find_extras=False, classify_prose=False)
        self.snapshot_id = self.store.write_snapshot(
            pair_id=self.pair.id, plan_hash=result.plan_hash, bundle_hash=result.bundle_hash,
            engine="scripted", model="test-model",
            toolchain=toolchain_id("scripted", "test-model"),
            totals=result.totals.as_dict(), verdicts=result.verdicts, extras=result.extras,
            lineage=result.lineage,
        )
        self.item_key = result.verdicts[0].item.key

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def _items(self) -> list[dict]:
        return service.snapshot_detail(self.store, self.snapshot_id, self.pair.id)["items"]

    def test_an_override_replaces_the_status_but_keeps_the_machine_verdict(self) -> None:
        self.store.set_override(self.pair.id, self.item_key, "partial", "docs missing", "reviewer")
        item = next(i for i in self._items() if i["item_key"] == self.item_key)
        self.assertEqual(item["status"], "partial")
        self.assertEqual(item["points"], 1)
        self.assertEqual(item["machine_status"], "done")
        self.assertEqual(item["machine_points"], 3)
        self.assertEqual(item["override"]["author"], "reviewer")

    def test_items_without_an_override_are_untouched(self) -> None:
        self.store.set_override(self.pair.id, self.item_key, "partial", "", "reviewer")
        others = [i for i in self._items() if i["item_key"] != self.item_key]
        self.assertTrue(others)
        self.assertTrue(all(i["override"] is None for i in others))
        self.assertTrue(all("machine_status" not in i for i in others))

    def test_a_second_override_supersedes_the_first_and_history_keeps_both(self) -> None:
        self.store.set_override(self.pair.id, self.item_key, "partial", "first", "a")
        self.store.set_override(self.pair.id, self.item_key, "missed", "second", "b")
        active = self.store.overrides(self.pair.id)
        self.assertEqual(active[self.item_key]["status"], "missed")
        history = self.store.override_history(self.pair.id)
        self.assertEqual([row["status"] for row in history], ["partial", "missed"])
        self.assertIsNotNone(history[0]["revoked_at"])
        self.assertIsNone(history[1]["revoked_at"])

    def test_revoking_restores_the_machine_verdict(self) -> None:
        self.store.set_override(self.pair.id, self.item_key, "missed", "wrong", "a")
        self.assertTrue(self.store.revoke_override(self.pair.id, self.item_key))
        item = next(i for i in self._items() if i["item_key"] == self.item_key)
        self.assertEqual(item["status"], "done")
        self.assertIsNone(item["override"])

    def test_revoking_nothing_reports_nothing(self) -> None:
        self.assertFalse(self.store.revoke_override(self.pair.id, self.item_key))

    def test_overrides_survive_a_later_comparison(self) -> None:
        self.store.set_override(self.pair.id, self.item_key, "partial", "still true", "a")
        engine = ScriptedEngine([all_done_reply(3, QUOTE)])
        result = compare_pair(self.pair, engine, self.store, force=True, find_extras=False, classify_prose=False)
        snapshot_id = self.store.write_snapshot(
            pair_id=self.pair.id, plan_hash=result.plan_hash, bundle_hash=result.bundle_hash,
            engine="scripted", model="test-model", toolchain="t1",
            totals=result.totals.as_dict(), verdicts=result.verdicts, extras=result.extras,
            lineage=result.lineage,
        )
        item = next(
            i for i in service.snapshot_detail(self.store, snapshot_id, self.pair.id)["items"]
            if i["item_key"] == self.item_key
        )
        self.assertEqual(item["status"], "partial")

    def test_extra_and_error_cannot_be_chosen_by_hand(self) -> None:
        for status in ("extra", "error", "nonsense"):
            with self.assertRaises(ValueError):
                overrides.validate(status)

    def test_apply_is_a_no_op_without_overrides(self) -> None:
        rows = [{"item_key": "k", "status": "done", "points": 3}]
        self.assertEqual(overrides.apply(rows, {}), [{**rows[0], "override": None}])


if __name__ == "__main__":
    unittest.main()
