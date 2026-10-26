"""Retry failsafe checks with a simulated clock and local attempt results."""

import contextlib
import io
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
import unittest.mock
from unittest.mock import patch

from .support import REPO

from lean1.lean import LeanTools
from lean1.notify import EmailConfig
from lean1.config import Acceptance
from lean1.runner import PRIOR_SOLUTION_NOTE, RunConfig, is_complete, load_prompt, supervise


class RetryFailsafeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="lean1-retries-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.enterContext(contextlib.chdir(self.root))
        self.config = RunConfig(
            self.root / "work", "Prove True.",
            LeanTools("lean", "lake", None, {}, "Lean 4"),
            EmailConfig("me@example.org", "mail"), restart_delay=0, notify="complete",
        )

    def run_attempts(self, attempts, *, max_runs=0, notify="complete", started=0, result=None, **options):
        """Each attempt supplies its runtime, exit code, message, and completion.

        `result`, if given, is the result.md text every attempt writes.
        """
        pending = iter(attempts)
        now = started

        def run(config, run_number):
            nonlocal now
            duration, exit_code, message, complete = next(pending)
            now += duration
            if result is not None:
                (config.workdir / "result.md").write_text(result)
            return {
                "run_number": run_number, "exit_code": exit_code,
                "duration_seconds": duration, "final_message": message,
                "task_complete": complete, "verification": {"success": complete},
                "stderr": "Agent startup failed." if exit_code else "",
            }

        console = io.StringIO()
        config = replace(self.config, max_runs=max_runs, notify=notify, **options)
        with patch("lean1.runner.run_codex", side_effect=run) as codex, patch("lean1.runner.time.monotonic", side_effect=lambda: now), patch.object(EmailConfig, "send", return_value=None) as email, contextlib.redirect_stdout(console):
            result = supervise(config)
        output = json.loads((config.workdir / "codex_last_output.json").read_text())
        return result, output, console.getvalue(), codex, email

    def test_nine_failures_do_not_trigger_the_failsafe(self):
        result, output, console, codex, _ = self.run_attempts(
            [(1, 7, None, False)] * 9, max_runs=9,
        )
        self.assertEqual(result, 1)
        self.assertEqual(codex.call_count, 9)
        self.assertNotIn("stop_reason", output)
        self.assertNotIn("Failsafe:", console)

    def test_tenth_failure_stops_even_when_the_window_crosses_a_minute(self):
        result, output, console, codex, email = self.run_attempts(
            [(1, 7, None, False)] * 10, notify="all", started=55,
        )
        self.assertEqual(result, 1)
        self.assertEqual(codex.call_count, 10)
        self.assertIn("10 times within 60 seconds", output["stop_reason"])
        self.assertIn("Failsafe:", console)
        self.assertIn("stopped incomplete", email.call_args.args[2])
        self.assertIn(output["stop_reason"], email.call_args.args[3])
        self.assertIn(output["stop_reason"], (self.config.workdir / "codex_runs.log").read_text())
        self.assertFalse((self.config.workdir / ".lean1/active_run.json").exists())
        self.assertEqual(list((self.root / ".lean1/active-runs").glob("*.json")), [])

    def test_old_failures_expire_before_a_later_burst(self):
        attempts = (
            [(0, 7, None, False)] * 9
            + [(61, 0, "Research in progress.", False)]
            + [(0, 7, None, False)] * 10
        )
        result, output, _, codex, email = self.run_attempts(attempts)
        self.assertEqual(result, 1)
        self.assertEqual(codex.call_count, 20)
        self.assertIn("stop_reason", output)
        # Only the failsafe stop is reported with --notify=complete.
        email.assert_called_once()
        self.assertIn("stopped incomplete", email.call_args.args[2])

    def test_failures_spread_over_more_than_a_minute_keep_retrying(self):
        result, output, _, codex, _ = self.run_attempts(
            [(7, 7, None, False)] * 12, max_runs=12,
        )
        self.assertEqual(result, 1)
        self.assertEqual(codex.call_count, 12)
        self.assertNotIn("stop_reason", output)

    def test_successful_incomplete_attempts_do_not_count_as_failures(self):
        result, output, _, codex, _ = self.run_attempts(
            [(0, 0, "Research or Lean verification needs more work.", False)] * 12,
            max_runs=12,
        )
        self.assertEqual(result, 1)
        self.assertEqual(codex.call_count, 12)
        self.assertNotIn("stop_reason", output)

    def test_empty_final_responses_count_even_with_a_zero_exit_code(self):
        for message in (None, "", " \n "):
            with self.subTest(message=message):
                result, output, _, codex, _ = self.run_attempts(
                    [(0, 0, message, False)] * 10,
                )
                self.assertEqual(result, 1)
                self.assertEqual(codex.call_count, 10)
                self.assertIn("stop_reason", output)

    def test_verified_completion_after_nine_failures_is_accepted(self):
        attempts = [(0, 7, None, False)] * 9 + [(0, 0, "Solved.", True)]
        result, output, _, codex, email = self.run_attempts(attempts)
        self.assertEqual(result, 0)
        self.assertEqual(codex.call_count, 10)
        self.assertTrue(output["task_complete"])
        self.assertNotIn("stop_reason", output)
        self.assertIn("completed task", email.call_args.args[2])


