"""Retry failsafe checks with a simulated clock and local attempt results."""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from lean1.lean import LeanTools
from lean1.notify import EmailConfig
from lean1.runner import RunConfig, supervise


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

    def run_attempts(self, attempts, *, max_runs=0, notify="complete", started=0):
        """Each attempt supplies its runtime, exit code, message, and completion."""
        pending = iter(attempts)
        now = started

        def run(config, run_number):
            nonlocal now
            duration, exit_code, message, complete = next(pending)
            now += duration
            return {
                "run_number": run_number, "exit_code": exit_code,
                "duration_seconds": duration, "final_message": message,
                "task_complete": complete, "verification": {"success": complete},
                "stderr": "Agent startup failed." if exit_code else "",
            }

        console = io.StringIO()
        config = replace(self.config, max_runs=max_runs, notify=notify)
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
        email.assert_not_called()

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


if __name__ == "__main__":
    unittest.main()
