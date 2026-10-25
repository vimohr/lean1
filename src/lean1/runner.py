"""Restart Codex attempts, stream logs, and verify Lean proofs."""

import json
import re
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from importlib.resources import files
from typing import Any

from .codex import codex_environment, execute_codex
from .common import RunnerError, atomic_write, log, read_tail, render_template, write_json
from .config import HealthCheckConfig, RunConfig
from .control import ActiveRun, active_run
from .health import ensure_codex_healthy
from .issues import (
    EXIT_CONFIGURATION, EXIT_EMAIL_FAILED, EXIT_LIMIT, EXIT_STOPPED,
    ReportedError, StopRequested, SupervisorIssue, issue_body, report_issue,
)
from .verification import Verification, verify

__all__ = [
    "COMPLETE_MARKER", "LEAN_MARKER", "FAILURE_LIMIT", "FAILURE_WINDOW_SECONDS",
    "HealthCheckConfig", "Outcome", "RunConfig", "is_complete", "load_prompt",
    "render_template", "run_codex", "supervise", "supervise_outcome",
]

COMPLETE_MARKER = "TASK_STATUS: COMPLETE"
LEAN_MARKER = "LEAN_VERIFIED: YES"
FAILURE_LIMIT = 10
FAILURE_WINDOW_SECONDS = 60


@dataclass(frozen=True)
class Outcome:
    """How a supervisor invocation ended; `code` is the process exit code."""

    code: int
    status: str


def is_complete(final_message: str | None) -> bool:
    lines = (final_message or "").strip().splitlines()
    return lines[-2:] == [COMPLETE_MARKER, LEAN_MARKER]


def load_prompt(config: RunConfig) -> str:
    template = (
        config.prompt_file.read_text(encoding="utf-8") if config.prompt_file
        else files("lean1").joinpath("prompt.txt").read_text(encoding="utf-8")
    )
    if template.count("{{PROBLEM}}") != 1:
        raise RunnerError("The prompt template must contain exactly one {{PROBLEM}} placeholder.")
    prompt = render_template(template, {
        "PROBLEM": config.problem,
        "LEAN_COMMAND": config.tools.lean,
        "LAKE_COMMAND": config.tools.lake,
        "ELAN_COMMAND": config.tools.elan or "elan (not installed; use the available lean and lake)",
    }).strip()
    feedback_file = config.workdir / ".lean1" / "verification_feedback.txt"
    if feedback_file.is_file():
        prompt += "\n\n## Feedback from the Python supervisor\n\n" + read_tail(feedback_file, 8000)
    prompt += (
        "\n\nThis may be a continuation. Inspect existing solution files, research notes, "
        "codex_last_message.md, and .lean1/ verification logs before repeating work. "
        "Preserve useful progress and continue until the original problem and Lean verification are complete.\n"
    )
    return prompt


def run_codex(config: RunConfig, run_number: int) -> dict[str, object]:
    print(f"\n=== Codex run #{run_number} ===", flush=True)
    prompt = load_prompt(config)
    state = config.workdir / ".lean1"
    prompt_path = state / "prompt.txt"
    atomic_write(prompt_path, prompt)
    result = execute_codex(
        config.agent_command(), cwd=config.workdir,
        env=codex_environment(config.workdir, config.tools.env), prompt_file=prompt_path,
        events_file=state / f"run-{run_number:04d}.events.jsonl",
        stderr_file=state / f"run-{run_number:04d}.stderr.log",
    )
    returncode, final_message = result.returncode, result.final_message
    claimed_complete = returncode == 0 and is_complete(final_message)
    verification = Verification(False, "Agent has not reported a complete Lean-verified result.", "")
    if claimed_complete:
        print("Agent reported completion; rechecking Lean proofs ...", flush=True)
        verification = verify(config.workdir, config.tools, run_number, config.verification_timeout)
    if not verification.success:
        detail = verification.detail
        if returncode:
            detail = f"Codex exited with code {returncode}.\n" + "\n".join(result.errors) + "\n" + read_tail(result.stderr_file)
        atomic_write(state / "verification_feedback.txt", detail + "\nContinue working; completion has not been accepted.\n")
    else:
        (state / "verification_feedback.txt").unlink(missing_ok=True)
    output: dict[str, object] = {
        "run_number": run_number,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "duration_seconds": result.duration_seconds,
        "exit_code": returncode,
        "success": returncode == 0,
        "claimed_complete": claimed_complete,
        "task_complete": claimed_complete and verification.success,
        "verification": {"success": verification.success, "detail": verification.detail, "log_file": verification.log_file},
        "final_message": final_message,
        "stderr": read_tail(result.stderr_file).strip(),
        "codex_errors": list(result.errors),
        "event_count": result.event_count,
        "events_file": str(result.events_file),
        "stderr_file": str(result.stderr_file),
    }
    atomic_write(config.workdir / "codex_last_message.md", final_message or "(Codex did not produce an agent_message.)\n")
    write_json(config.workdir / "codex_last_output.json", output)
    return output


