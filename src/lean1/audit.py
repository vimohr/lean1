"""Independent audit of a prepared publication package before a person publishes it.

The auditor searches for prior solutions, checks attribution and references, and looks
for mistakes. It must leave the package and the solution files unchanged.
"""

import hashlib
import json
import subprocess
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Callable

from .codex import codex_environment, execute_codex
from .common import atomic_write, log, read_tail, render_template
from .config import RunConfig
from .health import ensure_codex_healthy
from .issues import EXIT_AGENT_STAGE, StopRequested, SupervisorIssue
from .publication import RELEASE_DIR
from .qiqcop import ZooProblem
from .review import protected_files

AUDIT_ATTEMPTS = 2
VERDICTS = ("pass", "revise", "reject", "solved_elsewhere")
MARKERS = {f"AUDIT_VERDICT: {verdict.upper()}": verdict for verdict in VERDICTS}


class AuditError(SupervisorIssue):
    def __init__(self, message: str, detail: str = "") -> None:
        super().__init__(message, kind="audit", exit_code=EXIT_AGENT_STAGE, detail=detail)


@dataclass(frozen=True)
class AuditVerdict:
    verdict: str
    summary: str
    issues: list[str]
    prior_work: list[str]
    report_file: Path

    def describe(self) -> str:
        """Plain-text summary for feedback and emails."""
        text = f"Auditor summary: {self.summary}\n"
        if self.issues:
            text += "\nIssues:\n" + "\n".join(f"- {issue}" for issue in self.issues) + "\n"
        if self.prior_work:
            text += "\nPrior work found by the auditor:\n" + "\n".join(f"- {source}" for source in self.prior_work) + "\n"
        return text + f"\nFull audit: {self.report_file}\n"


def audited_files(workdir: Path) -> dict[str, str]:
    """Hash the solution files and every release file, including the release commit."""
    hashes = protected_files(workdir)
    release = workdir / RELEASE_DIR
    for path in release.rglob("*"):
        relative = path.relative_to(release)
        if relative.parts[0] == ".git" or not path.is_file():
            continue
        hashes[f"{RELEASE_DIR}/{relative.as_posix()}"] = hashlib.sha256(path.read_bytes()).hexdigest()
    head = subprocess.run(["git", "-C", str(release), "rev-parse", "HEAD"], text=True, capture_output=True, timeout=60)
    hashes[f"{RELEASE_DIR} commit"] = head.stdout.strip() if head.returncode == 0 else ""
    return hashes


