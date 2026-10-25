"""Independent review of verified solutions and its effect on the research loop."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from dataclasses import replace
from functools import partial
from pathlib import Path
from unittest.mock import patch

from .support import install_fake_tools, problem_id

from lean1.config import Acceptance, RunConfig
from lean1.issues import EXIT_AGENT_STAGE, EXIT_STOPPED, StopRequested
from lean1.lean import LeanTools
from lean1.notify import EmailConfig
from lean1.qiqcop import ZooProblem
from lean1.review import (
    ACCEPT_MARKER, REJECT_MARKER, ReviewError, changed_files, parse_verdict, protected_files,
    review_prompt, review_solution,
)
from lean1.runner import supervise

PROBLEM = ZooProblem(problem_id(42), "Example problem", "Unsolved")


class VerdictTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.report = Path(temporary.name) / "review.json"

    def write(self, **values):
        report = {"problem_id": PROBLEM.id, "verdict": "accept", "summary": "Matches.", "issues": []} | values
        self.report.write_text(json.dumps(report))

    def test_accept_and_reject(self):
        self.write()
        verdict = parse_verdict(f"Done.\n{ACCEPT_MARKER}\n", self.report, PROBLEM.id)
        self.assertTrue(verdict.accepted)
        self.write(verdict="reject", summary="Special case only.", issues=["n is fixed"])
        verdict = parse_verdict(f"Done.\n{REJECT_MARKER}", self.report, PROBLEM.id)
        self.assertFalse(verdict.accepted)
        self.assertEqual(verdict.issues, ["n is fixed"])

    def test_inconsistent_or_incomplete_verdicts_are_rejected(self):
        cases = [
            ("no marker", f"{ACCEPT_MARKER} but with caveats", {}),
            ("marker not last", f"{ACCEPT_MARKER}\nActually unsure.", {}),
            ("disagreement", REJECT_MARKER, {}),
            ("other problem", ACCEPT_MARKER, {"problem_id": problem_id(7)}),
            ("unknown verdict", ACCEPT_MARKER, {"verdict": "maybe"}),
            ("no summary", ACCEPT_MARKER, {"summary": " "}),
            ("bad issues", ACCEPT_MARKER, {"issues": "none"}),
            ("reject without issues", REJECT_MARKER, {"verdict": "reject"}),
        ]
        for label, message, values in cases:
            with self.subTest(label):
                self.write(**values)
                with self.assertRaises(ReviewError) as raised:
                    parse_verdict(message, self.report, PROBLEM.id)
                self.assertEqual(raised.exception.exit_code, EXIT_AGENT_STAGE)

    def test_missing_invalid_or_non_object_reports(self):
        with self.assertRaisesRegex(ReviewError, "did not write"):
            parse_verdict(ACCEPT_MARKER, self.report, PROBLEM.id)
        for content in ("{not json", "[1, 2]"):
            self.report.write_text(content)
            with self.subTest(content=content), self.assertRaises(ReviewError):
                parse_verdict(ACCEPT_MARKER, self.report, PROBLEM.id)


class ProtectedFileTests(unittest.TestCase):
    def test_only_changes_to_existing_solution_files_count(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for relative in ("Solution.lean", "result.md", "lean_verification.json", "Proj/A.lean", "notes.txt",
                             ".lean1/review/Scratch.lean", ".lake/build/B.lean", "release/artifact/C.lean"):
                (root / relative).parent.mkdir(parents=True, exist_ok=True)
                (root / relative).write_text(relative)
            before = protected_files(root)
            self.assertEqual(set(before), {"Solution.lean", "result.md", "lean_verification.json", "Proj/A.lean"})
            (root / "notes.txt").write_text("changed")
            (root / ".lean1/review/Scratch.lean").write_text("changed")
            (root / "New.lean").write_text("scratch")
            self.assertEqual(changed_files(before, protected_files(root)), [])
            (root / "Proj/A.lean").write_text("changed")
            (root / "result.md").unlink()
            self.assertEqual(changed_files(before, protected_files(root)), ["Proj/A.lean", "result.md"])


class ReviewAgentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="lean1-review-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.enterContext(contextlib.chdir(self.root))
        self.bin = self.root / "bin"
        install_fake_tools(self.bin)
        self.workdir = self.root / "work"
        (self.workdir / ".lean1/inputs").mkdir(parents=True)
        self.record = self.workdir / ".lean1/inputs/qiqcop-record.json"
        self.record.write_text(json.dumps({"id": PROBLEM.id}))
        (self.workdir / "Solution.lean").write_text("namespace Solution\ntheorem answer : 1 = 1 := by rfl\nend Solution\n")
        self.env = {"PATH": os.environ.get("PATH", ""), "LEAN1_TEST_ROOT": str(self.root), "CODEX_HOME": str(self.root / "seed")}
        self.config = RunConfig(
            self.workdir, f"Solve this problem {PROBLEM.url}. Make no mistakes.",
            LeanTools("lean", "lake", None, self.env, "Lean 4"),
            EmailConfig("me@example.org", "mail"), codex=str(self.bin / "codex"), restart_delay=0,
        )

    def review(self, mode: str, run_number: int = 1):
        config = replace(self.config, tools=replace(self.config.tools, env=self.env | {"LEAN1_TEST_REVIEW": mode}))
        with contextlib.redirect_stdout(io.StringIO()):
            return review_solution(config, {"run_number": run_number}, problem=PROBLEM, record_file=self.record)

    def test_prompt_names_the_website_statement_record_and_report(self):
        report = self.workdir / ".lean1/review/review-0001-1.json"
        prompt = review_prompt(self.config, PROBLEM, self.record, report)
        self.assertIn(f"Assigned problem: Solve this problem {PROBLEM.url}. Make no mistakes.", prompt)
        self.assertIn(PROBLEM.json_url, prompt)
        self.assertIn("Saved copy of that record: .lean1/inputs/qiqcop-record.json", prompt)
        self.assertIn("Write your verdict to .lean1/review/review-0001-1.json as a JSON object", prompt)
        self.assertNotIn("{{", prompt)

    def test_accepting_review(self):
        decision = self.review("accept")
        self.assertIsInstance(decision, Acceptance)
        self.assertTrue(decision.accepted)
        self.assertTrue(Path(decision.report).is_file())

    def test_rejecting_review_returns_actionable_feedback(self):
        decision = self.review("reject", run_number=3)
        self.assertFalse(decision.accepted)
        self.assertIn("the website asks for every n", decision.feedback)
        self.assertIn("review-0003-1.json", decision.report)

    def test_invalid_output_is_retried_then_stops_with_an_issue(self):
        for mode in ("garbage", "crash"):
            with self.subTest(mode=mode):
                with self.assertRaises(ReviewError) as raised:
                    self.review(mode)
                self.assertIn("2 attempts", str(raised.exception))
        self.assertEqual((self.root / "review-count").read_text(), "4")

    def test_reviewer_editing_the_solution_invalidates_the_review(self):
        with self.assertRaisesRegex(ReviewError, "modified solution files") as raised:
            self.review("tamper")
        self.assertIn("Solution.lean", raised.exception.detail)
        self.assertEqual((self.root / "review-count").read_text(), "1")


class SupervisorAcceptanceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="lean1-acceptance-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.enterContext(contextlib.chdir(self.root))
        self.config = RunConfig(
            self.root / "work", "Prove it.", LeanTools("lean", "lake", None, {}, "Lean 4"),
            EmailConfig("me@example.org", "mail"), restart_delay=0, notify="complete",
        )

    def run_with(self, decisions, attempts=3):
        outputs = [{
            "run_number": number, "exit_code": 0, "duration_seconds": 0, "task_complete": True,
            "verification": {"success": True}, "final_message": "Solved.", "stderr": "",
        } for number in range(1, attempts + 1)]
        decisions = iter(decisions)
        hook = lambda config, output: next(decisions)  # noqa: E731
        config = replace(self.config, acceptance=hook, max_runs=attempts)
        with patch("lean1.runner.run_codex", side_effect=outputs) as codex, patch.object(EmailConfig, "send", return_value=None) as email, contextlib.redirect_stdout(io.StringIO()):
            code = supervise(config)
        return code, codex, email

    def test_rejection_is_fed_back_and_a_later_acceptance_completes(self):
        code, codex, email = self.run_with([
            Acceptance(False, "Only a special case.", "Reviewer: prove it for every n.", "r1.json"),
            Acceptance(True, "Matches the website.", report="r2.json"),
        ])
        self.assertEqual(code, 0)
        self.assertEqual(codex.call_count, 2)
        email.assert_called_once()
        self.assertIn("completed task after run #2", email.call_args.args[2])
        self.assertIn("Matches the website.", email.call_args.args[3])
        output = json.loads((self.config.workdir / "codex_last_output.json").read_text())
        self.assertEqual(output["acceptance"]["report"], "r2.json")

    def test_feedback_from_a_rejection_reaches_the_next_prompt(self):
        code, _, email = self.run_with([Acceptance(False, "No.", "Reviewer: prove it for every n.", "r.json")], attempts=1)
        self.assertEqual(code, 1)
        email.assert_not_called()
        feedback = (self.config.workdir / ".lean1/verification_feedback.txt").read_text()
        self.assertIn("Reviewer: prove it for every n.", feedback)
        output = json.loads((self.config.workdir / "codex_last_output.json").read_text())
        self.assertFalse(output["task_complete"])
        self.assertFalse(output["acceptance"]["accepted"])

    def test_stop_while_waiting_for_the_reviewer_is_graceful(self):
        def waiting(config, output):
            raise StopRequested

        output = {"run_number": 1, "exit_code": 0, "duration_seconds": 0, "task_complete": True, "verification": {}, "final_message": "Solved.", "stderr": ""}
        with patch("lean1.runner.run_codex", return_value=output), patch.object(EmailConfig, "send_named") as email, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(supervise(replace(self.config, acceptance=waiting)), EXIT_STOPPED)
        email.assert_not_called()

    def test_review_issue_stops_the_loop_and_emails(self):
        def failing(config, output):
            raise ReviewError("the review agent failed to produce a valid verdict in 2 attempts")

        with patch("lean1.runner.run_codex", return_value={"run_number": 1, "exit_code": 0, "duration_seconds": 0, "task_complete": True, "verification": {}, "final_message": "Solved.", "stderr": ""}), patch.object(EmailConfig, "send_named", return_value=None) as email, contextlib.redirect_stdout(io.StringIO()):
            code = supervise(replace(self.config, acceptance=partial(failing)))
        self.assertEqual(code, EXIT_AGENT_STAGE)
        self.assertIn("review agent failed", email.call_args.args[2])


if __name__ == "__main__":
    unittest.main()
