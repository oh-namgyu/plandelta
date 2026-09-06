from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from plandelta import prose
from plandelta.compare import compare_pair
from plandelta.discovery import discover
from plandelta.extract import PlanItem, extract_items
from plandelta.hashing import plan_hash, read_text
from plandelta.store import Store
from test_store import ScriptedEngine, all_done_reply

FIXTURES = Path(__file__).parent / "fixtures" / "sample_root"
QUOTE = "The ingest service reads CSV files"

HEADINGS = [
    PlanItem("h1", "Goal", "## Goal\nDeliver a small ingest service.", "P", 1, 2, "heading"),
    PlanItem("h2", "Background", "## Background\nWe measured 3s p95.", "P", 4, 5, "heading"),
    PlanItem("h3", "Storage", "## Storage\nRows land in SQLite.", "P", 7, 8, "heading"),
]


def reply(*promises: bool) -> str:
    rows = ",".join(
        '{"index": %d, "promise": %s, "reason": "r"}' % (index, "true" if flag else "false")
        for index, flag in enumerate(promises)
    )
    return '{"headings": [%s]}' % rows


class ProseClassificationTest(unittest.TestCase):
    def test_scaffolding_headings_are_identified(self) -> None:
        kept = prose.promises_from_reply(reply(True, False, True), HEADINGS)
        self.assertEqual(kept, {"h1", "h3"})

    def test_a_heading_the_model_skipped_is_kept(self) -> None:
        """Dropping a real promise is worse than judging one piece of scaffolding."""
        kept = prose.promises_from_reply('{"headings": [{"index": 1, "promise": false}]}', HEADINGS)
        self.assertEqual(kept, {"h1", "h3"})

    def test_a_malformed_reply_keeps_everything(self) -> None:
        kept = prose.promises_from_reply('{"nope": []}', HEADINGS)
        self.assertEqual(kept, {"h1", "h2", "h3"})

    def test_the_prompt_carries_each_heading_with_its_body(self) -> None:
        prompt = prose.build_prompt(HEADINGS)
        self.assertIn("HEADING 0", prompt)
        self.assertIn("Rows land in SQLite", prompt)
        self.assertIn("<document>", prompt)


class ProseIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name))
        self.pair = next(p for p in discover(FIXTURES).pairs if p.id == "prose-plan")
        self.plan_hash = plan_hash(read_text(self.pair.plan))
        self.items = extract_items(read_text(self.pair.plan))

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def test_scaffolding_is_identified_and_cached(self) -> None:
        engine = ScriptedEngine([reply(True, False, True)])
        keys = prose.scaffolding_keys(self.items, engine, self.store, self.plan_hash)
        self.assertEqual(len(keys), 1)

        again = ScriptedEngine([])
        self.assertEqual(
            prose.scaffolding_keys(self.items, again, self.store, self.plan_hash), keys
        )
        self.assertEqual(again.calls, 0, "a classified revision must not be asked twice")

    def test_an_unavailable_engine_treats_nothing_as_scaffolding(self) -> None:
        keys = prose.scaffolding_keys(self.items, ScriptedEngine([]), self.store, "other-hash")
        self.assertEqual(keys, set())

    def test_checkbox_plans_never_call_the_model(self) -> None:
        pair = next(p for p in discover(FIXTURES).pairs if p.id == "checkbox-app")
        items = extract_items(read_text(pair.plan))
        engine = ScriptedEngine([])
        self.assertEqual(prose.scaffolding_keys(items, engine, self.store, "hash"), set())
        self.assertEqual(engine.calls, 0)

    def test_scaffolding_is_set_aside_and_still_visible(self) -> None:
        """A misread promise must not vanish: it is out of scope, not deleted."""
        engine = ScriptedEngine([reply(True, False, True), all_done_reply(2, QUOTE)])
        result = compare_pair(self.pair, engine, self.store, find_extras=False)
        self.assertEqual(result.dropped_headings, 1)
        self.assertEqual(len(result.verdicts), len(self.items))
        aside = [v for v in result.verdicts if v.status == "out_of_scope"]
        self.assertEqual(len(aside), 1)
        self.assertIn("scaffolding", aside[0].reason)
        self.assertEqual(result.totals.rate, 100.0)

    def test_classification_can_be_turned_off(self) -> None:
        engine = ScriptedEngine([all_done_reply(3, QUOTE)])
        result = compare_pair(
            self.pair, engine, self.store, find_extras=False, classify_prose=False
        )
        self.assertEqual(result.dropped_headings, 0)
        self.assertEqual(len(result.verdicts), len(self.items))
        self.assertEqual([v.status for v in result.verdicts].count("out_of_scope"), 0)


if __name__ == "__main__":
    unittest.main()
