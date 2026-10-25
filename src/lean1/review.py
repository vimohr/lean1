"""Independent review of a Lean-verified solution against the website's statement."""

import hashlib
import json
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any, Callable

from .codex import codex_environment, execute_codex
from .common import atomic_write, log, read_tail, render_template
from .config import Acceptance, RunConfig
from .health import ensure_codex_healthy
from .issues import EXIT_AGENT_STAGE, StopRequested, SupervisorIssue
from .qiqcop import ZooProblem

ACCEPT_MARKER = "REVIEW_VERDICT: ACCEPT"
REJECT_MARKER = "REVIEW_VERDICT: REJECT"
REVIEW_ATTEMPTS = 2
# Directories whose contents are agent state, build output, or later release work.
EXCLUDED_PARTS = {".lean1", ".codex", ".lake", ".git", "lake-packages", "release", "__pycache__"}
PROTECTED_NAMES = {
    "lean_verification.json", "result.md", "lakefile.lean", "lakefile.toml",
    "lean-toolchain", "lake-manifest.json",
}


class ReviewError(SupervisorIssue):
    def __init__(self, message: str, detail: str = "") -> None:
        super().__init__(message, kind="review", exit_code=EXIT_AGENT_STAGE, detail=detail)


@dataclass(frozen=True)
class Verdict:
    accepted: bool
    summary: str
    issues: list[str]
    report_file: Path


def protected_files(workdir: Path) -> dict[str, str]:
    """Hash the solution files a reviewer must leave unchanged."""
    hashes: dict[str, str] = {}
    for path in workdir.rglob("*"):
        relative = path.relative_to(workdir)
        if EXCLUDED_PARTS.intersection(relative.parts) or not path.is_file():
            continue
        if path.suffix == ".lean" or path.name in PROTECTED_NAMES:
            hashes[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def changed_files(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """Modified or deleted files; new scratch files cannot affect the declared proofs."""
    return sorted(name for name, digest in before.items() if after.get(name) != digest)


def parse_verdict(final_message: str | None, report_file: Path, problem_id: str) -> Verdict:
    """Require the final marker and the JSON report to agree."""
    lines = (final_message or "").strip().splitlines()
    marker = lines[-1].strip() if lines else ""
    if marker not in {ACCEPT_MARKER, REJECT_MARKER}:
        raise ReviewError("the review agent did not end with a REVIEW_VERDICT line")
    try:
        report = json.loads(report_file.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ReviewError(f"the review agent did not write {report_file.name}") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewError(f"the review report {report_file.name} is not valid JSON: {exc}") from exc
    if not isinstance(report, dict):
        raise ReviewError("the review report must be a JSON object")
    verdict = report.get("verdict")
    if verdict not in {"accept", "reject"}:
        raise ReviewError("the review report must set verdict to accept or reject")
    if (verdict == "accept") != (marker == ACCEPT_MARKER):
        raise ReviewError("the review report and final REVIEW_VERDICT line disagree")
    if report.get("problem_id") != problem_id:
        raise ReviewError(f"the review report names problem {report.get('problem_id')!r}, not {problem_id}")
    summary = report.get("summary")
    issues = report.get("issues", [])
    if not isinstance(summary, str) or not summary.strip():
        raise ReviewError("the review report has no summary")
    if not isinstance(issues, list) or not all(isinstance(issue, str) for issue in issues):
        raise ReviewError("the review report's issues must be a list of strings")
    if verdict == "reject" and not issues:
        raise ReviewError("a rejecting review must list at least one issue")
    return Verdict(verdict == "accept", summary.strip(), issues, report_file)


def review_prompt(config: RunConfig, problem: ZooProblem, record_file: Path, report_file: Path) -> str:
    template = files("lean1").joinpath("review_prompt.txt").read_text(encoding="utf-8")
    return render_template(template, {
        "PROBLEM": config.problem,
        "PROBLEM_URL": problem.url,
        "PROBLEM_JSON_URL": problem.json_url,
        "PROBLEM_ID": problem.id,
        "PROBLEM_TITLE": problem.title,
        "RECORD_FILE": record_file.relative_to(config.workdir).as_posix(),
        "REVIEW_FILE": report_file.relative_to(config.workdir).as_posix(),
        "LEAN_COMMAND": config.tools.lean,
        "LAKE_COMMAND": config.tools.lake,
    })


def rejection_feedback(verdict: Verdict) -> str:
    issues = "\n".join(f"- {issue}" for issue in verdict.issues)
    return (
        "An independent review agent checked the Lean-verified solution against the problem "
        "as stated on the website and rejected it.\n\n"
        f"Reviewer summary: {verdict.summary}\n\nIssues to resolve:\n{issues}\n\n"
        f"Full review: {verdict.report_file}\n"
        "Fix these issues; a solution must answer the exact question on the website."
    )


def review_solution(
    config: RunConfig, output: dict[str, Any], *, problem: ZooProblem, record_file: Path,
    stop_requested: Callable[[], bool] = lambda: False,
) -> Acceptance:
    """Run a fresh Codex review; raise ReviewError when no valid verdict is produced."""
    state = config.workdir / ".lean1" / "review"
    state.mkdir(parents=True, exist_ok=True)
    run_number = int(output.get("run_number", 0))
    failures: list[str] = []
    for attempt in range(1, REVIEW_ATTEMPTS + 1):
        stem = f"review-{run_number:04d}-{attempt}"
        report_file = state / f"{stem}.json"
        report_file.unlink(missing_ok=True)
        if not ensure_codex_healthy(config, f"review of run #{run_number}", stop_requested):
            raise StopRequested
        print(f"\n=== Independent review of run #{run_number} (attempt {attempt}) ===", flush=True)
        prompt_file = state / f"{stem}.prompt.txt"
        atomic_write(prompt_file, review_prompt(config, problem, record_file, report_file))
        before = protected_files(config.workdir)
        result = execute_codex(
            config.agent_command(), cwd=config.workdir,
            env=codex_environment(config.workdir, config.tools.env), prompt_file=prompt_file,
            events_file=state / f"{stem}.events.jsonl", stderr_file=state / f"{stem}.stderr.log",
        )
        changed = changed_files(before, protected_files(config.workdir))
        if changed:
            # A changed proof invalidates both the review and the earlier verification.
            raise ReviewError(
                "the review agent modified solution files",
                "Changed files:\n" + "\n".join(changed),
            )
        if result.returncode:
            failures.append(f"attempt {attempt}: Codex exited with code {result.returncode}\n{read_tail(result.stderr_file, 4000)}")
            continue
        try:
            verdict = parse_verdict(result.final_message, report_file, problem.id)
        except ReviewError as exc:
            failures.append(f"attempt {attempt}: {exc}")
            continue
        log(config.workdir, f"review of run #{run_number}: {'accepted' if verdict.accepted else 'rejected'}")
        print(f"Review verdict: {'ACCEPT' if verdict.accepted else 'REJECT'}", flush=True)
        if verdict.accepted:
            return Acceptance(True, verdict.summary, report=str(report_file))
        return Acceptance(False, verdict.summary, rejection_feedback(verdict), str(report_file))
    raise ReviewError(
        f"the review agent failed to produce a valid verdict in {REVIEW_ATTEMPTS} attempts",
        "\n\n".join(failures) + f"\n\nLogs: {state}",
    )
