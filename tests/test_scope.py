import json
import tempfile
import unittest
from pathlib import Path

from plandelta.compare import compare_pair
from plandelta.discovery import MANIFEST_NAME, discover
from plandelta.judge import Verdict
from plandelta.scoring import summarize
from test_store import ScriptedEngine, all_done_reply

FIXTURES = Path(__file__).parent / "fixtures" / "sample_root"
QUOTE = "The CLI is done"


class ScopeTest(unittest.TestCase):
    def pair(self, scope=()):
        found = next(p for p in discover(FIXTURES).pairs if p.id == "checkbox-app")
        return found if not scope else type(found)(
            id=found.id, plan=found.plan, done=found.done, title=found.title, scope=tuple(scope)
        )

    def test_no_scope_judges_every_item(self) -> None:
        engine = ScriptedEngine([all_done_reply(6, QUOTE)])
        result = compare_pair(self.pair(), engine, None, find_extras=False)
        self.assertEqual(len(result.verdicts), 6)
        self.assertNotIn("out_of_scope", {v.status for v in result.verdicts})

    def test_scope_sets_other_items_aside_without_calling_the_model(self) -> None:
        engine = ScriptedEngine([all_done_reply(4, QUOTE)])
        result = compare_pair(self.pair(scope=["9. DoD"]), engine, None, find_extras=False)
        statuses = [v.status for v in result.verdicts]
        self.assertEqual(statuses.count("out_of_scope"), 2, statuses)
        self.assertEqual(engine.calls, 1)

    def test_out_of_scope_items_do_not_lower_the_rate(self) -> None:
        engine = ScriptedEngine([all_done_reply(4, QUOTE)])
        result = compare_pair(self.pair(scope=["9. DoD"]), engine, None, find_extras=False)
        self.assertEqual(result.totals.rate, 100.0)
        self.assertEqual(result.totals.out_of_scope, 2)
        self.assertEqual(result.totals.coverage, 100.0)

    def test_scope_matches_on_title_as_well_as_section(self) -> None:
        engine = ScriptedEngine([all_done_reply(1, QUOTE)])
        result = compare_pair(self.pair(scope=["donut chart"]), engine, None, find_extras=False)
        judged = [v for v in result.verdicts if v.status != "out_of_scope"]
        self.assertEqual([v.item.title for v in judged], ["Report renders a donut chart"])

    def test_manifest_carries_the_scope(self) -> None:
        manifest = FIXTURES / MANIFEST_NAME
        manifest.write_text(
            json.dumps([{
                "id": "scoped", "plan": "checkbox-app_final.md",
                "done": ["checkbox-app_completion.md"], "scope": ["6. Steps"],
            }]),
            encoding="utf-8",
        )
        try:
            pair = discover(FIXTURES).pairs[0]
            self.assertEqual(pair.scope, ("6. Steps",))
            self.assertTrue(pair.in_scope("Checkbox App Plan > 6. Steps", "Build the parser"))
            self.assertFalse(pair.in_scope("Checkbox App Plan > 9. DoD", "CLI accepts a plan file"))
        finally:
            manifest.unlink()

    def test_totals_report_out_of_scope_separately(self) -> None:
        item = next(iter(compare_pair(self.pair(), ScriptedEngine([all_done_reply(6, QUOTE)]),
                                      None, find_extras=False).verdicts)).item
        totals = summarize([
            Verdict(item=item, status="done"),
            Verdict(item=item, status="out_of_scope"),
        ])
        self.assertEqual(totals.rate, 100.0)
        self.assertEqual(totals.coverage, 100.0)
        self.assertEqual(totals.out_of_scope, 1)


if __name__ == "__main__":
    unittest.main()
