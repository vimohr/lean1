"""Graceful stop discovery, lifecycle, and requests between attempts."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from .support import REPO  # noqa: F401

from lean1.common import RunnerError, write_json
from lean1.control import active_run, request_stop
from lean1.lean import LeanTools
from lean1.notify import EmailConfig
from lean1.runner import RunConfig, supervise


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="lean1-control-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.launch = self.root / "launch"
        self.launch.mkdir()
        self.enterContext(contextlib.chdir(self.launch))
        self.workdir = self.root / "workspace"

    def test_ambiguous_runs_require_an_explicit_workspace(self):
        with active_run(self.workdir) as first, active_run(self.root / "another") as second:
            with self.assertRaisesRegex(RunnerError, "Multiple active"):
                request_stop()
            self.assertEqual(request_stop(self.workdir), self.workdir)
            self.assertTrue(first.stop_requested())
            self.assertFalse(second.stop_requested())

    def test_nested_campaign_and_problem_runs_select_the_campaign(self):
        campaign = self.root / "campaign"
        with active_run(campaign) as outer, active_run(campaign / "problems" / "op_1") as inner:
            self.assertEqual(request_stop(), campaign)
            self.assertTrue(outer.stop_requested())
            self.assertFalse(inner.stop_requested())
            with contextlib.chdir(campaign / "problems" / "op_1"):
                self.assertEqual(request_stop(), campaign / "problems" / "op_1")
            self.assertTrue(inner.stop_requested())

    def test_workspace_subdirectory_finds_its_supervisor(self):
        with active_run(self.workdir) as run:
            nested = self.workdir / "proofs"
            nested.mkdir()
            with contextlib.chdir(nested):
                self.assertEqual(request_stop(), self.workdir)
            self.assertTrue(run.stop_requested())

    def test_duplicate_supervisor_is_rejected_and_exception_cleans_up(self):
        with self.assertRaisesRegex(RuntimeError, "test failure"):
            with active_run(self.workdir) as run:
                with self.assertRaisesRegex(RunnerError, "already running"):
                    with active_run(self.workdir):
                        self.fail("Duplicate supervisor started")
                request_stop(self.workdir)
                self.assertTrue(run.stop_requested())
                raise RuntimeError("test failure")
        self.assertFalse((self.workdir / ".lean1/active_run.json").exists())
        self.assertFalse((self.workdir / ".lean1/stop_requested").exists())
        self.assertEqual(list((self.launch / ".lean1/active-runs").glob("*.json")), [])

    @unittest.skipUnless(os.name == "posix", "Local PID checks use POSIX signals")
    def test_dead_supervisor_and_old_stop_request_do_not_prevent_resume(self):
        active_file = self.workdir / ".lean1/active_run.json"
        with active_run(self.workdir):
            previous = json.loads(active_file.read_text())
        write_json(active_file, previous)
        (self.workdir / ".lean1/stop_requested").write_text(previous["run_id"])
        with patch("lean1.control.os.kill", side_effect=ProcessLookupError):
            with self.assertRaisesRegex(RunnerError, "No active"):
                request_stop(self.workdir)
            with active_run(self.workdir) as resumed:
                self.assertNotEqual(resumed.run_id, previous["run_id"])
                self.assertFalse(resumed.stop_requested())

    def test_stop_during_restart_delay_or_notification_never_starts_another_attempt(self):
        for stage in ("delay", "notification"):
            with self.subTest(stage=stage):
                self.workdir = self.root / stage
                config = RunConfig(
                    self.workdir, "Prove True.", LeanTools("lean", "lake", None, {}, "Lean 4"),
                    EmailConfig("me@example.org", "mail"), restart_delay=60,
                )
                output = {
                    "run_number": 1, "exit_code": 0, "duration_seconds": 0,
                    "task_complete": False, "verification": {}, "final_message": "Incomplete", "stderr": "",
                }

                def stop(*args):
                    request_stop(self.workdir)

                with patch("lean1.runner.run_codex", return_value=output) as codex, patch.object(EmailConfig, "send", side_effect=stop if stage == "notification" else None, return_value=None), patch("lean1.runner.time.sleep", side_effect=stop) as sleep, contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(supervise(config), 130)
                codex.assert_called_once()
                if stage == "notification":
                    sleep.assert_not_called()
                else:
                    sleep.assert_called_once()
                saved = json.loads((self.workdir / "codex_last_output.json").read_text())
                self.assertTrue(saved["stop_requested"])


if __name__ == "__main__":
    unittest.main()
