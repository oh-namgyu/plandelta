import json
import unittest
from pathlib import Path

from plandelta.discovery import MANIFEST_NAME, discover, resolve_in_root
from plandelta.errors import PathDenied

ROOT = Path(__file__).parent / "fixtures" / "sample_root"


class DiscoveryTest(unittest.TestCase):
    def test_finds_pairs_with_primary_document(self) -> None:
        result = discover(ROOT)
        ids = sorted(p.id for p in result.pairs)
        self.assertEqual(ids, ["bundle-multi", "checkbox-app", "prose-plan", "xss-payload"])

    def test_plan_without_completion_is_skipped_with_reason(self) -> None:
        result = discover(ROOT)
        skipped = {entry["id"]: entry["reason"] for entry in result.skipped}
        self.assertEqual(skipped, {"orphan-plan": "no primary completion document"})

    def test_only_delivery_documents_join_the_bundle(self) -> None:
        """Review notes and approval metadata are not delivery evidence."""
        pair = next(p for p in discover(ROOT).pairs if p.id == "bundle-multi")
        self.assertEqual([p.name for p in pair.done], ["bundle-multi_completion.md"])

    def test_extra_documents_can_be_opted_in(self) -> None:
        pair = next(
            p
            for p in discover(ROOT, supplementary_suffixes=("_verify.md",)).pairs
            if p.id == "bundle-multi"
        )
        self.assertEqual(
            [p.name for p in pair.done],
            ["bundle-multi_completion.md", "bundle-multi_verify.md"],
        )

    def test_manifest_takes_priority(self) -> None:
        manifest = ROOT / MANIFEST_NAME
        manifest.write_text(
            json.dumps(
                [{"id": "only", "plan": "prose-plan_final.md", "done": ["prose-plan_completion.md"]}]
            ),
            encoding="utf-8",
        )
        try:
            result = discover(ROOT)
            self.assertEqual([p.id for p in result.pairs], ["only"])
        finally:
            manifest.unlink()

    def test_path_outside_root_is_denied(self) -> None:
        with self.assertRaises(PathDenied):
            resolve_in_root(ROOT, Path("../../etc/hosts"))


if __name__ == "__main__":
    unittest.main()
