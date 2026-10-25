"""Validation of prepared publication packages and the publication agent."""

import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from .support import install_fake_tools, problem_id

from lean1.config import RunConfig
from lean1.issues import EXIT_AGENT_STAGE, StopRequested
from lean1.lean import LeanTools
from lean1.notify import EmailConfig
from lean1.publication import (
    REQUIRED_FILES, Author, PublicationError, check_release, publication_prompt, publish_solution,
    repository_name,
)
from lean1.qiqcop import ZooProblem

PROBLEM = ZooProblem(problem_id(0xabcdef), "Honest-party lockability of distillable key", "Unsolved")
GIT = shutil.which("git")


def git(release: Path, *arguments: str) -> None:
    subprocess.run(["git", "-C", str(release), "-c", "user.name=T", "-c", "user.email=t@example.org", *arguments], check=True, capture_output=True)


def build_release(release: Path, commit: bool = True) -> None:
    for relative in REQUIRED_FILES:
        path = release / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("content\n")
    (release / "manuscript.pdf").write_bytes(b"%PDF-1.5\n")
    (release / ".zenodo.json").write_text(json.dumps({"title": "T", "creators": [{"name": "A"}], "description": "D", "license": "mit"}))
    (release / "artifact/source_manifest.json").write_text("{}")
    (release / "artifact/lean").mkdir(parents=True)
    (release / "artifact/lean/lean-toolchain").write_text("leanprover/lean4:v4.19.0\n")
    (release / "artifact/lean/Solution.lean").write_text("theorem t : True := trivial\n")
    (release / "verification/run").mkdir(parents=True)
    (release / "verification/run/result.json").write_text(json.dumps({"status": "PASS"}))
    if commit:
        git(release, "init", "-q", "-b", "main")
        git(release, "add", "-A")
        git(release, "commit", "-q", "-m", "feat: initial release")


@unittest.skipUnless(GIT, "git is required")
class CheckReleaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.release = Path(temporary.name) / "release"

    def test_complete_release_passes(self):
        build_release(self.release)
        self.assertEqual(check_release(self.release), [])

    def test_missing_release(self):
        self.assertIn("does not exist", check_release(self.release)[0])

    def assert_defect(self, expected: str, change, commit_after: bool = True):
        build_release(self.release)
        change(self.release)
        if commit_after:
            git(self.release, "add", "-A")
            git(self.release, "commit", "-q", "--allow-empty", "-m", "chore: change")
        defects = "\n".join(check_release(self.release))
        self.assertIn(expected, defects)

    def test_each_defect_is_reported(self):
        cases = {
            "Missing or empty manuscript.pdf": lambda r: (r / "manuscript.pdf").unlink(),
            "Missing or empty HUMAN-CHECK.md": lambda r: (r / "HUMAN-CHECK.md").write_text(""),
            "manuscript.pdf is not a PDF": lambda r: (r / "manuscript.pdf").write_text("LaTeX failed"),
            ".zenodo.json is not valid JSON": lambda r: (r / ".zenodo.json").write_text("{"),
            ".zenodo.json lacks creators": lambda r: (r / ".zenodo.json").write_text(json.dumps({"title": "T", "description": "D", "license": "mit"})),
            "records a PASS": lambda r: (r / "verification/run/result.json").write_text(json.dumps({"status": "FAIL"})),
            "lean-toolchain is missing": lambda r: (r / "artifact/lean/lean-toolchain").unlink(),
            "Private agent state": lambda r: ((r / ".codex").mkdir(), (r / ".codex/auth.json").write_text("{}")),
            "Generated build output is committed": lambda r: ((r / "artifact/lean/.lake").mkdir(), (r / "artifact/lean/.lake/A.olean").write_text("x")),
        }
        for expected, change in cases.items():
            with self.subTest(expected):
                shutil.rmtree(self.release, ignore_errors=True)
                self.assert_defect(expected, change)

    def test_uncommitted_files_no_repository_and_remotes(self):
        self.assert_defect("uncommitted changes", lambda r: (r / "README.md").write_text("edited\n"), commit_after=False)
        shutil.rmtree(self.release)
        build_release(self.release, commit=False)
        self.assertIn("not a Git repository", "\n".join(check_release(self.release)))
        shutil.rmtree(self.release)
        build_release(self.release)
        git(self.release, "remote", "add", "origin", "https://github.com/example/repo.git")
        self.assertIn("must not have a remote", "\n".join(check_release(self.release)))


class PromptTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workdir = Path(temporary.name)
        self.config = RunConfig(self.workdir, "", LeanTools("/lean", "/lake", None, {}, "Lean 4"), EmailConfig("me@example.org", "mail"), model="gpt-test")

    def test_repository_name_is_a_slug_with_the_problem_id(self):
        self.assertEqual(repository_name(PROBLEM), "honest-party-lockability-of-distillable-key-000000")
        self.assertEqual(repository_name(ZooProblem(problem_id(1), "Ü!", "Unsolved")), "solution-000000")

    def test_author_description(self):
        self.assertIn("AUTHOR NAME", Author().describe())
        described = Author("Ada Lovelace", "Analytical Engines Ltd", "0000-0002-1825-0097").describe()
        for text in ("Ada Lovelace", "Analytical Engines Ltd", "0000-0002-1825-0097"):
            self.assertIn(text, described)

    def test_prompt_contains_problem_author_style_references_and_rules(self):
        record = self.workdir / ".lean1/inputs/qiqcop-record.json"
        review = self.workdir / ".lean1/review/review-0001-1.json"
        prompt = publication_prompt(self.config, PROBLEM, record, review, Author("Ada Lovelace"), feedback="- Missing manuscript.pdf.")
        for text in (
            PROBLEM.url, PROBLEM.json_url, ".lean1/inputs/qiqcop-record.json", ".lean1/review/review-0001-1.json",
            "Ada Lovelace", "gpt-test", "https://zenodo.org/records/23215121",
            "https://github.com/vimohr/superchannel-amortization", "Do not add a remote, push",
            "never invent a DOI", "- Missing manuscript.pdf.", "PUBLICATION_STATUS: READY",
            "honest-party-lockability-of-distillable-key-000000",
        ):
            self.assertIn(text, prompt)
        self.assertNotIn("{{", prompt)
        self.assertNotIn("Feedback from the supervisor", publication_prompt(self.config, PROBLEM, record, review, Author()))


@unittest.skipUnless(GIT, "git is required")
class PublicationAgentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="lean1-publication-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.enterContext(contextlib.chdir(self.root))
        install_fake_tools(self.root / "bin")
        self.workdir = self.root / "work"
        (self.workdir / ".lean1/inputs").mkdir(parents=True)
        (self.workdir / "Solution.lean").write_text("theorem t : True := trivial\n")
        self.env = {"PATH": os.environ.get("PATH", ""), "LEAN1_TEST_ROOT": str(self.root), "CODEX_HOME": str(self.root / "seed")}
        self.config = RunConfig(self.workdir, "", LeanTools("lean", "lake", None, self.env, "Lean 4"), EmailConfig("me@example.org", "mail"), codex=str(self.root / "bin/codex"))

    def publish(self, mode: str) -> Path:
        config = replace(self.config, tools=replace(self.config.tools, env=self.env | {"LEAN1_TEST_PUBLISH": mode}))
        with contextlib.redirect_stdout(io.StringIO()):
            return publish_solution(
                config, problem=PROBLEM, record_file=self.workdir / ".lean1/inputs/qiqcop-record.json",
                review_file=self.workdir / ".lean1/review/review.json", author=Author("Ada Lovelace"),
            )

    def test_ready_package(self):
        release = self.publish("ok")
        self.assertEqual(release, self.workdir / "release")
        self.assertEqual(check_release(release), [])
        self.assertEqual((self.root / "publish-count").read_text(), "1")

    def test_defects_are_fed_back_to_a_second_attempt(self):
        self.publish("incomplete-once")
        self.assertEqual((self.root / "publish-count").read_text(), "2")
        self.assertIn("Missing or empty manuscript.pdf.", (self.root / "publish-prompt-2.txt").read_text())

    def test_stop_during_health_retry_is_not_an_issue(self):
        with patch("lean1.publication.ensure_codex_healthy", return_value=False), self.assertRaises(StopRequested):
            self.publish("ok")
        self.assertFalse((self.root / "publish-count").exists())

    def test_persistent_defects_stop_with_an_issue(self):
        with self.assertRaises(PublicationError) as raised:
            self.publish("incomplete")
        self.assertEqual(raised.exception.exit_code, EXIT_AGENT_STAGE)
        self.assertIn("manuscript.pdf", raised.exception.detail)


if __name__ == "__main__":
    unittest.main()
