import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from plandelta.__main__ import main
from plandelta.compare import compare_pair
from plandelta.discovery import discover
from plandelta.report import render_report
from plandelta.store import Store
from test_store import ScriptedEngine, all_done_reply

FIXTURES = Path(__file__).parent / "fixtures" / "sample_root"


class CliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "docs"
        shutil.copytree(FIXTURES, self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _run(self, argv: list[str]) -> str:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main(argv)
        self.assertEqual(code, 0)
        return buffer.getvalue()

    def test_pairs_json_lists_every_pair(self) -> None:
        payload = json.loads(self._run(["pairs", "--root", str(self.root), "--json"]))
        self.assertEqual(payload["count"], 4)
        self.assertEqual(payload["pairs"][0]["plan"], "bundle-multi_final.md")

    def test_compare_without_consent_fails_closed(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main(["compare", "--root", str(self.root), "--engine", "claude-cli"])
        self.assertEqual(code, 2)

    def test_snapshots_reports_stored_rounds(self) -> None:
        store = Store(self.root)
        pair = next(p for p in discover(self.root).pairs if p.id == "prose-plan")
        result = compare_pair(
            pair, ScriptedEngine([all_done_reply(3, "The ingest service reads CSV files")]),
            store, find_extras=False, classify_prose=False,
        )
        store.write_snapshot(
            pair_id=pair.id, plan_hash=result.plan_hash, bundle_hash=result.bundle_hash,
            engine="scripted", model="test-model", toolchain="t1", totals=result.totals.as_dict(),
            verdicts=result.verdicts, extras=result.extras, lineage=result.lineage,
        )
        store.close()
        rows = json.loads(
            self._run(["snapshots", "prose-plan", "--root", str(self.root), "--json"])
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["totals"]["rate"], 100.0)


class ReportTest(unittest.TestCase):
    def _result(self, pair_id: str, reply: str):
        pair = next(p for p in discover(FIXTURES).pairs if p.id == pair_id)
        return compare_pair(pair, ScriptedEngine([reply]), None, find_extras=False, classify_prose=False)

    def test_report_escapes_document_markup(self) -> None:
        payload = (
            '{"verdicts": [{"index": 0, "status": "done", "reason": "shipped",'
            ' "evidence": [{"file": "xss-payload_completion.md", "line_start": 2, "line_end": 2,'
            ' "quote": "Rendering is escaped: <img src=x onerror=\\"window.__pwned=1\\"> stays inert."}]}]}'
        )
        html = render_report(self._result("xss-payload", payload))
        self.assertNotIn("<img src=x", html)
        self.assertIn("&lt;img src=x", html)
        self.assertNotIn("<script>window.__pwned", html)

    def test_report_has_no_inline_style_attributes(self) -> None:
        html = render_report(self._result("prose-plan", all_done_reply(3, "The ingest service reads CSV")))
        self.assertNotIn(" style=", html)
        self.assertIn("<style>", html)

    def test_report_declares_a_content_security_policy(self) -> None:
        html = render_report(self._result("prose-plan", all_done_reply(3, "The ingest service reads CSV")))
        self.assertIn("default-src 'none'", html)

    def test_report_is_self_contained(self) -> None:
        html = render_report(self._result("prose-plan", all_done_reply(3, "The ingest service reads CSV")))
        for external in ("http://", "https://", "<script"):
            self.assertNotIn(external, html)

    def test_report_shows_rate_and_coverage(self) -> None:
        result = self._result("prose-plan", all_done_reply(3, "The ingest service reads CSV"))
        html = render_report(result)
        self.assertIn("Completion rate", html)
        self.assertIn("Evidence coverage", html)


if __name__ == "__main__":
    unittest.main()
