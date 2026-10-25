"""Prepare a manuscript and a GitHub-ready repository for an accepted solution."""

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import date
from importlib.resources import files
from pathlib import Path
from typing import Callable

from .codex import codex_environment, execute_codex
from .common import atomic_write, log, read_tail, render_template
from .config import RunConfig
from .health import ensure_codex_healthy
from .issues import EXIT_AGENT_STAGE, StopRequested, SupervisorIssue
from .qiqcop import ZooProblem

READY_MARKER = "PUBLICATION_STATUS: READY"
RELEASE_DIR = "release"
PUBLICATION_ATTEMPTS = 2
REQUIRED_FILES = (
    "README.md", "LICENSE", "RIGHTS.txt", "manuscript.tex", "manuscript.pdf", "references.bib",
    "VERIFY.md", "HUMAN-CHECK.md", "PROVENANCE.md", "REVIEW.txt", "RELEASE_NOTES.md",
    "CITATION.cff", ".zenodo.json", ".gitignore", "verify_artifact.py",
    "artifact/source_manifest.json", "artifact/ReadStatement.lean", "artifact/ProofAudit.lean",
    ".github/workflows/lean.yml", "release-manifest.json",
)
FORBIDDEN_PARTS = {".codex", ".lean1", ".env", "auth.json"}
GENERATED_SUFFIXES = {".olean", ".ilean", ".pyc"}


class PublicationError(SupervisorIssue):
    def __init__(self, message: str, detail: str = "") -> None:
        super().__init__(message, kind="publication", exit_code=EXIT_AGENT_STAGE, detail=detail)


@dataclass(frozen=True)
class Author:
    name: str = ""
    affiliation: str = ""
    orcid: str = ""

    def describe(self) -> str:
        if not self.name:
            return (
                "Author: not supplied. Use the placeholder AUTHOR NAME everywhere an author is "
                "required, and list it in preparation-status.txt as a required human edit."
            )
        lines = [f"Author: {self.name}"]
        if self.affiliation:
            lines.append(f"Affiliation: {self.affiliation}")
        if self.orcid:
            lines.append(f"ORCID: {self.orcid}")
        lines.append("The author initiated the automated research run; credit the research itself to the agents.")
        return "\n".join(lines)


def repository_name(problem: ZooProblem) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", problem.title.lower()).strip("-")
    slug = slug[:60].rstrip("-") or "solution"
    return f"{slug}-{problem.id.removeprefix('op_')[:6]}"


def _git(release: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(release), *arguments], text=True, capture_output=True, timeout=60)


def check_release(release: Path) -> list[str]:
    """Return every defect of a prepared release; an empty list means it is ready."""
    if not release.is_dir():
        return [f"{release} does not exist."]
    problems: list[str] = []
    for relative in REQUIRED_FILES:
        path = release / relative
        if not path.is_file() or path.stat().st_size == 0:
            problems.append(f"Missing or empty {relative}.")
    pdf = release / "manuscript.pdf"
    if pdf.is_file() and not pdf.read_bytes()[:5] == b"%PDF-":
        problems.append("manuscript.pdf is not a PDF file.")
    lean = release / "artifact" / "lean"
    if not (lean / "lean-toolchain").is_file():
        problems.append("artifact/lean/lean-toolchain is missing.")
    if not any(path.suffix == ".lean" for path in lean.rglob("*") if ".lake" not in path.parts):
        problems.append("artifact/lean contains no Lean sources.")
    for name, required in ((".zenodo.json", ("title", "creators", "description", "license")), ("artifact/source_manifest.json", ())):
        path = release / name
        if not path.is_file():
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            problems.append(f"{name} is not valid JSON: {exc}")
            continue
        missing = [key for key in required if not isinstance(document, dict) or not document.get(key)]
        if missing:
            problems.append(f"{name} lacks {', '.join(missing)}.")
    passed = []
    for result in (release / "verification").rglob("result.json"):
        try:
            passed.append(json.loads(result.read_text(encoding="utf-8")).get("status") == "PASS")
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
            passed.append(False)
    if not any(passed):
        problems.append("No verification/**/result.json records a PASS from verify_artifact.py.")
    for path in release.rglob("*"):
        relative = path.relative_to(release)
        if relative.parts[0] == ".git":
            continue
        if FORBIDDEN_PARTS.intersection(relative.parts) or path.name.startswith(".env"):
            problems.append(f"Private agent state or credentials must not be released: {relative}")
    problems.extend(_check_git(release))
    return problems


