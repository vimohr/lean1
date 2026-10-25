"""Shared file and template helpers."""

import tempfile
import unittest
from pathlib import Path

from .support import REPO  # noqa: F401

from lean1.common import atomic_write, read_tail, render_template


class CommonTests(unittest.TestCase):
    def test_render_template_substitutes_once_and_keeps_unknown_braces(self):
        rendered = render_template("A {{X}} {{Y}} {{Z}} {}", {"X": "{{Y}}", "Y": "y"})
        self.assertEqual(rendered, "A {{Y}} y {{Z}} {}")

    def test_atomic_write_and_tail(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "nested" / "file.txt"
            atomic_write(path, "abcdef")
            self.assertEqual(path.read_text(), "abcdef")
            self.assertEqual(read_tail(path, 3), "def")
            self.assertEqual(list(path.parent.iterdir()), [path])


if __name__ == "__main__":
    unittest.main()
