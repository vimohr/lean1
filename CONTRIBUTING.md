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
| `src/lean1/runner.py` | Codex executions, retries, logs, and completion handling. |
| `src/lean1/verification.py` | Manifest validation, Lean compilation, and axiom checks. |
| `src/lean1/notify.py` | Local mail and SMTP notifications. |
| `src/lean1/prompt.txt` and `prompt.txt` | Matching copies of the bundled research template. |
| `tests/test_runner.py` | Unit tests and simulated command-line pipeline tests. |
| `docs/` | Configuration and proof-verification guides. |

## Validation

Run the test suite and build both distribution formats:

```sh
uv run python -m unittest discover -s tests -v
uv build
```

The tests use temporary workspaces, simulated Codex/Lean/Lake/mail executables, and mocked SMTP and download calls. They do not require credentials, send real email, install Lean, or start paid agent runs. They test orchestration and validation behavior; a live Codex and Lean integration still requires a separately configured environment.

To run the tests without installing the package, use `PYTHONPATH=src python -m unittest discover -s tests -v` in a POSIX shell.

Keep the top-level and packaged prompt files identical when editing research instructions. The test suite checks their equality and verifies that supplied problem text is substituted literally. Preserve the completion-marker format and verification manifest contract described in the [verification guide](docs/verification.md).

Build output is written to `dist/`. Wheels contain the package and its prompt; source distributions also include the tests, compatibility script, and contributor documentation. Local workspaces, toolchains, credentials, and run logs are excluded from version control.

## Reporting problems and proposing changes

For a reproducible issue, include the lean1 version, Python version, operating system, command used, and relevant error output. Reduce the problem statement to a shareable example when possible, and redact credentials or private research from logs.

Keep pull requests focused on one problem. Describe the resulting behavior and the checks run. Update the README or linked guides when changing command-line options, setup behavior, notifications, or verification rules.
