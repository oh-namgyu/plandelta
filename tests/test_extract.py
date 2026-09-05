import unittest
from pathlib import Path

from plandelta.extract import extract_items
from plandelta.hashing import item_key, read_text

ROOT = Path(__file__).parent / "fixtures" / "sample_root"


class ExtractTest(unittest.TestCase):
    def test_checkbox_and_ordered_items_are_extracted(self) -> None:
        items = extract_items(read_text(ROOT / "checkbox-app_final.md"))
        titles = [i.title for i in items]
        self.assertIn("CLI accepts a plan file and prints JSON", titles)
        self.assertIn("Build the parser (verify: unit tests)", titles)
        self.assertEqual(len(items), 6)

    def test_prose_plan_falls_back_to_headings(self) -> None:
        items = extract_items(read_text(ROOT / "prose-plan_final.md"))
        self.assertEqual([i.kind for i in items], ["heading"] * len(items))
        self.assertEqual([i.title for i in items], ["Goal", "Storage design", "Observability"])

    def test_item_key_is_stable_across_body_edits(self) -> None:
        original = read_text(ROOT / "prose-plan_final.md")
        edited = original.replace("Rows land in SQLite", "Rows are written to SQLite")
        before = {i.title: i.key for i in extract_items(original)}
        after = {i.title: i.key for i in extract_items(edited)}
        self.assertEqual(before, after)

    def test_item_key_matches_section_and_title(self) -> None:
        items = extract_items(read_text(ROOT / "bundle-multi_final.md"))
        first = items[0]
        self.assertEqual(first.key, item_key(first.section, first.title))

    def test_source_lines_point_at_the_item(self) -> None:
        text = read_text(ROOT / "bundle-multi_final.md")
        items = extract_items(text)
        lines = text.split("\n")
        for item in items:
            self.assertIn(item.title, lines[item.line_start - 1])

    def test_code_fences_are_ignored(self) -> None:
        text = "# T\n\n## S\n\n```\n- [ ] not an item\n```\n\n- [ ] real item\n"
        titles = [i.title for i in extract_items(text)]
        self.assertEqual(titles, ["real item"])


if __name__ == "__main__":
    unittest.main()
