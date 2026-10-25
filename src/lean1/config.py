"""Shared configuration for supervised Codex runs."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .codex import codex_command
from .lean import LeanTools
from .notify import EmailConfig


@dataclass(frozen=True)
class HealthCheckConfig:
    """A short Codex execution that must succeed before every real one."""

    timeout: float = 300
    retries: int = 1
    retry_delay: float = 30


@dataclass(frozen=True)
class Acceptance:
    """Decision of an acceptance stage that runs after Lean verification succeeds."""

    accepted: bool
    summary: str
    feedback: str = ""
    report: str = ""


@dataclass(frozen=True)
class RunConfig:
    workdir: Path
    problem: str
    tools: LeanTools
    email: EmailConfig
    codex: str = "codex"
    model: str | None = None
    prompt_file: Path | None = None
    restart_delay: float = 3
    max_runs: int = 0
    notify: str = "all"
    network: bool = True
    verification_timeout: float = 1800
    health: HealthCheckConfig | None = None
    label: str = ""
    # Called after Lean verification succeeds; may reject the solution.
    acceptance: Callable[["RunConfig", dict[str, Any]], Acceptance] | None = field(default=None, compare=False)
    # An outer supervisor (a problem campaign) may request a stop.
    external_stop: Callable[[], bool] | None = field(default=None, compare=False)

    def agent_command(self) -> list[str]:
        return codex_command(
            self.codex, workdir=self.workdir, network=self.network,
            model=self.model, writable_home=self.tools.writable_home,
        )

    def subject(self, text: str) -> str:
        return f"[{self.label}] {text}" if self.label else text
