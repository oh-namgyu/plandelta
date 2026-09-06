import unittest

from plandelta.extract import PlanItem
from plandelta.judge import (
    ExtraFinding,
    extras_from_reply,
    parse_json_object,
    verdicts_from_reply,
    verify_quote,
)
from plandelta.matcher import Evidence
from plandelta.scoring import summarize

DOCS = {"done.md": "The CLI is finished and covered by 14 unit tests.\nLoad testing reached 300 rps."}
ITEMS = [
    PlanItem("k1", "Ship the CLI", "- [ ] Ship the CLI", "DoD", 3, 3, "checkbox"),
    PlanItem("k2", "Sustain 1k rps", "- [ ] Sustain 1k rps", "DoD", 4, 4, "checkbox"),
]


def reply(status: str, quote: str) -> str:
    return (
        '{"verdicts": [{"index": 0, "status": "%s", "reason": "r",'
        ' "evidence": [{"file": "done.md", "line_start": 1, "line_end": 1, "quote": "%s"}]}]}'
        % (status, quote)
    )


class JudgeTest(unittest.TestCase):
    def test_accepts_verdict_backed_by_a_real_quote(self) -> None:
        out = verdicts_from_reply(reply("done", "The CLI is finished"), ITEMS, DOCS)
        self.assertEqual(out[0].status, "done")
        self.assertEqual(out[0].points, 3)

    def test_fabricated_quote_demotes_claim_to_unknown(self) -> None:
        out = verdicts_from_reply(reply("done", "The CLI shipped with a dashboard"), ITEMS, DOCS)
        self.assertEqual(out[0].status, "unknown")
        self.assertEqual(out[0].evidence, [])

    def test_missed_needs_a_quote_that_states_the_shortfall(self) -> None:
        payload = (
            '{"verdicts": [{"index": 1, "status": "missed", "reason": "300 rps only",'
            ' "shortfall_quote": "Load testing reached 300 rps"}]}'
        )
        out = verdicts_from_reply(payload, ITEMS, DOCS)
        self.assertEqual(out[1].status, "missed")
        self.assertEqual(out[1].evidence[0].quote, "Load testing reached 300 rps")

    def test_missed_without_a_shortfall_quote_abstains(self) -> None:
        payload = '{"verdicts": [{"index": 1, "status": "missed", "reason": "300 rps only"}]}'
        out = verdicts_from_reply(payload, ITEMS, DOCS)
        self.assertEqual(out[1].status, "unknown")
        self.assertIn("shortfall", out[1].reason)

    def test_partial_without_a_shortfall_quote_abstains(self) -> None:
        payload = (
            '{"verdicts": [{"index": 0, "status": "partial", "reason": "docs missing",'
            ' "evidence": [{"file": "done.md", "line_start": 1, "line_end": 1,'
            ' "quote": "The CLI is finished"}]}]}'
        )
        out = verdicts_from_reply(payload, ITEMS, DOCS)
        self.assertEqual(out[0].status, "unknown")

    def test_partial_with_an_unverifiable_shortfall_quote_abstains(self) -> None:
        payload = (
            '{"verdicts": [{"index": 0, "status": "partial", "reason": "docs missing",'
            ' "shortfall_quote": "the documentation was never written"}]}'
        )
        self.assertEqual(verdicts_from_reply(payload, ITEMS, DOCS)[0].status, "unknown")

    def test_unknown_status_and_bad_index_are_ignored(self) -> None:
        payload = '{"verdicts": [{"index": 9, "status": "done"}, {"index": 0, "status": "nope"}]}'
        self.assertEqual(verdicts_from_reply(payload, ITEMS, DOCS), {})

    def test_non_json_reply_raises(self) -> None:
        with self.assertRaises(Exception):
            parse_json_object("no json here")

    def test_fenced_json_is_parsed(self) -> None:
        self.assertEqual(parse_json_object('```json\n{"a": 1}\n```'), {"a": 1})

    def test_short_quotes_never_verify(self) -> None:
        self.assertFalse(verify_quote("CLI", DOCS))

    def test_extras_come_from_the_candidate_we_sent(self) -> None:
        candidates = [Evidence("done.md", 2, 2, "Load testing reached 300 rps", 0.0)]
        payload = '{"candidates": [{"index": 0, "unplanned": true, "reason": "unplanned"}]}'
        found = extras_from_reply(payload, DOCS, candidates)
        self.assertEqual([f.evidence.quote for f in found], ["Load testing reached 300 rps"])

    def test_candidates_marked_planned_are_not_reported(self) -> None:
        candidates = [Evidence("done.md", 2, 2, "Load testing reached 300 rps", 0.0)]
        payload = '{"candidates": [{"index": 0, "unplanned": false, "reason": "planned"}]}'
        self.assertEqual(extras_from_reply(payload, DOCS, candidates), [])

    def test_an_index_outside_the_candidate_list_is_ignored(self) -> None:
        payload = '{"candidates": [{"index": 7, "unplanned": true}]}'
        self.assertEqual(extras_from_reply(payload, DOCS, []), [])


