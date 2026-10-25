"""Command-line interface for research runs and local Lean setup."""

import argparse
import math
import shutil
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from . import __version__
from .campaign import CampaignConfig, run_campaign
from .common import RunnerError
from .config import HealthCheckConfig, RunConfig
from .control import request_stop
from .lean import ensure_lean
from .notify import EmailConfig
from .problems import prepare_problem, resolve_problem
from .publication import Author
from .qiqcop import site_url
from .runner import supervise


def nonnegative_integer(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if number < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return number


def nonnegative_float(value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not math.isfinite(number) or number < 0:
        raise argparse.ArgumentTypeError("must be a finite number, zero or greater")
    return number


def positive_float(value: str) -> float:
    number = nonnegative_float(value)
    if number == 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return number


def lean_options(parser: argparse.ArgumentParser) -> None:
    permission = parser.add_mutually_exclusive_group()
    permission.add_argument("--install-lean", action="store_true", help="Give permission to download/install local Elan and Lean without prompting.")
    permission.add_argument("--no-install-lean", action="store_true", help="Fail if Lean 4/Lake are unavailable; never offer installation.")
    parser.add_argument("--lean-home", type=Path, help="Local Elan installation directory (default: ./.elan in the directory lean1 is launched from).")
    parser.add_argument("--lean-toolchain", help="Toolchain to install if missing (default: project's lean-toolchain, then leanprover/lean4:stable).")


def run_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lean1",
        description="Run a Codex research agent until its solution passes Lean verification, then email the result.",
        epilog="Run 'lean1 setup' to check/install Lean separately, or 'lean1 stop' to stop after the current attempt. Codex CLI must already be installed and authenticated.",
    )
    parser.add_argument("--version", action="version", version=f"lean1 {__version__}")
    parser.add_argument("--email", required=True, help="Email recipient for run notifications.")
    problems = parser.add_mutually_exclusive_group(required=True)
    problems.add_argument("--problem", help="Problem text, an http(s) URL, an existing file path, or @path.")
    problems.add_argument("--problem-text", help="Literal problem text (disable URL/file detection).")
    problems.add_argument("--problem-url", help="Website or document URL to solve.")
    problems.add_argument("--problem-file", help="Problem file to copy into the workspace; '-' reads UTF-8 text from stdin.")
    problems.add_argument(
        "--standard-problem", action="store_true",
        help="Work through random unsolved problems from https://qiqc-op.com/, one after another; "
        "accepted solutions are reviewed and packaged for publication.",
    )
    parser.add_argument("--workdir", type=Path, help="Agent workspace; reuse it to continue work (default: ./lean1-runs/<timestamp>-<id>).")
    parser.add_argument("--prompt-file", type=Path, help="Custom prompt template containing {{PROBLEM}} (default: bundled prompt.txt).")
    parser.add_argument("--codex-command", default="codex", help="Codex executable name/path (default: codex).")
    parser.add_argument("--model", help="Override the configured Codex model.")
    parser.add_argument("--max-runs", type=nonnegative_integer, default=0, help="Maximum attempts in this invocation (per problem with --standard-problem); 0 retries until verified or the failsafe stops retries (default: 0).")
    parser.add_argument("--max-problems", type=nonnegative_integer, default=0, help="With --standard-problem, stop after this many problems; 0 continues until an issue or a stop request (default: 0).")
    parser.add_argument("--restart-delay", type=nonnegative_float, default=3, help="Seconds between attempts (default: 3).")
    parser.add_argument("--verification-timeout", type=positive_float, default=1800, help="Seconds allowed per Lean/build command (default: 1800).")
    parser.add_argument("--notify", choices=("all", "complete"), default="all", help="Email after every attempt (all), or only verified completion (complete).")
    parser.add_argument("--email-backend", choices=("auto", "mail", "smtp"), default="auto", help="auto uses SMTP when LEAN1_SMTP_HOST is set, otherwise mail.")
    parser.add_argument("--mail-command", default="mail", help="Local mail executable name/path (default: mail).")
    parser.add_argument("--no-network", action="store_true", help="Disable Codex web search and network access inside its workspace sandbox.")
    health = parser.add_argument_group("Codex health check", "A short Codex execution before every real one; failure pauses lean1 and sends an email.")
    health.add_argument("--no-health-check", action="store_true", help="Skip the health check before each Codex execution.")
    health.add_argument("--health-check-timeout", type=positive_float, default=300, help="Seconds allowed for one health check (default: 300).")
    health.add_argument("--health-check-retries", type=nonnegative_integer, default=1, help="Additional health checks, 30 seconds apart, before pausing (default: 1).")
    publication = parser.add_argument_group("Publication metadata", "Author details for manuscripts prepared with --standard-problem.")
    publication.add_argument("--author-name", default="", help="Author name for the manuscript and citation metadata.")
    publication.add_argument("--author-affiliation", default="", help="Author affiliation.")
    publication.add_argument("--author-orcid", default="", help="Author ORCID iD, such as 0000-0002-1825-0097.")
    lean_options(parser)
    return parser


def setup_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lean1 setup", description="Check Lean 4/Lake and offer a local installation if needed. No agent or email is started.")
    parser.add_argument("--workdir", type=Path, default=Path.cwd(), help="Directory whose Lean toolchain should be checked (default: current directory).")
    lean_options(parser)
    return parser


def stop_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lean1 stop", description="Stop a running supervisor after its current Codex attempt, verification, and notifications finish.")
    parser.add_argument("--workdir", type=Path, help="Workspace to stop (default: the current workspace or the only active run launched from this directory).")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    setup = arguments[:1] == ["setup"]
    stop = arguments[:1] == ["stop"]
    parser = stop_parser() if stop else setup_parser() if setup else run_parser()
    options = parser.parse_args(arguments[1:] if setup or stop else arguments)
    try:
        if stop:
            workdir = request_stop(options.workdir)
            print(f"Stop requested for {workdir}. The current attempt, verification, and notifications will finish before the supervisor exits.", flush=True)
            return 0
        if setup:
            workdir = options.workdir.expanduser().resolve()
            if not workdir.is_dir():
                raise RunnerError(f"Setup directory does not exist: {workdir}")
            ensure_lean(
                workdir, options.lean_home, allow_install=options.install_lean,
                no_install=options.no_install_lean, toolchain=options.lean_toolchain,
            )
            return 0
        standard = options.standard_problem
        if standard:
            if options.no_network:
                raise RunnerError("--standard-problem needs network access for the agents to read the problem website.")
            site_url()
        else:
            if options.max_problems:
                raise RunnerError("--max-problems applies only with --standard-problem.")
            kind, value = next(
                (kind, value) for kind, value in (
                    ("auto", options.problem), ("text", options.problem_text),
                    ("url", options.problem_url), ("file", options.problem_file),
                ) if value is not None
            )
            problem = resolve_problem(value, kind)
        codex = shutil.which(options.codex_command)
        if not codex:
            raise RunnerError(f"Codex executable {options.codex_command!r} was not found. Install Codex CLI and run 'codex login' first.")
        codex = str(Path(codex).absolute())
        email = EmailConfig.load(options.email, options.email_backend, options.mail_command)
        prompt_file = options.prompt_file.expanduser().resolve() if options.prompt_file else None
        if prompt_file and not prompt_file.is_file():
            raise RunnerError(f"Prompt template not found: {prompt_file}")
        workdir = (
            options.workdir.expanduser() if options.workdir else
            Path.cwd() / "lean1-runs" / f"{datetime.now():%Y%m%d-%H%M%S}-{'standard-' if standard else ''}{uuid4().hex[:8]}"
        ).resolve()
        workdir.mkdir(parents=True, exist_ok=True)
        # Check actual writes rather than trusting permission bits or os.access.
        with NamedTemporaryFile(dir=workdir) as stream:
            stream.write(b"lean1 workspace write check\n")
            stream.flush()
        tools = ensure_lean(
            workdir, options.lean_home, allow_install=options.install_lean,
            no_install=options.no_install_lean, toolchain=options.lean_toolchain,
        )
        run = RunConfig(
            workdir=workdir, problem="", tools=tools, email=email, codex=codex,
            model=options.model, prompt_file=prompt_file, restart_delay=options.restart_delay,
            max_runs=options.max_runs, notify=options.notify, network=not options.no_network,
            verification_timeout=options.verification_timeout,
            health=None if options.no_health_check else HealthCheckConfig(
                timeout=options.health_check_timeout, retries=options.health_check_retries,
            ),
        )
        if standard:
            author = Author(options.author_name.strip(), options.author_affiliation.strip(), options.author_orcid.strip())
            return run_campaign(CampaignConfig(root=workdir, run=run, author=author, max_problems=options.max_problems))
        return supervise(replace(run, problem=prepare_problem(problem, workdir)))
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        return 130
    except (RunnerError, OSError, ValueError) as exc:
        print(f"lean1: {exc}", file=sys.stderr)
        return 2
