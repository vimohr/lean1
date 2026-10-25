"""Launch Codex executions and collect their JSON event streams."""

import json
import os
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CodexResult:
    returncode: int
    final_message: str | None
    event_count: int
    duration_seconds: float
    events_file: Path
    stderr_file: Path
    timed_out: bool = False
    errors: tuple[str, ...] = ()


def codex_command(
    executable: str, *, workdir: Path, network: bool = True,
    model: str | None = None, writable_home: Path | None = None,
) -> list[str]:
    command = [executable]
    if network:
        command.append("--search")
    command.extend([
        "exec", "--sandbox", "workspace-write", "--json", "--skip-git-repo-check",
        "-c", 'approval_policy="never"',
        "-c", f"sandbox_workspace_write.network_access={'true' if network else 'false'}",
    ])
    if writable_home and not writable_home.is_relative_to(workdir):
        command.extend(["--add-dir", str(writable_home)])
    if model:
        command.extend(["--model", model])
    command.append("-")
    return command


def stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        process.wait()


def codex_environment(workdir: Path, base_env: dict[str, str]) -> dict[str, str]:
    """Keep runtime state local while seeding existing credentials and settings."""
    home = (workdir / ".codex").resolve()
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    source = Path(base_env.get("CODEX_HOME") or str(Path.home() / ".codex")).expanduser().resolve()
    if source != home:
        for original in (source / "auth.json", source / "config.toml", *source.glob("*.config.toml")):
            destination = home / original.name
            if not original.is_file() or destination.exists():
                continue
            # Exclusive creation preserves workspace-specific login/configuration.
            try:
                descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                continue
            try:
                with os.fdopen(descriptor, "wb") as target, original.open("rb") as existing:
                    shutil.copyfileobj(existing, target)
            except BaseException:
                destination.unlink(missing_ok=True)
                raise
    env = {key: value for key, value in base_env.items() if not key.startswith("LEAN1_SMTP_")}
    return env | {"CODEX_HOME": str(home)}


def final_agent_message(event: object) -> str | None:
    if not isinstance(event, dict) or event.get("type") != "item.completed":
        return None
    item = event.get("item")
    if isinstance(item, dict) and item.get("type") == "agent_message" and isinstance(item.get("text"), str):
        return item["text"]
    return None


def error_message(event: dict[str, object]) -> str | None:
    """Extract error text from `error`, `turn.failed`, and error item events."""
    kind = event.get("type")
    if kind == "error" and isinstance(event.get("message"), str):
        return event["message"]
    error = event.get("error")
    if kind == "turn.failed" and isinstance(error, dict) and isinstance(error.get("message"), str):
        return error["message"]
    item = event.get("item")
    if kind == "item.completed" and isinstance(item, dict) and item.get("type") == "error" and isinstance(item.get("message"), str):
        return item["message"]
    return None


def execute_codex(
    command: list[str], *, cwd: Path, env: dict[str, str], prompt_file: Path,
    events_file: Path, stderr_file: Path, timeout: float | None = None, echo: bool = True,
) -> CodexResult:
    """Run one Codex execution; a timeout stops the whole process group."""
    events_file.parent.mkdir(parents=True, exist_ok=True)
    stderr_file.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    final_message: str | None = None
    event_count = 0
    error_messages: list[str] = []
    timed_out = threading.Event()
    with prompt_file.open("r", encoding="utf-8") as prompt_input, events_file.open("w", encoding="utf-8") as events, stderr_file.open("w", encoding="utf-8") as errors:
        process = subprocess.Popen(
            command, cwd=cwd, env=env, stdin=prompt_input,
            stdout=subprocess.PIPE, stderr=errors, text=True, encoding="utf-8",
            errors="replace", bufsize=1, start_new_session=os.name == "posix",
        )

        def expire() -> None:
            timed_out.set()
            stop_process(process)

        timer = threading.Timer(timeout, expire) if timeout else None
        if timer:
            timer.daemon = True
            timer.start()
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
                error = error_message(event)
                if error and error not in error_messages:
                    error_messages.append(error)
                message = final_agent_message(event)
                if message is not None:
                    final_message = message
                    if echo:
                        print(final_message, flush=True)
            returncode = process.wait()
        except BaseException:
            stop_process(process)
            raise
        finally:
            if timer:
                timer.cancel()
            if process.stdout:
                process.stdout.close()
    return CodexResult(
        returncode=returncode, final_message=final_message, event_count=event_count,
        duration_seconds=round(time.monotonic() - started, 2), events_file=events_file,
        stderr_file=stderr_file, timed_out=timed_out.is_set(), errors=tuple(error_messages[-10:]),
    )