def _check_git(release: Path) -> list[str]:
    if not shutil.which("git"):
        return ["git is not available to check the release repository."]
    if not (release / ".git").exists():
        return ["The release is not a Git repository."]
    if _git(release, "rev-parse", "--verify", "HEAD").returncode:
        return ["The release repository has no commit."]
    problems = []
    status = _git(release, "status", "--porcelain")
    if status.returncode or status.stdout.strip():
        problems.append("The release repository has uncommitted changes:\n" + status.stdout.strip())
    tracked = set(_git(release, "ls-files").stdout.splitlines())
    for relative in REQUIRED_FILES:
        if (release / relative).is_file() and relative not in tracked:
            problems.append(f"{relative} is not committed.")
    for relative in tracked:
        parts = set(Path(relative).parts)
        if ".lake" in parts or Path(relative).suffix in GENERATED_SUFFIXES:
            problems.append(f"Generated build output is committed: {relative}")
    if _git(release, "remote").stdout.strip():
        problems.append("The release repository must not have a remote; a person publishes it.")
    return problems


def publication_prompt(
    config: RunConfig, problem: ZooProblem, record_file: Path, review_file: Path,
    author: Author, feedback: str = "",
) -> str:
    template = files("lean1").joinpath("publish_prompt.txt").read_text(encoding="utf-8")
    return render_template(template, {
        "PROBLEM_URL": problem.url,
        "PROBLEM_JSON_URL": problem.json_url,
        "PROBLEM_ID": problem.id,
        "PROBLEM_TITLE": problem.title,
        "RECORD_FILE": record_file.relative_to(config.workdir).as_posix(),
        "REVIEW_FILE": review_file.relative_to(config.workdir).as_posix() if review_file.is_relative_to(config.workdir) else str(review_file),
        "RELEASE_DIR": RELEASE_DIR,
        "REPOSITORY_NAME": repository_name(problem),
        "AUTHOR": author.describe(),
        "DATE": date.today().isoformat(),
        "MODEL": config.model or "the model configured for Codex",
        "LEAN_COMMAND": config.tools.lean,
        "LAKE_COMMAND": config.tools.lake,
        "FEEDBACK": f"\n## Feedback from the supervisor on a previous attempt\n\n{feedback}\n" if feedback else "",
    })


def publish_solution(
    config: RunConfig, *, problem: ZooProblem, record_file: Path, review_file: Path,
    author: Author, stop_requested: Callable[[], bool] = lambda: False,
) -> Path:
    """Run the publication agent until the release passes `check_release`."""
    state = config.workdir / ".lean1" / "publication"
    state.mkdir(parents=True, exist_ok=True)
    release = config.workdir / RELEASE_DIR
    feedback = ""
    failures: list[str] = []
    for attempt in range(1, PUBLICATION_ATTEMPTS + 1):
        if not ensure_codex_healthy(config, f"publication of {problem.id}", stop_requested):
            raise StopRequested
        print(f"\n=== Publication package for {problem.id} (attempt {attempt}) ===", flush=True)
        stem = f"publication-{attempt}"
        prompt_file = state / f"{stem}.prompt.txt"
        atomic_write(prompt_file, publication_prompt(config, problem, record_file, review_file, author, feedback))
        result = execute_codex(
            config.agent_command(), cwd=config.workdir,
            env=codex_environment(config.workdir, config.tools.env), prompt_file=prompt_file,
            events_file=state / f"{stem}.events.jsonl", stderr_file=state / f"{stem}.stderr.log",
        )
        defects = check_release(release)
        lines = (result.final_message or "").strip().splitlines()
        if result.returncode:
            defects.insert(0, f"Codex exited with code {result.returncode}.\n{read_tail(result.stderr_file, 4000)}")
        elif not lines or lines[-1].strip() != READY_MARKER:
            defects.insert(0, f"The final response did not end with {READY_MARKER}.")
        if not defects:
            log(config.workdir, f"publication package ready: {release}")
            return release
        feedback = "\n".join(f"- {defect}" for defect in defects)
        failures.append(f"attempt {attempt}:\n{feedback}")
        log(config.workdir, f"publication attempt {attempt} incomplete: {len(defects)} defect(s)")
    raise PublicationError(
        f"the publication package for {problem.id} is incomplete after {PUBLICATION_ATTEMPTS} attempts",
        "\n\n".join(failures) + f"\n\nRelease directory: {release}\nLogs: {state}",
    )
