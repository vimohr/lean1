"""Email address validation and SMTP delivery."""

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from .support import REPO  # noqa: F401

from lean1.common import RunnerError
from lean1.notify import EmailConfig, email_address


class EmailTests(unittest.TestCase):
    def test_email_rejects_option_and_header_injection(self):
        for value in ("-option@example.org", "a@example.org\nBcc: b@example.org", "a@example.org,b@example.org", "a\x00@example.org", "not-an-email"):
            with self.subTest(value=value), self.assertRaises(RunnerError):
                email_address(value)

    def test_smtp_starttls_authentication_submission_and_saved_email(self):
        env = {
            "LEAN1_SMTP_HOST": "smtp.example.org", "LEAN1_SMTP_FROM": "sender@example.org",
            "LEAN1_SMTP_USERNAME": "sender@example.org", "LEAN1_SMTP_PASSWORD": "secret",
        }
        with patch.dict(os.environ, env, clear=True), tempfile.TemporaryDirectory() as temporary, patch("lean1.notify.smtplib.SMTP") as smtp, contextlib.redirect_stdout(io.StringIO()):
            config = EmailConfig.load("recipient@example.org")
            client = smtp.return_value
            client.send_message.return_value = {}
            self.assertIsNone(config.send(Path(temporary), 1, "Complete", "The proof is verified."))
            client.starttls.assert_called_once()
            client.login.assert_called_once_with("sender@example.org", "secret")
            submitted = client.send_message.call_args.args[0]
            self.assertEqual(submitted["To"], "recipient@example.org")
            self.assertEqual(submitted["From"], "sender@example.org")
            self.assertNotIn("secret", (Path(temporary) / ".lean1/notifications/run-0001.eml").read_text())
            self.assertNotIn("secret", repr(config))


if __name__ == "__main__":
    unittest.main()
