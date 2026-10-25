"""The Codex health check before every execution, and pausing when it fails."""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from .support import install_fake_tools

from lean1.config import HealthCheckConfig, RunConfig
from lean1.health import HEALTH_PROMPT, HEALTH_TOKEN, HealthResult, ensure_codex_healthy, run_health_check
from lean1.issues import EXIT_HEALTH_CHECK, SupervisorIssue
from lean1.lean import LeanTools
from lean1.notify import EmailConfig
from lean1.runner import supervise


def result(ok: bool, detail: str = "detail") -> HealthResult:
    return HealthResult(ok, detail, 0.1, 0 if ok else 1, "2026-01-01T00:00:00")


class HealthCheckTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="lean1-health-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.enterContext(contextlib.chdir(self.root))
        self.bin = self.root / "bin"
        install_fake_tools(self.bin)
        env = {"PATH": os.environ.get("PATH", ""), "LEAN1_TEST_ROOT": str(self.root), "CODEX_HOME": str(self.root / "seed")}
        self.config = RunConfig(
            self.root / "work", "Prove True.", LeanTools("lean", "lake", None, env, "Lean 4"),
            EmailConfig("me@example.org", "mail"), codex=str(self.bin / "codex"),
            health=HealthCheckConfig(timeout=30, retries=0, retry_delay=0),
        )
        self.config.workdir.mkdir()

    def test_prompt_asks_for_the_token_only(self):
        self.assertIn(HEALTH_TOKEN, HEALTH_PROMPT)
        self.assertIn("Do not run commands", HEALTH_PROMPT)

    def test_successful_check_uses_workspace_codex_home_outside_the_solution_files(self):
        checked = run_health_check(self.config, self.config.health)
        self.assertTrue(checked.ok, checked.detail)
        command = json.loads((self.root / "commands.jsonl").read_text().splitlines()[-1])
        self.assertEqual(command["codex_home"], str((self.config.workdir / ".codex").resolve()))
        self.assertEqual(Path(command["cwd"]), (self.config.workdir / ".lean1" / "health").resolve())
        self.assertIn("exec", command["args"])
        self.assertFalse((self.config.workdir / "attempt-count").exists())

    def test_failed_check_reports_exit_code_codex_errors_and_stderr(self):
        config = replace(self.config, tools=replace(self.config.tools, env=self.config.tools.env | {"LEAN1_TEST_HEALTH": "fail"}))
        checked = run_health_check(config, config.health)
        self.assertFalse(checked.ok)
        self.assertIn("exited with code 1", checked.detail)
        self.assertIn("Codex usage limit reached.", checked.detail)
        self.assertIn("health check failed", checked.detail)

    def test_unexpected_answer_timeout_and_missing_executable_fail(self):
        talker = self.root / "talker"
        talker.write_text(f"#!{sys.executable}\nimport json, sys\nsys.stdin.read()\nprint(json.dumps({{'type': 'item.completed', 'item': {{'type': 'agent_message', 'text': 'Hello there'}}}}))\n")
        talker.chmod(0o755)
        sleeper = self.root / "sleeper"
        sleeper.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(60)\n")
        sleeper.chmod(0o755)
        cases = {
            str(talker): "unexpectedly: Hello there",
            str(sleeper): "did not finish the health check within 0.5 seconds",
            str(self.root / "missing-codex"): "could not be started",
        }
        for executable, expected in cases.items():
            with self.subTest(executable=executable):
                config = replace(self.config, codex=executable)
                checked = run_health_check(config, HealthCheckConfig(timeout=0.5))
                self.assertFalse(checked.ok)
                self.assertIn(expected, checked.detail)

    def test_disabled_check_never_runs(self):
        with patch("lean1.health.run_health_check") as check:
            self.assertTrue(ensure_codex_healthy(replace(self.config, health=None), "run #1"))
        check.assert_not_called()

    def test_retry_recovers_from_one_transient_failure(self):
        config = replace(self.config, health=HealthCheckConfig(retries=2, retry_delay=0))
        with patch("lean1.health.run_health_check", side_effect=[result(False), result(True)]) as check, contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(ensure_codex_healthy(config, "run #1"))
        self.assertEqual(check.call_count, 2)
        saved = json.loads((config.workdir / ".lean1/health/last.json").read_text())
        self.assertTrue(saved["ok"])
        self.assertEqual(saved["purpose"], "run #1")

    def test_exhausted_retries_raise_an_issue_with_every_failure(self):
        config = replace(self.config, health=HealthCheckConfig(retries=1, retry_delay=0))
        with patch("lean1.health.run_health_check", side_effect=[result(False, "first"), result(False, "second")]), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SupervisorIssue) as raised:
                ensure_codex_healthy(config, "run #3")
        self.assertEqual(raised.exception.exit_code, EXIT_HEALTH_CHECK)
        self.assertEqual(raised.exception.kind, "health_check")
        self.assertIn("before run #3 failed 2 time(s)", str(raised.exception))
        self.assertIn("first", raised.exception.detail)
        self.assertIn("second", raised.exception.detail)

    def test_stop_request_during_retry_delay_returns_without_an_issue(self):
        config = replace(self.config, health=HealthCheckConfig(retries=1, retry_delay=60))
        with patch("lean1.health.run_health_check", return_value=result(False)) as check, contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(ensure_codex_healthy(config, "run #1", stop_requested=lambda: True))
        check.assert_called_once()


class SupervisorHealthTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="lean1-health-supervisor-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.enterContext(contextlib.chdir(self.root))
        self.config = RunConfig(
            self.root / "work", "Prove True.", LeanTools("lean", "lake", None, {}, "Lean 4"),
            EmailConfig("me@example.org", "mail"), restart_delay=0, notify="complete",
            health=HealthCheckConfig(retries=0, retry_delay=0),
        )
        self.output = {
            "run_number": 1, "exit_code": 0, "duration_seconds": 0, "task_complete": False,
            "verification": {}, "final_message": "Incomplete", "stderr": "",
        }

    def test_failed_check_pauses_before_research_and_always_emails(self):
        with patch("lean1.health.run_health_check", return_value=result(False, "Codex is logged out.")), patch("lean1.runner.run_codex") as codex, patch.object(EmailConfig, "send_named", return_value=None) as email, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(supervise(self.config), EXIT_HEALTH_CHECK)
        codex.assert_not_called()
        email.assert_called_once()
        subject, body = email.call_args.args[2], email.call_args.args[3]
        self.assertIn("lean1 stopped", subject)
        self.assertIn("health check", subject)
        self.assertIn("Codex is logged out.", body)
        self.assertIn(f"--workdir {self.config.workdir}", body)
        issue = json.loads((self.config.workdir / ".lean1/last_issue.json").read_text())
        self.assertEqual(issue["kind"], "health_check")
        self.assertFalse((self.config.workdir / ".lean1/active_run.json").exists())

    def test_check_precedes_every_attempt_and_a_late_failure_stops_retries(self):
        checks = [result(True), result(True), result(False, "quota exhausted")]
        outputs = [dict(self.output, run_number=number) for number in (1, 2)]
        with patch("lean1.health.run_health_check", side_effect=checks) as check, patch("lean1.runner.run_codex", side_effect=outputs) as codex, patch.object(EmailConfig, "send_named", return_value=None) as email, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(supervise(replace(self.config, max_runs=5)), EXIT_HEALTH_CHECK)
        self.assertEqual(check.call_count, 3)
        self.assertEqual(codex.call_count, 2)
        self.assertIn("before run #3", email.call_args.args[2])
        saved = json.loads((self.config.workdir / "codex_last_output.json").read_text())
        self.assertEqual(saved["issue_kind"], "health_check")

    def test_paused_workspace_resumes_after_codex_recovers(self):
        with patch("lean1.health.run_health_check", return_value=result(False)), patch.object(EmailConfig, "send_named", return_value=None), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(supervise(self.config), EXIT_HEALTH_CHECK)
        done = dict(self.output, task_complete=True, final_message="Solved.")
        with patch("lean1.health.run_health_check", return_value=result(True)), patch("lean1.runner.run_codex", return_value=done) as codex, patch.object(EmailConfig, "send", return_value=None), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(supervise(self.config), 0)
        codex.assert_called_once()


if __name__ == "__main__":
    unittest.main()
