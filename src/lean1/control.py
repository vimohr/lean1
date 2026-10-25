"""Workspace-local registration and graceful stop requests for supervisors."""

import json
import os
import socket
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from .common import RunnerError, atomic_write, write_json


@dataclass(frozen=True)
class ActiveRun:
    workdir: Path
    run_id: str
    pid: int
    hostname: str

    @property
    def state(self) -> Path:
        return self.workdir / ".lean1"

    def stop_requested(self) -> bool:
        try:
            return (self.state / "stop_requested").read_text(encoding="utf-8").strip() == self.run_id
        except FileNotFoundError:
            return False

    def is_running(self) -> bool:
        # A shared filesystem may expose runs on other hosts. Never signal them.
        if self.hostname != socket.gethostname() or os.name != "posix":
            return True
        try:
            os.kill(self.pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            pass
        return True


def _read_run(path: Path) -> ActiveRun | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(data, dict) or not isinstance(data.get("workdir"), str)
            or not isinstance(data.get("run_id"), str) or not data["run_id"]
            or not isinstance(data.get("pid"), int) or data["pid"] <= 0
            or not isinstance(data.get("hostname"), str)
        ):
            return None
        return ActiveRun(Path(data["workdir"]), data["run_id"], data["pid"], data["hostname"])
    except (FileNotFoundError, json.JSONDecodeError):
        return None


@contextmanager
def active_run(workdir: Path) -> Iterator[ActiveRun]:
    workdir = workdir.resolve()
    state = workdir / ".lean1"
    state.mkdir(parents=True, exist_ok=True)
    active_file = state / "active_run.json"
    previous = _read_run(active_file)
    if previous and previous.is_running():
        raise RunnerError(f"A lean1 supervisor is already running in {workdir}.")
    run = ActiveRun(workdir, uuid4().hex, os.getpid(), socket.gethostname())
    record = {"workdir": str(workdir), "run_id": run.run_id, "pid": run.pid, "hostname": run.hostname}
    registry_file = Path.cwd() / ".lean1" / "active-runs" / f"{run.run_id}.json"
    (state / "stop_requested").unlink(missing_ok=True)
    write_json(active_file, record)
    try:
        write_json(registry_file, record)
        yield run
    finally:
        registry_file.unlink(missing_ok=True)
        if _read_run(active_file) == run:
            active_file.unlink(missing_ok=True)
        if run.stop_requested():
            (state / "stop_requested").unlink(missing_ok=True)


def request_stop(workdir: Path | None = None) -> Path:
    if workdir is not None:
        target = workdir.expanduser().resolve()
        run = _read_run(target / ".lean1" / "active_run.json")
        if not run or not run.is_running():
            raise RunnerError(f"No active lean1 run in {target}.")
    else:
        current = Path.cwd().resolve()
        run = None
        for directory in (current, *current.parents):
            candidate = _read_run(directory / ".lean1" / "active_run.json")
            if candidate and candidate.is_running():
                run = candidate
                break
        if run is None:
            candidates = {}
            for path in (current / ".lean1" / "active-runs").glob("*.json"):
                candidate = _read_run(path)
                if candidate and candidate.is_running() and _read_run(candidate.state / "active_run.json") == candidate:
                    candidates[candidate.workdir] = candidate
            # A problem campaign registers its directory and the nested problem
            # workspace; stopping the campaign also stops the nested run.
            outer = [
                candidate for candidate in candidates.values()
                if all(path.is_relative_to(candidate.workdir) for path in candidates)
            ]
            if len(candidates) > 1 and len(outer) == 1:
                candidates = {outer[0].workdir: outer[0]}
            if len(candidates) > 1:
                workspaces = "\n".join(f"  {path}" for path in sorted(candidates))
                raise RunnerError(f"Multiple active lean1 runs; select one with 'lean1 stop --workdir PATH':\n{workspaces}")
            run = next(iter(candidates.values()), None)
        if run is None:
            raise RunnerError(f"No active lean1 run found from {current}. Use 'lean1 stop --workdir PATH' to select a workspace.")
    atomic_write(run.state / "stop_requested", run.run_id + "\n")
    return run.workdir
