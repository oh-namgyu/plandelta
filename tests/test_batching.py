import unittest

from plandelta import judge
from plandelta.compare import _batches
from plandelta.extract import PlanItem
from plandelta.matcher import Evidence

# What one item can cost in a prompt: its body and its quotes are both truncated,
# so the ceiling is bounded no matter how large the source document is.
MAX_ITEM_COST = judge.MAX_BODY_CHARS + 5 * judge.MAX_QUOTE_CHARS


def item(key: str, body_chars: int) -> PlanItem:
    return PlanItem(key, f"item {key}", "x" * body_chars, "S", 1, 1, "checkbox")


def with_full_evidence(items: list[PlanItem]) -> dict[str, list[Evidence]]:
    quote = "y" * judge.MAX_QUOTE_CHARS
    return {i.key: [Evidence("f", 1, 2, quote, 1.0) for _ in range(5)] for i in items}


class BatchingTest(unittest.TestCase):
    def test_short_items_fill_up_to_the_count_limit(self) -> None:
        items = [item(str(i), 50) for i in range(25)]
        sizes = [len(b) for b in _batches(items, {})]
        self.assertEqual(sizes, [judge.BATCH_SIZE, judge.BATCH_SIZE, 5])

    def test_evidence_heavy_items_split_before_the_count_limit(self) -> None:
        items = [item(str(i), judge.MAX_BODY_CHARS) for i in range(10)]
        sizes = [len(b) for b in _batches(items, with_full_evidence(items))]
        self.assertLess(max(sizes), judge.BATCH_SIZE)
        self.assertEqual(sum(sizes), 10)

    def test_every_batch_stays_within_one_item_of_the_budget(self) -> None:
        items = [item(str(i), judge.MAX_BODY_CHARS) for i in range(12)]
        evidence = with_full_evidence(items)
        for batch in _batches(items, evidence):
            cost = sum(
                len(i.body[: judge.MAX_BODY_CHARS])
                + sum(len(e.quote[: judge.MAX_QUOTE_CHARS]) for e in evidence[i.key])
                for i in batch
            )
            self.assertLessEqual(cost - MAX_ITEM_COST, judge.BATCH_CHAR_BUDGET, len(batch))

    def test_truncation_caps_the_cost_of_a_huge_body(self) -> None:
        """A whole-section item body is truncated, so it cannot blow up a batch alone."""
        items = [item(str(i), judge.MAX_BODY_CHARS * 20) for i in range(5)]
        self.assertEqual([len(b) for b in _batches(items, {})], [5])

    def test_no_items_means_no_batches(self) -> None:
        self.assertEqual(_batches([], {}), [])


if __name__ == "__main__":
    unittest.main()