class PriorSolutionTests(RetryFailsafeTests):
    """Stopping when result.md reports that an existing source resolves the problem."""

    SOLVED = "## Prior work\n\nPrior resolution: COMPLETE\n\nDoe, J. A full answer. arXiv:2501.00001.\n"

    def test_reported_prior_solution_stops_without_review(self):
        review = unittest.mock.Mock(return_value=Acceptance(True, "accepted"))
        result, output, console, codex, email = self.run_attempts(
            [(5, 0, "Solved.\nTASK_STATUS: COMPLETE\nLEAN_VERIFIED: YES", True)] * 3,
            result=self.SOLVED, stop_on_prior_solution=True, acceptance=review,
        )
        self.assertEqual(result, 0)
        self.assertEqual(codex.call_count, 1)
        review.assert_not_called()
        self.assertEqual(output["prior_work"]["status"], "COMPLETE")
        self.assertIn("arXiv:2501.00001", output["prior_work"]["section"])
        self.assertIn("existing source completely resolves", output["stop_reason"])
        self.assertIn("already resolves the problem", console)
        email.assert_not_called()

    def test_partial_or_disabled_check_keeps_working(self):
        for result, enabled in ((self.SOLVED.replace("COMPLETE", "PARTIAL"), True), (self.SOLVED, False)):
            with self.subTest(enabled=enabled):
                code, output, _, codex, _ = self.run_attempts(
                    [(5, 0, "Still working.", False)] * 2, max_runs=2, result=result, stop_on_prior_solution=enabled,
                )
                self.assertEqual(code, 1)
                self.assertEqual(codex.call_count, 2)
                self.assertNotIn("prior_work", output)

    def test_prompt_note_appears_only_when_enabled(self):
        self.assertNotIn(PRIOR_SOLUTION_NOTE.strip(), load_prompt(self.config))
        self.assertIn(PRIOR_SOLUTION_NOTE.strip(), load_prompt(replace(self.config, stop_on_prior_solution=True)))


class PromptTests(unittest.TestCase):
    def test_prompt_copies_match_and_problem_placeholders_are_literal(self):
        self.assertEqual((REPO / "prompt.txt").read_text(), (REPO / "src/lean1/prompt.txt").read_text())
        with tempfile.TemporaryDirectory() as temporary:
            tools = LeanTools("/lean", "/lake", None, {}, "Lean 4")
            config = RunConfig(Path(temporary), "Prove {{LEAN_COMMAND}} is literal.", tools, EmailConfig("me@example.org", "mail"))
            rendered = load_prompt(config)
            self.assertIn("Prove {{LEAN_COMMAND}} is literal.", rendered)
            self.assertIn("Lean: /lean", rendered)
            self.assertNotIn("L(n,k,m)", rendered)

    def test_completion_requires_exact_final_marker_lines(self):
        markers = "TASK_STATUS: COMPLETE\nLEAN_VERIFIED: YES"
        self.assertTrue(is_complete("A result.\n" + markers + "\n"))
        self.assertFalse(is_complete("Do not use " + markers))
        self.assertFalse(is_complete(markers + "\nBut actually incomplete."))
        self.assertFalse(is_complete(None))


if __name__ == "__main__":
    unittest.main()
