"""Stops caused by problems that need attention, and their mandatory emails."""

from datetime import datetime
from pathlib import Path

from .common import RunnerError, log
from .notify import EmailConfig

EXIT_LIMIT = 1
EXIT_CONFIGURATION = 2
EXIT_EMAIL_FAILED = 3
EXIT_HEALTH_CHECK = 4
EXIT_PROBLEM_SOURCE = 5
EXIT_AGENT_STAGE = 6
EXIT_STOPPED = 130


class SupervisorIssue(RunnerError):
    """A condition that stops the loop and must be reported by email."""

    def __init__(self, message: str, *, kind: str, exit_code: int, detail: str = "") -> None:
        super().__init__(message)
        self.kind = kind
        self.exit_code = exit_code
        self.detail = detail


class StopRequested(Exception):
    """A graceful stop request arrived while a stage was waiting to start."""


class ReportedError(RunnerError):
    """An error whose issue email has already been sent."""


def report_issue(
    email: EmailConfig, workdir: Path, subject: str, body: str, *, label: str = "",
) -> str | None:
    """Email an issue regardless of the notification level; return any delivery error."""
    prefix = f"[{label}] " if label else ""
    name = f"issue-{datetime.now():%Y%m%d-%H%M%S-%f}"
    workdir.mkdir(parents=True, exist_ok=True)
    log(workdir, f"issue: {subject}")
    error = email.send_named(workdir, name, f"lean1 stopped: {prefix}{subject}", body)
    if error:
        log(workdir, error)
    return error


def issue_body(issue: SupervisorIssue, workdir: Path, resume_hint: str) -> str:
    body = f"lean1 stopped because of an issue.\n\nReason: {issue}\nKind: {issue.kind}\nWorkspace: {workdir}\n"
    if issue.detail:
        body += f"\nDetails:\n\n{issue.detail.strip()}\n"
    return body + f"\n{resume_hint}\n"