def changed_files(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """Modified or deleted files, plus any file added to the release."""
    names = before.keys() | {name for name in after if name.startswith(f"{RELEASE_DIR}/")}
    return sorted(name for name in names if before.get(name) != after.get(name))


def _string_list(report: dict, key: str) -> list[str]:
    value = report.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise AuditError(f"the audit report's {key} must be a list of strings")
    return value


def parse_audit(final_message: str | None, report_file: Path, problem_id: str) -> AuditVerdict:
    """Require the final marker and the JSON report to agree."""
    lines = (final_message or "").strip().splitlines()
    marker = MARKERS.get(lines[-1].strip() if lines else "")
    if marker is None:
        raise AuditError("the audit agent did not end with an AUDIT_VERDICT line")
    try:
        report = json.loads(report_file.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AuditError(f"the audit agent did not write {report_file.name}") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AuditError(f"the audit report {report_file.name} is not valid JSON: {exc}") from exc
    if not isinstance(report, dict):
        raise AuditError("the audit report must be a JSON object")
    verdict = report.get("verdict")
    if verdict not in VERDICTS:
        raise AuditError(f"the audit report must set verdict to one of {', '.join(VERDICTS)}")
    if verdict != marker:
        raise AuditError("the audit report and final AUDIT_VERDICT line disagree")
    if report.get("problem_id") != problem_id:
        raise AuditError(f"the audit report names problem {report.get('problem_id')!r}, not {problem_id}")
    summary = report.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise AuditError("the audit report has no summary")
    issues = _string_list(report, "issues")
    prior_work = _string_list(report, "prior_work")
    if verdict != "pass" and not issues:
        raise AuditError(f"an audit with verdict {verdict} must list at least one issue")
    if verdict == "solved_elsewhere" and not prior_work:
        raise AuditError("a solved_elsewhere audit must cite the resolving source in prior_work")
    return AuditVerdict(verdict, summary.strip(), issues, prior_work, report_file)


def audit_prompt(
    config: RunConfig, problem: ZooProblem, record_file: Path, review_file: Path, report_file: Path,
) -> str:
    template = files("lean1").joinpath("audit_prompt.txt").read_text(encoding="utf-8")
    return render_template(template, {
        "PROBLEM_URL": problem.url,
        "PROBLEM_JSON_URL": problem.json_url,
        "PROBLEM_ID": problem.id,
        "PROBLEM_TITLE": problem.title,
        "RECORD_FILE": record_file.relative_to(config.workdir).as_posix(),
        "REVIEW_FILE": review_file.relative_to(config.workdir).as_posix() if review_file.is_relative_to(config.workdir) else str(review_file),
        "AUDIT_FILE": report_file.relative_to(config.workdir).as_posix(),
        "RELEASE_DIR": RELEASE_DIR,
    })


def revision_feedback(verdict: AuditVerdict) -> str:
    return (
        "An independent audit of the package in release/ found defects. Update the existing package "
        "and commit the changes; do not change the proofs or the mathematical result.\n\n"
        + verdict.describe()
    )


def audit_release(
    config: RunConfig, *, problem: ZooProblem, record_file: Path, review_file: Path, round_number: int,
    stop_requested: Callable[[], bool] = lambda: False,
) -> AuditVerdict:
    """Run a fresh Codex audit; raise AuditError when no valid verdict is produced."""
    state = config.workdir / ".lean1" / "audit"
    state.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []
    for attempt in range(1, AUDIT_ATTEMPTS + 1):
        stem = f"audit-{round_number:02d}-{attempt}"
        report_file = state / f"{stem}.json"
        report_file.unlink(missing_ok=True)
        if not ensure_codex_healthy(config, f"audit of {problem.id}", stop_requested):
            raise StopRequested
        print(f"\n=== Release audit for {problem.id} (round {round_number}, attempt {attempt}) ===", flush=True)
        prompt_file = state / f"{stem}.prompt.txt"
        atomic_write(prompt_file, audit_prompt(config, problem, record_file, review_file, report_file))
        before = audited_files(config.workdir)
        result = execute_codex(
            config.agent_command(), cwd=config.workdir,
            env=codex_environment(config.workdir, config.tools.env), prompt_file=prompt_file,
            events_file=state / f"{stem}.events.jsonl", stderr_file=state / f"{stem}.stderr.log",
        )
        changed = changed_files(before, audited_files(config.workdir))
        if changed:
            raise AuditError("the audit agent modified the release or solution files", "Changed files:\n" + "\n".join(changed))
        if result.returncode:
            failures.append(f"attempt {attempt}: Codex exited with code {result.returncode}\n{read_tail(result.stderr_file, 4000)}")
            continue
        try:
            verdict = parse_audit(result.final_message, report_file, problem.id)
        except AuditError as exc:
            failures.append(f"attempt {attempt}: {exc}")
            continue
        log(config.workdir, f"audit round {round_number}: {verdict.verdict}")
        print(f"Audit verdict: {verdict.verdict.upper()}", flush=True)
        return verdict
    raise AuditError(
        f"the audit agent failed to produce a valid verdict in {AUDIT_ATTEMPTS} attempts",
        "\n\n".join(failures) + f"\n\nLogs: {state}",
    )


__all__ = ["AuditError", "AuditVerdict", "audit_release", "changed_files", "parse_audit", "revision_feedback"]