def _stopped(config: RunConfig) -> Outcome:
    output_path = config.workdir / "codex_last_output.json"
    if output_path.is_file():
        output = json.loads(output_path.read_text(encoding="utf-8"))
        output["stop_requested"] = True
        write_json(output_path, output)
    log(config.workdir, "supervisor stopped after a graceful stop request")
    print("Stopped after the current attempt; workspace and progress have been saved.", flush=True)
    return Outcome(EXIT_STOPPED, "stopped")


def _record_issue(config: RunConfig, issue: SupervisorIssue) -> None:
    record = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "kind": issue.kind, "reason": str(issue), "detail": issue.detail,
    }
    write_json(config.workdir / ".lean1" / "last_issue.json", record)
    output_path = config.workdir / "codex_last_output.json"
    if output_path.is_file():
        output = json.loads(output_path.read_text(encoding="utf-8"))
        output["stop_reason"] = str(issue)
        output["issue_kind"] = issue.kind
        write_json(output_path, output)


def _resume_hint(config: RunConfig) -> str:
    return (
        "The workspace and all progress have been saved. Fix the cause, then rerun the "
        f"same lean1 command with --workdir {config.workdir} to resume."
    )


def _apply_acceptance(config: RunConfig, output: dict[str, Any]) -> bool:
    """Run the optional acceptance stage; a rejection becomes feedback for the next attempt."""
    decision = config.acceptance(config, output)
    output["acceptance"] = {"accepted": decision.accepted, "summary": decision.summary, "report": decision.report}
    if decision.accepted:
        return True
    output["task_complete"] = False
    atomic_write(
        config.workdir / ".lean1" / "verification_feedback.txt",
        decision.feedback + "\nContinue working; completion has not been accepted.\n",
    )
    write_json(config.workdir / "codex_last_output.json", output)
    return False


