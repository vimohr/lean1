"""Check that Codex starts and answers before each real execution."""

import time
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Callable

from .codex import codex_environment, execute_codex
from .common import atomic_write, log, read_tail, write_json
from .config import HealthCheckConfig, RunConfig
from .issues import EXIT_HEALTH_CHECK, SupervisorIssue

HEALTH_TOKEN = "LEAN1_HEALTH_CHECK_OK"
HEALTH_PROMPT = (
    "This is an automated health check of the Codex CLI.\n"
    "Do not run commands, browse, read files, or edit files.\n"
    f"Reply with exactly this line and nothing else:\n{HEALTH_TOKEN}\n"
)


@dataclass(frozen=True)
class HealthResult:
    ok: bool
    detail: str
    duration_seconds: float
    returncode: int | None
    timestamp: str


def run_health_check(config: RunConfig, settings: HealthCheckConfig) -> HealthResult:
    """Run one check with the same executable, model, sandbox, and Codex home as real runs."""
    directory = config.workdir / ".lean1" / "health"
    directory.mkdir(parents=True, exist_ok=True)
    prompt_file = directory / "prompt.txt"
    atomic_write(prompt_file, HEALTH_PROMPT)
    stderr_file = directory / "check.stderr.log"
    timestamp = datetime.now().isoformat(timespec="seconds")
    try:
        result = execute_codex(
            config.agent_command(), cwd=directory, env=codex_environment(config.workdir, config.tools.env),
            prompt_file=prompt_file, events_file=directory / "check.events.jsonl",
            stderr_file=stderr_file, timeout=settings.timeout, echo=False,
        )
    except OSError as exc:
        return HealthResult(False, f"Codex could not be started: {exc}", 0.0, None, timestamp)
    message = (result.final_message or "").strip()
    if result.timed_out:
        detail = f"Codex did not finish the health check within {settings.timeout:g} seconds."
    elif result.returncode:
        detail = f"Codex exited with code {result.returncode} during the health check."
    elif HEALTH_TOKEN not in message:
        shown = message[:500] or "(no final response)"
        detail = f"Codex answered the health check unexpectedly: {shown}"
    else:
        return HealthResult(True, "Codex answered the health check.", result.duration_seconds, 0, timestamp)
    if result.errors:
        detail += "\nCodex errors:\n" + "\n".join(f"- {error[:1000]}" for error in result.errors)
    stderr = read_tail(stderr_file, 4000).strip()
    if stderr:
        detail += f"\nstderr:\n{stderr}"
    return HealthResult(False, detail, result.duration_seconds, result.returncode, timestamp)


def ensure_codex_healthy(
    config: RunConfig, purpose: str, stop_requested: Callable[[], bool] = lambda: False,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Return True when Codex works, False if a stop arrives while waiting to retry.

    Raises SupervisorIssue once every attempt fails, so the caller pauses the loop.
    """
    settings = config.health
    if settings is None:
        return True
    attempts = settings.retries + 1
    failures: list[str] = []
    for attempt in range(1, attempts + 1):
        print(f"Codex health check before {purpose} ({attempt}/{attempts}) ...", flush=True)
        result = run_health_check(config, settings)
        write_json(config.workdir / ".lean1" / "health" / "last.json", asdict(result) | {"purpose": purpose})
        log(config.workdir, f"health check before {purpose}: {'ok' if result.ok else 'failed'} ({result.duration_seconds}s)")
        if result.ok:
            return True
        print(f"Health check failed: {result.detail.splitlines()[0]}", flush=True)
        failures.append(f"Attempt {attempt} at {result.timestamp}: {result.detail}")
        if attempt < attempts:
            deadline = time.monotonic() + settings.retry_delay
            while time.monotonic() < deadline:
                if stop_requested():
                    return False
                sleep(min(0.5, max(0.0, deadline - time.monotonic())))
    raise SupervisorIssue(
        f"the Codex health check before {purpose} failed {attempts} time(s)",
        kind="health_check", exit_code=EXIT_HEALTH_CHECK,
        detail="\n\n".join(failures) + f"\n\nLogs: {config.workdir / '.lean1' / 'health'}",
    )
