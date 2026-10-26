"""Parsing of the prior-work verdict in result.md."""

import tempfile
import unittest
from pathlib import Path

from . import support  # noqa: F401  (puts src/ on sys.path)

from lean1.prior import SECTION_LIMIT, read_prior_work


class PriorWorkTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="lean1-prior-")
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)

    def write(self, text):
        (self.workspace / "result.md").write_text(text)

    def test_missing_file_or_verdict_gives_none(self):
        self.assertIsNone(read_prior_work(self.workspace))
        self.write("# Result\n\nA proof without a prior-work section.\n")
        self.assertIsNone(read_prior_work(self.workspace))

    def test_each_status_is_recognized_with_markdown_decoration(self):
        cases = {
            "Prior resolution: NONE FOUND": "NONE FOUND",
            "**Prior resolution:** PARTIAL": "PARTIAL",
            "- `Prior resolution: COMPLETE`": "COMPLETE",
            "Prior resolution: **complete**": "COMPLETE",
            "Prior resolution:  none   found": "NONE FOUND",
        }
        for line, status in cases.items():
            with self.subTest(line=line):
                self.write(f"## Prior work\n\n{line}\n")
                prior = read_prior_work(self.workspace)
                self.assertEqual(prior.status, status)
                self.assertEqual(prior.solved, status == "COMPLETE")

    def test_only_the_first_verdict_counts(self):
        self.write("## Prior work\n\nPrior resolution: NONE FOUND\n\n## Notes\n\nPrior resolution: COMPLETE\n")
        self.assertFalse(read_prior_work(self.workspace).solved)

    def test_section_ends_at_the_next_heading_of_the_same_level(self):
        self.write(
            "# Report\n\n## Prior work\n\nPrior resolution: COMPLETE\n\n### Sources\n\n"
            "Doe, J. A full answer. arXiv:2501.00001 (2025).\n\n## Problem statement\n\nUnrelated.\n"
        )
        section = read_prior_work(self.workspace).section
        self.assertTrue(section.startswith("## Prior work"))
        self.assertIn("arXiv:2501.00001", section)
        self.assertNotIn("Unrelated", section)

    def test_verdict_without_heading_and_long_sections_are_handled(self):
        self.write("Prior resolution: COMPLETE\n" + "x" * (SECTION_LIMIT + 100) + "\n# Next\n")
        section = read_prior_work(self.workspace).section
        self.assertTrue(section.startswith("Prior resolution: COMPLETE"))
        self.assertIn("[truncated; see result.md]", section)
        self.assertNotIn("# Next", section)


if __name__ == "__main__":
    unittest.main()
