"""Restart Codex attempts, stream logs, and verify Lean proofs."""

import json
import os
import re
import signal
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from importlib.resources import files
from pathlib import Path

from .common import RunnerError, atomic_write, log, read_tail, write_json
from .lean import LeanTools
from .notify import EmailConfig
from .verification import Verification, verify

COMPLETE_MARKER = "TASK_STATUS: COMPLETE"
LEAN_MARKER = "LEAN_VERIFIED: YES"


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
    replacements = {
        "PROBLEM": config.problem,
        "LEAN_COMMAND": config.tools.lean,
        "LAKE_COMMAND": config.tools.lake,
        "ELAN_COMMAND": config.tools.elan or "elan (not installed; use the available lean and lake)",
    }
    # One pass preserves literal placeholders and JSON braces in supplied problems.
    prompt = re.sub(
        r"\{\{(PROBLEM|LEAN_COMMAND|LAKE_COMMAND|ELAN_COMMAND)\}\}",
        lambda match: replacements[match.group(1)], template,
    ).strip()
    feedback_file = config.workdir / ".lean1" / "verification_feedback.txt"
    if feedback_file.is_file():
        prompt += "\n\n## Feedback from the Python supervisor\n\n" + read_tail(feedback_file, 8000)
    prompt += (
        "\n\nThis may be a continuation. Inspect existing solution files, research notes, "
        "codex_last_message.md, and .lean1/ verification logs before repeating work. "
        "Preserve useful progress and continue until the original problem and Lean verification are complete.\n"
    )
    return prompt


def codex_command(config: RunConfig) -> list[str]:
    command = [config.codex]
    if config.network:
        command.append("--search")
    command.extend([
        "exec", "--sandbox", "workspace-write", "--json", "--skip-git-repo-check",
        "-c", 'approval_policy="never"',
        "-c", f"sandbox_workspace_write.network_access={'true' if config.network else 'false'}",
    ])
    if config.tools.writable_home and not config.tools.writable_home.is_relative_to(config.workdir):
        command.extend(["--add-dir", str(config.tools.writable_home)])
    if config.model:
        command.extend(["--model", config.model])
    command.append("-")
    return command


def stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "posix":
        os.killpg(process.pid, signal.SIGTERM)
    else:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        process.wait()


def run_codex(config: RunConfig, run_number: int) -> dict[str, object]:
    print(f"\n=== Codex run #{run_number} ===", flush=True)
    prompt = load_prompt(config)
    prompt_path = config.workdir / ".lean1" / "prompt.txt"
    atomic_write(prompt_path, prompt)
    state = config.workdir / ".lean1"
    events_file = state / f"run-{run_number:04d}.events.jsonl"
    stderr_file = state / f"run-{run_number:04d}.stderr.log"
    started = time.monotonic()
    final_message: str | None = None
    event_count = 0
    # SMTP credentials belong to the notifier, not to agent or Lean processes.
    env = {key: value for key, value in config.tools.env.items() if not key.startswith("LEAN1_SMTP_")}
    with prompt_path.open("r", encoding="utf-8") as prompt_input, events_file.open("w", encoding="utf-8") as events, stderr_file.open("w", encoding="utf-8") as errors:
        process = subprocess.Popen(
            codex_command(config), cwd=config.workdir, env=env, stdin=prompt_input,
            stdout=subprocess.PIPE, stderr=errors, text=True, encoding="utf-8",
            errors="replace", bufsize=1, start_new_session=os.name == "posix",
        )
        try:
            assert process.stdout is not None
            for line in process.stdout:
                events.write(line)
                events.flush()
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                event_count += 1
                item = event.get("item")
                if event.get("type") == "item.completed" and isinstance(item, dict) and item.get("type") == "agent_message":
                    if isinstance(item.get("text"), str):
                        final_message = item["text"]
                        print(final_message, flush=True)
            returncode = process.wait()
        except BaseException:
            stop_process(process)
            raise
        finally:
            if process.stdout:
                process.stdout.close()
    claimed_complete = returncode == 0 and is_complete(final_message)
    verification = Verification(False, "Agent has not reported a complete Lean-verified result.", "")
    if claimed_complete:
        print("Agent reported completion; rechecking Lean proofs ...", flush=True)
        verification = verify(config.workdir, config.tools, run_number, config.verification_timeout)
    if not verification.success:
        detail = verification.detail
        if returncode:
            detail = f"Codex exited with code {returncode}.\n" + read_tail(stderr_file)
        atomic_write(state / "verification_feedback.txt", detail + "\nContinue working; completion has not been accepted.\n")
    else:
        (state / "verification_feedback.txt").unlink(missing_ok=True)
    output: dict[str, object] = {
        "run_number": run_number,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "duration_seconds": round(time.monotonic() - started, 2),
        "exit_code": returncode,
        "success": returncode == 0,
        "claimed_complete": claimed_complete,
        "task_complete": claimed_complete and verification.success,
        "verification": {"success": verification.success, "detail": verification.detail, "log_file": verification.log_file},
        "final_message": final_message,
        "stderr": read_tail(stderr_file).strip(),
        "event_count": event_count,
        "events_file": str(events_file),
        "stderr_file": str(stderr_file),
    }
    atomic_write(config.workdir / "codex_last_message.md", final_message or "(Codex did not produce an agent_message.)\n")
    write_json(config.workdir / "codex_last_output.json", output)
    return output


