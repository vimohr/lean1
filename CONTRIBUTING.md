# Contributing

Bug reports, documentation improvements, and focused code changes are welcome through [GitHub issues](https://github.com/vimohr/lean1/issues) and pull requests.

Contributions are made under the project's [MIT License](LICENSE).

## Development setup

Install Python 3.11 or newer, Git, and [uv](https://docs.astral.sh/uv/getting-started/installation/), then clone the repository:

```sh
git clone https://github.com/vimohr/lean1.git
cd lean1
uv sync --locked
uv run lean1 --help
```

For an editable installation on the shell's tool path, use `uv tool install --editable .`. The compatibility entry point `python runner.py --help` also loads the package from `src/`.

## Project layout

| Path | Responsibility |
| --- | --- |
| `src/lean1/cli.py` | Argument parsing and command setup. |
| `src/lean1/problems.py` | Problem input detection, copying, and URL snapshots. |
| `src/lean1/lean.py` | Local Lean discovery and installation. |
| `src/lean1/runner.py` | Research attempts, retries, logs, and completion handling. |
| `src/lean1/codex.py` | Codex command construction, execution, event parsing, and timeouts. |
| `src/lean1/config.py` | Shared run and health-check configuration. |
| `src/lean1/health.py` | The Codex health check before every execution. |
| `src/lean1/issues.py` | Exit codes and the emails sent for every issue stop. |
| `src/lean1/qiqcop.py` | Validated selection of unsolved problems from qiqc-op.com. |
| `src/lean1/campaign.py` | The `--standard-problem` loop, its state, and resumption. |
| `src/lean1/review.py` | The independent review agent and verdict validation. |
| `src/lean1/publication.py` | The publication agent and release package checks. |
| `src/lean1/audit.py` | The release audit agent: prior work, attribution, and mistakes. |
| `src/lean1/prior.py` | Reading the prior-work verdict in `result.md`. |
| `src/lean1/control.py` | Active workspace registration and graceful stop requests. |
| `src/lean1/verification.py` | Manifest validation, Lean compilation, and axiom checks. |
| `src/lean1/notify.py` | Local mail and SMTP notifications. |
| `src/lean1/prompt.txt` and `prompt.txt` | Matching copies of the bundled research template. |
| `src/lean1/review_prompt.txt`, `src/lean1/publish_prompt.txt`, `src/lean1/audit_prompt.txt`, `src/lean1/standard_problem.txt` | Review, publication, and audit instructions, and the one-line standard problem template. |
| `tests/lean1/test_<module>.py` | Tests for `src/lean1/<module>.py`; `test_cli.py` holds simulated end-to-end runs. |
| `tests/lean1/support.py` | Simulated Codex, Lean, Lake, and mail executables, and a local stand-in for qiqc-op.com. |
| `docs/` | Configuration and proof-verification guides. |

## Validation

Run the test suite and build both distribution formats:

```sh
uv run python -m unittest discover -s tests -t . -v
uv build
```

The tests use temporary workspaces, simulated Codex/Lean/Lake/mail executables, a local HTTP stand-in for qiqc-op.com, and mocked SMTP and download calls. They do not require credentials, send real email, install Lean, or start paid agent runs. They test orchestration and validation behavior; a live Codex and Lean integration still requires a separately configured environment.

The test tree mirrors `src/`: tests for `src/lean1/x.py` belong in `tests/lean1/test_x.py`. The `-t .` option makes `tests` the top-level package, so the test directory `tests/lean1` cannot shadow the `lean1` package. `LEAN1_LIVE_TESTS=1` adds a check of problem selection against the real qiqc-op.com, and `LEAN1_TEST_CLI=/path/to/lean1` runs the command-line tests against an installed executable.

Keep the top-level and packaged prompt files identical when editing research instructions. The test suite checks their equality and verifies that supplied problem text is substituted literally. Preserve the completion-marker format and verification manifest contract described in the [verification guide](docs/verification.md).

Build output is written to `dist/`. Wheels contain the package and its prompt; source distributions also include the tests, compatibility script, and contributor documentation. Local workspaces, toolchains, credentials, and run logs are excluded from version control.

## Reporting problems and proposing changes

For a reproducible issue, include the lean1 version, Python version, operating system, command used, and relevant error output. Reduce the problem statement to a shareable example when possible, and redact credentials or private research from logs.

Keep pull requests focused on one problem. Describe the resulting behavior and the checks run. Update the README or linked guides when changing command-line options, setup behavior, notifications, or verification rules.