def _supervise(config: RunConfig, control: ActiveRun) -> Outcome:
    state = config.workdir / ".lean1"
    state.mkdir(parents=True, exist_ok=True)
    # Validate the installed/custom resource before starting a paid agent run.
    load_prompt(config)
    print(f"Workspace: {config.workdir}", flush=True)
    print(f"Codex home: {config.workdir / '.codex'}", flush=True)
    print(f"Notifications: {config.email.recipient} ({config.email.backend}; {config.notify})", flush=True)

    def stop_requested() -> bool:
        return control.stop_requested() or bool(config.external_stop and config.external_stop())

    run_number = 0
    failed_attempts: deque[float] = deque()
    # Continue log numbering when the same workspace is used again.
    prior = state.glob("run-*.events.jsonl")
    numbers = [int(path.name.split(".")[0].split("-")[1]) for path in prior if re.fullmatch(r"run-\d+\.events\.jsonl", path.name)]
    next_number = max(numbers, default=0)
    try:
        while config.max_runs == 0 or run_number < config.max_runs:
            if stop_requested():
                return _stopped(config)
            if not ensure_codex_healthy(config, f"run #{next_number + run_number + 1}", stop_requested):
                return _stopped(config)
            run_number += 1
            attempt_started = time.monotonic()
            output = run_codex(config, next_number + run_number)
            if output["task_complete"] and config.acceptance:
                _apply_acceptance(config, output)
            complete = output["task_complete"]
            if output["exit_code"] or not (output["final_message"] or "").strip():
                failed_attempts.append(attempt_started)
            now = time.monotonic()
            while failed_attempts and now - failed_attempts[0] > FAILURE_WINDOW_SECONDS:
                failed_attempts.popleft()
            failsafe_reason = None
            if len(failed_attempts) >= FAILURE_LIMIT:
                failsafe_reason = f"Codex failed to run {FAILURE_LIMIT} times within {FAILURE_WINDOW_SECONDS} seconds; stopping retries."
                output["stop_reason"] = failsafe_reason
                log(config.workdir, f"supervisor failsafe: {failsafe_reason}")
            last_attempt = bool(config.max_runs and run_number == config.max_runs)
            stop_requested_now = stop_requested()
            reason = f"exit={output['exit_code']}, duration={output['duration_seconds']}s, complete={complete}"
            log(config.workdir, f"run #{output['run_number']}: {reason}")
            print(f"Finished run #{output['run_number']}: {reason}", flush=True)
            notification_error = None
            # A failsafe stop is an issue and is always reported.
            if config.notify == "all" or complete or failsafe_reason:
                status = "completed task" if complete else "stopped incomplete" if last_attempt or stop_requested_now or failsafe_reason else "restarting"
                subject = config.subject(f"Codex {status} after run #{output['run_number']}")
                body = (
                    f"Codex run #{output['run_number']} finished.\n\n"
                    f"Workspace: {config.workdir}\nExit code: {output['exit_code']}\n"
                    f"Duration: {output['duration_seconds']} seconds\nTask complete: {complete}\n"
                    f"Lean verification: {output['verification']}\n"
                )
                if "acceptance" in output:
                    body += f"Independent review: {output['acceptance']}\n"
                body += (
                    f"\nLast Codex message:\n\n{output['final_message'] or '(No final message)'}\n\n"
                    f"stderr:\n\n{output['stderr'] or '(empty)'}\n"
                )
                if failsafe_reason:
                    body += f"\nStop reason: {failsafe_reason}\n\n{_resume_hint(config)}\n"
                notification_error = config.email.send(config.workdir, int(output["run_number"]), subject, body)
                if notification_error:
                    log(config.workdir, notification_error)
            output["notification_error"] = notification_error
            # A request can also arrive while a notification is being delivered.
            output["stop_requested"] = stop_requested_now = stop_requested()
            write_json(config.workdir / "codex_last_output.json", output)
            if complete:
                print(f"Task complete and Lean verified. Result: {config.workdir / 'codex_last_message.md'}", flush=True)
                return Outcome(EXIT_EMAIL_FAILED, "complete") if notification_error else Outcome(0, "complete")
            if stop_requested_now:
                return _stopped(config)
            if failsafe_reason:
                print(f"Failsafe: {failsafe_reason} Details: {config.workdir / 'codex_last_output.json'}", flush=True)
                return Outcome(EXIT_LIMIT, "failsafe")
            if last_attempt:
                print(f"Reached --max-runs without a verified solution. Details: {config.workdir / 'codex_last_output.json'}", flush=True)
                return Outcome(EXIT_LIMIT, "limit")
            print(f"Result incomplete or verification failed. Restarting in {config.restart_delay:g}s ...", flush=True)
            deadline = time.monotonic() + config.restart_delay
            while not stop_requested():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(0.1, remaining))
    except StopRequested:
        return _stopped(config)
    except KeyboardInterrupt:
        log(config.workdir, "supervisor stopped by user")
        print("\nStopped; workspace and progress have been saved.", flush=True)
        return Outcome(EXIT_STOPPED, "interrupted")
    except SupervisorIssue as issue:
        _record_issue(config, issue)
        report_issue(config.email, config.workdir, str(issue), issue_body(issue, config.workdir, _resume_hint(config)), label=config.label)
        print(f"lean1 paused: {issue}. An email has been sent; details: {state / 'last_issue.json'}", flush=True)
        return Outcome(issue.exit_code, issue.kind)
    except (OSError, RunnerError) as exc:
        log(config.workdir, f"supervisor error: {exc}")
        issue = SupervisorIssue(f"supervisor error: {exc}", kind="error", exit_code=EXIT_CONFIGURATION)
        report_issue(config.email, config.workdir, str(issue), issue_body(issue, config.workdir, _resume_hint(config)), label=config.label)
        raise ReportedError(str(exc)) from exc
    return Outcome(EXIT_LIMIT, "limit")


def supervise_outcome(config: RunConfig) -> Outcome:
    config.workdir.mkdir(parents=True, exist_ok=True)
    with active_run(config.workdir) as control:
        return _supervise(config, control)


def supervise(config: RunConfig) -> int:
    return supervise_outcome(config).code