class ScoringTest(unittest.TestCase):
    def _verdicts(self, statuses: list[str]) -> list:
        from plandelta.judge import Verdict

        return [Verdict(item=ITEMS[0], status=s) for s in statuses]

    def test_rate_and_coverage_for_mixed_result(self) -> None:
        totals = summarize(self._verdicts(["done", "partial", "missed", "unknown"]))
        self.assertEqual(totals.points, 2)
        self.assertEqual(totals.max_points, 9)
        self.assertEqual(totals.rate, 22.2)
        self.assertEqual(totals.coverage, 75.0)

    def test_all_unknown_reports_zero_coverage_not_zero_delivery(self) -> None:
        totals = summarize(self._verdicts(["unknown", "unknown"]))
        self.assertEqual(totals.coverage, 0.0)
        self.assertEqual(totals.max_points, 0)
        self.assertEqual(totals.rate, 0.0)

    def test_all_missed_clamps_rate_to_zero(self) -> None:
        totals = summarize(self._verdicts(["missed", "missed"]))
        self.assertEqual(totals.rate, 0.0)
        self.assertEqual(totals.penalty, -4)

    def test_exceeded_caps_at_hundred_and_reports_bonus(self) -> None:
        totals = summarize(self._verdicts(["exceeded", "exceeded"]))
        self.assertEqual(totals.rate, 100.0)
        self.assertEqual(totals.bonus, 4)

    def test_extras_are_counted_but_not_scored(self) -> None:
        extra = ExtraFinding(evidence=Evidence("done.md", 1, 1, "q", 0.0), reason="unplanned")
        totals = summarize(self._verdicts(["done"]), [extra])
        self.assertEqual(totals.scope_creep, 1)
        self.assertEqual(totals.points, 3)




class ExtraCandidateTest(unittest.TestCase):
    def _para(self, text: str):
        from plandelta.matcher import Evidence

        return Evidence("done.md", 1, 3, text, 0.0)

    def test_prose_about_delivered_work_is_a_candidate(self) -> None:
        from plandelta.matcher import is_prose

        self.assertTrue(
            is_prose(self._para("We also added a --watch mode that nobody asked for, with tests."))
        )

    def test_headings_and_tables_are_not_candidates(self) -> None:
        from plandelta.matcher import is_prose

        for noise in ("# Completion report for the ingest service work", "| a | b | c | d | e | f |"):
            self.assertFalse(is_prose(self._para(noise)), noise)

    def test_front_matter_and_next_steps_are_not_candidates(self) -> None:
        from plandelta.matcher import is_prose

        for noise in (
            "작성: 2026-09-06 · 코드 커밋: abc1234 · 검증: pytest 210 green 전체 통과함",
            "다음 작업자 첫 액션: 골드셋을 만들고 품질 지표를 숫자로 산출할 것",
        ):
            self.assertFalse(is_prose(self._para(noise)), noise)

    def test_very_short_paragraphs_are_not_candidates(self) -> None:
        from plandelta.matcher import is_prose

        self.assertFalse(is_prose(self._para("Shipped the CLI.")))

    def test_candidates_exclude_filtered_paragraphs(self) -> None:
        from plandelta.matcher import extra_candidates

        paragraphs = [
            self._para("# Some heading that matches nothing at all in this plan"),
            self._para("We also shipped an unplanned export button with its own tests."),
        ]
        found = extra_candidates(ITEMS, paragraphs)
        self.assertEqual([e.quote[:16] for e in found], ["We also shipped "])




class BulletSplitTest(unittest.TestCase):
    def test_a_bullet_list_becomes_one_candidate_per_bullet(self) -> None:
        from plandelta.matcher import split_bullets

        block = Evidence("done.md", 10, 12, "- first thing shipped\n- second thing shipped\n- third", 0.0)
        parts = split_bullets(block)
        self.assertEqual([p.quote for p in parts],
                         ["- first thing shipped", "- second thing shipped", "- third"])
        self.assertEqual([p.line_start for p in parts], [10, 11, 12])

    def test_continuation_lines_stay_with_their_bullet(self) -> None:
        from plandelta.matcher import split_bullets

        block = Evidence("done.md", 5, 8, "- first\n  continued here\n- second\n  also continued", 0.0)
        parts = split_bullets(block)
        self.assertEqual(len(parts), 2)
        self.assertIn("continued here", parts[0].quote)
        self.assertIn("also continued", parts[1].quote)

    def test_prose_without_bullets_is_left_alone(self) -> None:
        from plandelta.matcher import split_bullets

        block = Evidence("done.md", 1, 2, "We shipped the exporter.\nIt streams.", 0.0)
        self.assertEqual(split_bullets(block), [block])

    def test_a_single_bullet_is_left_alone(self) -> None:
        from plandelta.matcher import split_bullets

        block = Evidence("done.md", 1, 1, "- only one bullet here", 0.0)
        self.assertEqual(split_bullets(block), [block])


if __name__ == "__main__":
    unittest.main()
