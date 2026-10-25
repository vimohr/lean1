"""Issue emails are sent regardless of the notification level."""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from .support import REPO  # noqa: F401

from lean1.issues import EXIT_HEALTH_CHECK, SupervisorIssue, issue_body, report_issue
from lean1.notify import EmailConfig


class IssueTests(unittest.TestCase):
    def test_issue_email_is_saved_logged_and_labelled(self):
        with tempfile.TemporaryDirectory() as temporary:
            workdir = Path(temporary) / "work"
            email = EmailConfig("me@example.org", "mail", mail_command="/nonexistent/mail")
            with contextlib.redirect_stdout(io.StringIO()):
                error = report_issue(email, workdir, "health check failed", "Body.", label="op_0000000000000001")
            self.assertIn("Email delivery failed", error)
            saved = list((workdir / ".lean1/notifications").glob("issue-*.eml"))
            self.assertEqual(len(saved), 1)
            self.assertIn("Subject: lean1 stopped: [op_0000000000000001] health check failed", saved[0].read_text())
            log = (workdir / "codex_runs.log").read_text()
            self.assertIn("issue: health check failed", log)
            self.assertIn("Email delivery failed", log)

    def test_successive_issue_emails_do_not_overwrite_each_other(self):
        with tempfile.TemporaryDirectory() as temporary, patch("lean1.notify.subprocess.run") as run, contextlib.redirect_stdout(io.StringIO()):
            run.return_value.returncode = 0
            email = EmailConfig("me@example.org", "mail", mail_command="mail")
            for _ in range(3):
                self.assertIsNone(report_issue(email, Path(temporary), "stopped", "Body."))
            self.assertEqual(len(list(Path(temporary).glob(".lean1/notifications/issue-*.eml"))), 3)

    def test_body_contains_reason_detail_and_resume_hint(self):
        issue = SupervisorIssue("Codex failed", kind="health_check", exit_code=EXIT_HEALTH_CHECK, detail="quota\n")
        body = issue_body(issue, Path("/work"), "Rerun to resume.")
        for text in ("Reason: Codex failed", "Kind: health_check", "Workspace: /work", "quota", "Rerun to resume."):
            self.assertIn(text, body)


if __name__ == "__main__":
    unittest.main()
