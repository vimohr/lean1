"""Deliver email notifications through a local mail command or SMTP."""

import os
import re
import shutil
import smtplib
import ssl
import subprocess
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

from .common import RunnerError, atomic_write


def email_address(value: str) -> str:
    if value.startswith("-") or not re.fullmatch(r"[^\s@<>;,\x00-\x1f\x7f]+@[^\s@<>;,\x00-\x1f\x7f]+", value):
        raise RunnerError("Supply one plain email address, such as researcher@example.com.")
    return value


@dataclass(frozen=True)
class EmailConfig:
    recipient: str
    backend: str
    mail_command: str | None = None
    host: str = ""
    port: int = 587
    sender: str = ""
    username: str = ""
    password: str = field(default="", repr=False)
    security: str = "starttls"

    @classmethod
    def load(cls, recipient: str, backend: str = "auto", mail_command: str = "mail") -> "EmailConfig":
        recipient = email_address(recipient)
        host = os.environ.get("LEAN1_SMTP_HOST", "")
        if backend == "auto":
            backend = "smtp" if host else "mail"
        if backend == "mail":
            executable = shutil.which(mail_command)
            if not executable:
                raise RunnerError(
                    f"Email command {mail_command!r} was not found. Configure a local mail client/MTA, "
                    "or set LEAN1_SMTP_HOST and LEAN1_SMTP_FROM to use SMTP."
                )
            return cls(recipient, "mail", mail_command=str(Path(executable).absolute()))
        if not host:
            raise RunnerError("SMTP delivery requires LEAN1_SMTP_HOST.")
        username = os.environ.get("LEAN1_SMTP_USERNAME", "")
        sender = os.environ.get("LEAN1_SMTP_FROM", "") or username
        if not sender:
            raise RunnerError("SMTP delivery requires LEAN1_SMTP_FROM (or an email address as LEAN1_SMTP_USERNAME).")
        sender = email_address(sender)
        security = os.environ.get("LEAN1_SMTP_SECURITY", "starttls").lower()
        if security not in {"starttls", "ssl", "plain"}:
            raise RunnerError("LEAN1_SMTP_SECURITY must be starttls, ssl, or plain.")
        try:
            port = int(os.environ.get("LEAN1_SMTP_PORT", "465" if security == "ssl" else "587"))
        except ValueError as exc:
            raise RunnerError("LEAN1_SMTP_PORT must be a port number.") from exc
        if not 1 <= port <= 65535:
            raise RunnerError("LEAN1_SMTP_PORT must be between 1 and 65535.")
        return cls(
            recipient, "smtp", host=host, port=port, sender=sender, username=username,
            password=os.environ.get("LEAN1_SMTP_PASSWORD", ""), security=security,
        )

    def send(self, workdir: Path, run_number: int, subject: str, body: str) -> str | None:
        return self.send_named(workdir, f"run-{run_number:04d}", subject, body)

    def send_named(self, workdir: Path, name: str, subject: str, body: str) -> str | None:
        """Save the message as .lean1/notifications/<name>.eml, then submit it."""
        message = EmailMessage()
        message["To"] = self.recipient
        if self.sender:
            message["From"] = self.sender
        message["Subject"] = subject
        message["Date"] = formatdate(localtime=True)
        message["Message-ID"] = make_msgid()
        message.set_content(body)
        saved = workdir / ".lean1" / "notifications" / f"{name}.eml"
        atomic_write(saved, message.as_string())
        try:
            if self.backend == "mail":
                result = subprocess.run(
                    [self.mail_command or "mail", "-s", subject, self.recipient],
                    input=body, text=True, capture_output=True, timeout=30,
                )
                if result.returncode:
                    raise RunnerError(result.stderr.strip() or result.stdout.strip() or f"mail exited {result.returncode}")
            else:
                if self.security == "ssl":
                    client = smtplib.SMTP_SSL(self.host, self.port, timeout=30, context=ssl.create_default_context())
                else:
                    client = smtplib.SMTP(self.host, self.port, timeout=30)
                with client:
                    if self.security == "starttls":
                        client.starttls(context=ssl.create_default_context())
                    if self.username:
                        client.login(self.username, self.password)
                    refused = client.send_message(message)
                    if refused:
                        raise RunnerError(f"SMTP refused recipients: {refused}")
            print(f"Email submitted to {self.recipient}", flush=True)
            return None
        except (OSError, smtplib.SMTPException, subprocess.SubprocessError, RunnerError) as exc:
            error = f"Email delivery failed: {exc}. Saved email: {saved}"
            print(error, flush=True)
            return error
