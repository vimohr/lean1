"""Work through randomly selected unsolved QIQCOP problems, one after another.

Each problem gets its own workspace under <campaign>/problems/<id>/. A
Lean-verified solution is checked by an independent review agent; an accepted
solution is then packaged as a manuscript and a local Git repository before
the next problem is selected. Any issue stops the campaign with an email.
"""

import hashlib
import json
import random
from dataclasses import dataclass, field, replace
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any

from .common import RunnerError, atomic_write, log, write_json
from .config import RunConfig
from .control import ActiveRun, active_run
from .issues import (
    EXIT_CONFIGURATION, EXIT_STOPPED, ReportedError, StopRequested, SupervisorIssue, issue_body, report_issue,
)
from .publication import Author, publish_solution
from .qiqcop import ZooClient, ZooProblem, pick_unsolved, standard_problem_text
from .review import review_solution
from .runner import supervise_outcome

STATE_FILE = "campaign.json"
ACTIVE_STATUSES = ("researching", "accepted")


@dataclass(frozen=True)
class CampaignConfig:
    root: Path
    run: RunConfig
    author: Author = Author()
    max_problems: int = 0
    client: ZooClient | None = field(default=None, compare=False)
    rng: random.Random | None = field(default=None, compare=False)


class CampaignState:
    """The campaign's problem history, persisted after every change."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, Any] = {"format_version": 1, "problems": []}
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise RunnerError(f"{path} is not valid JSON; repair or move it before resuming.") from exc
            if not isinstance(data, dict) or not isinstance(data.get("problems"), list):
                raise RunnerError(f"{path} has no problem list; repair or move it before resuming.")
            self.data = data

    @property
    def problems(self) -> list[dict[str, Any]]:
        return self.data["problems"]

    def attempted(self) -> set[str]:
        return {entry["id"] for entry in self.problems}

    def active(self) -> dict[str, Any] | None:
        return next((entry for entry in reversed(self.problems) if entry.get("status") in ACTIVE_STATUSES), None)

    def add(self, problem: ZooProblem, workspace: Path) -> dict[str, Any]:
        now = datetime.now().isoformat(timespec="seconds")
        entry = {
            "id": problem.id, "title": problem.title, "url": problem.url, "site": problem.site,
            "workspace": str(workspace), "status": "researching", "started": now, "updated": now,
        }
        self.problems.append(entry)
        self.save()
        return entry

    def update(self, entry: dict[str, Any], **values: Any) -> None:
        entry.update(values, updated=datetime.now().isoformat(timespec="seconds"))
        self.save()

    def save(self) -> None:
        write_json(self.path, self.data)


def prepare_workspace(problem: ZooProblem, record: dict[str, Any], workspace: Path) -> str:
    """Save the one-line problem statement and the website record for the agents."""
    inputs = workspace / ".lean1" / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    text = standard_problem_text(problem)
    atomic_write(inputs / "standard_problem.txt", text + "\n")
    serialized = json.dumps(record, indent=2, ensure_ascii=False) + "\n"
    atomic_write(inputs / "qiqcop-record.json", serialized)
    write_json(workspace / ".lean1" / "problem.json", {
        "kind": "standard", "source": problem.url, "id": problem.id, "title": problem.title,
        "site": problem.site, "record": ".lean1/inputs/qiqcop-record.json",
        "record_sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
    })
    return text


def _problem(entry: dict[str, Any]) -> ZooProblem:
    return ZooProblem(entry["id"], entry["title"], "Unsolved", entry["site"])


def _review_report(workspace: Path) -> str:
    output = json.loads((workspace / "codex_last_output.json").read_text(encoding="utf-8"))
    report = (output.get("acceptance") or {}).get("report")
    if not report:
        raise RunnerError(f"No accepted review report is recorded in {workspace / 'codex_last_output.json'}.")
    return report


def _resume_hint(root: Path) -> str:
    return (
        f"The campaign state is saved in {root / STATE_FILE}. Fix the cause, then rerun the same "
        f"lean1 --standard-problem command with --workdir {root} to resume the current problem."
    )


class Campaign:
    def __init__(self, config: CampaignConfig, control: ActiveRun) -> None:
        self.config = config
        self.root = config.root
        self.control = control
        self.state = CampaignState(self.root / STATE_FILE)
        self.client = config.client or ZooClient()
        self.rng = config.rng or random.SystemRandom()

    def stop_requested(self) -> bool:
        return self.control.stop_requested()

    def notify(self, entry: dict[str, Any], subject: str, body: str, *, always: bool = False) -> None:
        if not always and self.config.run.notify != "all":
            return
        name = f"campaign-{entry['id']}-{datetime.now():%Y%m%d-%H%M%S}"
        error = self.config.run.email.send_named(self.root, name, f"[{entry['id']}] {subject}", body)
        if error:
            log(self.root, error)

    def start_next(self) -> dict[str, Any]:
        problem, record = pick_unsolved(self.client, self.state.attempted(), self.rng)
        workspace = self.root / "problems" / problem.id
        prepare_workspace(problem, record, workspace)
        entry = self.state.add(problem, workspace)
        log(self.root, f"selected {problem.id}: {problem.title}")
        print(f"\nSelected {problem.id}: {problem.title}\n{problem.url}", flush=True)
        self.notify(entry, f"Starting {problem.title}", (
            f"lean1 selected a random unsolved problem.\n\nProblem: {problem.title}\nURL: {problem.url}\n"
            f"Workspace: {workspace}\nCampaign: {self.root}\n"
        ))
        return entry

    def research(self, entry: dict[str, Any]) -> int | None:
        """Return an exit code to stop the campaign, or None to continue."""
        workspace = Path(entry["workspace"])
        problem = _problem(entry)
        record_file = workspace / ".lean1" / "inputs" / "qiqcop-record.json"
        text = (workspace / ".lean1" / "inputs" / "standard_problem.txt").read_text(encoding="utf-8").strip()
        run = replace(
            self.config.run, workdir=workspace, problem=text, label=problem.id,
            acceptance=partial(review_solution, problem=problem, record_file=record_file, stop_requested=self.stop_requested),
            external_stop=self.stop_requested,
        )
        outcome = supervise_outcome(run)
        log(self.root, f"{problem.id}: research ended with {outcome.status} (exit {outcome.code})")
        if outcome.status == "complete":
            self.state.update(entry, status="accepted", review=_review_report(workspace))
            return None
        if outcome.status == "limit":
            self.state.update(entry, status="exhausted")
            print(f"No accepted solution for {problem.id} within --max-runs; selecting another problem.", flush=True)
            return None
        # Stops and issues leave the problem active so the next invocation resumes it.
        return outcome.code

    def publish(self, entry: dict[str, Any]) -> None:
        workspace = Path(entry["workspace"])
        problem = _problem(entry)
        run = replace(self.config.run, workdir=workspace, label=problem.id, external_stop=self.stop_requested)
        release = publish_solution(
            run, problem=problem, record_file=workspace / ".lean1" / "inputs" / "qiqcop-record.json",
            review_file=Path(entry["review"]), author=self.config.author, stop_requested=self.stop_requested,
        )
        self.state.update(entry, status="published", release=str(release))
        log(self.root, f"{problem.id}: publication package ready at {release}")
        self.notify(entry, f"Solved and packaged: {problem.title}", (
            "A Lean-verified solution passed independent review, and its publication package is ready "
            "for human inspection.\n\n"
            f"Problem: {problem.title}\nURL: {problem.url}\nWorkspace: {workspace}\n"
            f"Review: {entry['review']}\nRelease repository: {release}\n\n"
            "The repository has a local commit only. Inspect manuscript.pdf, HUMAN-CHECK.md, and "
            "VERIFY.md before publishing it on GitHub or Zenodo.\n"
        ), always=True)

    def run(self) -> int:
        processed = 0
        print(f"Campaign: {self.root}\nProblem source: {self.client.site}", flush=True)
        while True:
            if self.stop_requested():
                log(self.root, "campaign stopped after a graceful stop request")
                print("Campaign stopped; progress has been saved.", flush=True)
                return EXIT_STOPPED
            entry = self.state.active()
            if entry is None:
                if self.config.max_problems and processed >= self.config.max_problems:
                    print(f"Processed {processed} problem(s) as limited by --max-problems.", flush=True)
                    return 0
                entry = self.start_next()
            else:
                print(f"\nResuming {entry['id']}: {entry['title']} ({entry['status']})", flush=True)
            processed += 1
            if entry["status"] == "researching":
                code = self.research(entry)
                if code is not None:
                    return code
            if entry["status"] == "accepted":
                self.publish(entry)


def run_campaign(config: CampaignConfig) -> int:
    root = config.root
    root.mkdir(parents=True, exist_ok=True)
    with active_run(root) as control:
        campaign = Campaign(config, control)
        try:
            return campaign.run()
        except StopRequested:
            log(root, "campaign stopped after a graceful stop request")
            print("Campaign stopped; progress has been saved.", flush=True)
            return EXIT_STOPPED
        except KeyboardInterrupt:
            log(root, "campaign stopped by user")
            print("\nStopped; campaign progress has been saved.", flush=True)
            return EXIT_STOPPED
        except SupervisorIssue as issue:
            report_issue(config.run.email, root, str(issue), issue_body(issue, root, _resume_hint(root)))
            print(f"lean1 stopped: {issue}. An email has been sent.", flush=True)
            return issue.exit_code
        except ReportedError:
            raise
        except (OSError, RunnerError) as exc:
            issue = SupervisorIssue(f"campaign error: {exc}", kind="error", exit_code=EXIT_CONFIGURATION)
            report_issue(config.run.email, root, str(issue), issue_body(issue, root, _resume_hint(root)))
            raise ReportedError(str(exc)) from exc


__all__ = ["Campaign", "CampaignConfig", "CampaignState", "prepare_workspace", "run_campaign"]
