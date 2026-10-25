"""Problem input detection, copying, and website snapshots."""

import io
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch

from .support import REPO  # noqa: F401  (adds src/ to the import path)

from lean1.common import RunnerError
from lean1.problems import prepare_problem, resolve_problem


class InputTests(unittest.TestCase):
    def test_text_url_file_and_forced_literal(self):
        with tempfile.TemporaryDirectory() as temporary:
            problem_file = Path(temporary) / "problem.md"
            problem_file.write_text("Prove α = α.", encoding="utf-8")
            self.assertEqual(resolve_problem("Prove α = α.").kind, "text")
            self.assertEqual(resolve_problem("https://example.org/problem").kind, "url")
            self.assertEqual(resolve_problem(str(problem_file)).kind, "file")
            self.assertEqual(resolve_problem("@" + str(problem_file)).kind, "file")
            self.assertEqual(resolve_problem(str(problem_file), "text").kind, "text")
            with self.assertRaises(RunnerError):
                resolve_problem("./not-a-real-problem.md")
            with self.assertRaises(RunnerError):
                resolve_problem("file:///etc/passwd", "url")
            with patch("sys.stdin", io.StringIO("Prove True.")):
                self.assertEqual(resolve_problem("-", "file").content, "Prove True.")

    def test_binary_file_is_copied_for_agent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "problem.pdf"
            source.write_bytes(b"%PDF-1.7\x00\xff")
            workdir = root / "work"
            workdir.mkdir()
            rendered = prepare_problem(resolve_problem(str(source)), workdir)
            self.assertIn("binary document", rendered)
            self.assertEqual((workdir / ".lean1/inputs/problem.pdf").read_bytes(), source.read_bytes())

    def test_website_snapshot_and_offline_fallback(self):
        headers = Message()
        headers["Content-Type"] = "text/html; charset=utf-8"

        class Response(io.BytesIO):
            def geturl(self):
                return "https://example.org/problem"

        with tempfile.TemporaryDirectory() as temporary:
            workdir = Path(temporary)
            response = Response(b"<h1>Prove this</h1><p>x = x</p><script>secret script</script>")
            response.headers = headers
            with patch("lean1.problems.urlopen", return_value=response):
                prompt = prepare_problem(resolve_problem("https://example.org/problem"), workdir)
            self.assertIn("Prove this", prompt)
            self.assertIn("x = x", prompt)
            self.assertNotIn("secret script", prompt)
            self.assertTrue((workdir / ".lean1/inputs/website.html").exists())
            with patch("lean1.problems.urlopen", side_effect=OSError("offline")):
                prompt = prepare_problem(resolve_problem("https://example.org/problem"), workdir)
            self.assertIn("Use browsing to open the link", prompt)


if __name__ == "__main__":
    unittest.main()