def supervise(config: RunConfig) -> int:
    config.workdir.mkdir(parents=True, exist_ok=True)
    state = config.workdir / ".lean1"
    state.mkdir(exist_ok=True)
    # Validate the installed/custom resource before starting a paid agent run.
    load_prompt(config)
    print(f"Workspace: {config.workdir}", flush=True)
    print(f"Notifications: {config.email.recipient} ({config.email.backend}; {config.notify})", flush=True)
    run_number = 0
    # Continue log numbering when the same workspace is used again.
    prior = state.glob("run-*.events.jsonl")
    numbers = [int(path.name.split(".")[0].split("-")[1]) for path in prior if re.fullmatch(r"run-\d+\.events\.jsonl", path.name)]
    next_number = max(numbers, default=0)
    try:
        while config.max_runs == 0 or run_number < config.max_runs:
            run_number += 1
            output = run_codex(config, next_number + run_number)
            complete = output["task_complete"]
            last_attempt = bool(config.max_runs and run_number == config.max_runs)
            reason = f"exit={output['exit_code']}, duration={output['duration_seconds']}s, complete={complete}"
            log(config.workdir, f"run #{output['run_number']}: {reason}")
            print(f"Finished run #{output['run_number']}: {reason}", flush=True)
            notification_error = None
            if config.notify == "all" or complete:
                status = "completed task" if complete else "stopped incomplete" if last_attempt else "restarting"
                subject = f"Codex {status} after run #{output['run_number']}"
                body = (
                    f"Codex run #{output['run_number']} finished.\n\n"
                    f"Workspace: {config.workdir}\nExit code: {output['exit_code']}\n"
                    f"Duration: {output['duration_seconds']} seconds\nTask complete: {complete}\n"
                    f"Lean verification: {output['verification']}\n\n"
                    f"Last Codex message:\n\n{output['final_message'] or '(No final message)'}\n\n"
                    f"stderr:\n\n{output['stderr'] or '(empty)'}\n"
                )
                notification_error = config.email.send(config.workdir, int(output["run_number"]), subject, body)
                if notification_error:
                    log(config.workdir, notification_error)
            output["notification_error"] = notification_error
            write_json(config.workdir / "codex_last_output.json", output)
            if complete:
                print(f"Task complete and Lean verified. Result: {config.workdir / 'codex_last_message.md'}", flush=True)
                return 3 if notification_error else 0
            if last_attempt:
                print(f"Reached --max-runs without a verified solution. Details: {config.workdir / 'codex_last_output.json'}", flush=True)
                return 1
            print(f"Result incomplete or verification failed. Restarting in {config.restart_delay:g}s ...", flush=True)
            time.sleep(config.restart_delay)
    except KeyboardInterrupt:
        log(config.workdir, "supervisor stopped by user")
        print("\nStopped; workspace and progress have been saved.", flush=True)
        return 130
    except (OSError, RunnerError) as exc:
        log(config.workdir, f"supervisor error: {exc}")
        raise
    return 1
