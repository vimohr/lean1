"""The lean1 console command and permission-aware setup command."""

import argparse
import math
import shutil
import sys
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from . import __version__
from .common import RunnerError
from .lean import ensure_lean
from .notify import EmailConfig
from .problems import prepare_problem, resolve_problem
from .runner import RunConfig, supervise


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
        epilog="Run 'lean1 setup' to check/install Lean separately. Codex CLI must already be installed and authenticated.",
    )
    parser.add_argument("--version", action="version", version=f"lean1 {__version__}")
    parser.add_argument("--email", required=True, help="Email recipient for run notifications.")
    problems = parser.add_mutually_exclusive_group(required=True)
    problems.add_argument("--problem", help="Problem text, an http(s) URL, an existing file path, or @path.")
    problems.add_argument("--problem-text", help="Literal problem text (disable URL/file detection).")
    problems.add_argument("--problem-url", help="Website or document URL to solve.")
    problems.add_argument("--problem-file", help="Problem file to copy into the workspace; '-' reads UTF-8 text from stdin.")
    parser.add_argument("--workdir", type=Path, help="Agent workspace; reuse it to continue work (default: ./lean1-runs/<timestamp>-<id>).")
    parser.add_argument("--prompt-file", type=Path, help="Custom prompt template containing {{PROBLEM}} (default: bundled prompt.txt).")
    parser.add_argument("--codex-command", default="codex", help="Codex executable name/path (default: codex).")
    parser.add_argument("--model", help="Override the configured Codex model.")
    parser.add_argument("--max-runs", type=nonnegative_integer, default=0, help="Maximum attempts in this invocation; 0 retries until verified (default: 0).")
    parser.add_argument("--restart-delay", type=nonnegative_float, default=3, help="Seconds between attempts (default: 3).")
    parser.add_argument("--verification-timeout", type=positive_float, default=1800, help="Seconds allowed per Lean/build command (default: 1800).")
    parser.add_argument("--notify", choices=("all", "complete"), default="all", help="Email after every attempt (all), or only verified completion (complete).")
    parser.add_argument("--email-backend", choices=("auto", "mail", "smtp"), default="auto", help="auto uses SMTP when LEAN1_SMTP_HOST is set, otherwise mail.")
    parser.add_argument("--mail-command", default="mail", help="Local mail executable name/path (default: mail).")
    parser.add_argument("--no-network", action="store_true", help="Disable Codex web search and network access inside its workspace sandbox.")
    lean_options(parser)
    return parser


def setup_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lean1 setup", description="Check Lean 4/Lake and offer a local installation if needed. No agent or email is started.")
    parser.add_argument("--workdir", type=Path, default=Path.cwd(), help="Directory whose Lean toolchain should be checked (default: current directory).")
    lean_options(parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    setup = arguments[:1] == ["setup"]
    parser = setup_parser() if setup else run_parser()
    options = parser.parse_args(arguments[1:] if setup else arguments)
    try:
        if setup:
            workdir = options.workdir.expanduser().resolve()
            if not workdir.is_dir():
                raise RunnerError(f"Setup directory does not exist: {workdir}")
            ensure_lean(
                workdir, options.lean_home, allow_install=options.install_lean,
                no_install=options.no_install_lean, toolchain=options.lean_toolchain,
            )
            return 0
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
            Path.cwd() / "lean1-runs" / f"{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:8]}"
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
        rendered_problem = prepare_problem(problem, workdir)
        return supervise(RunConfig(
            workdir=workdir, problem=rendered_problem, tools=tools, email=email, codex=codex,
            model=options.model, prompt_file=prompt_file, restart_delay=options.restart_delay,
            max_runs=options.max_runs, notify=options.notify, network=not options.no_network,
            verification_timeout=options.verification_timeout,
        ))
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        return 130
    except (RunnerError, OSError, ValueError) as exc:
        print(f"lean1: {exc}", file=sys.stderr)
        return 2
