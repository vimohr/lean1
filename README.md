# lean1

Run a Codex agent on a mathematical problem, retry incomplete attempts, and check the resulting Lean proofs before accepting completion.

lean1 accepts problem statements as text, URLs, or files. It can also work through random unsolved problems from the [QIQCOP Zoo](https://qiqc-op.com/), with an independent review of each solution, a prepared publication package, and an independent audit of that package for mistakes, attribution, and prior solutions. Each run uses a persistent workspace containing the research report, proof sources, and logs. Email notifications report progress, verified completion, and every issue that stops a run.

[Configuration](docs/configuration.md) · [Proof verification](docs/verification.md) · [Standard problems](docs/standard-problems.md) · [Contributing](CONTRIBUTING.md)

## Requirements

- **Python 3.11 or newer** and [uv](https://docs.astral.sh/uv/getting-started/installation/).
- **Git** for installation from this repository.
- An installed and authenticated **[Codex CLI](https://learn.chatgpt.com/docs/codex/cli)**.
- A working **SMTP service** or a configured local **`mail` command** for notifications.
- **Lean 4 and Lake**. `lean1 setup` can install them locally on Linux and macOS; automatic installation requires `sh` and `curl`. On Windows, prepare an Elan installation manually and select it with `--lean-home`.

lean1 uses the Python standard library and has no third-party Python runtime dependencies. Codex, Lean, and email delivery are configured separately.

## Install

Install directly from GitHub:

```sh
uv tool install git+https://github.com/vimohr/lean1.git
lean1 --help
```

Or install from a local checkout:

```sh
git clone https://github.com/vimohr/lean1.git
cd lean1
uv tool install .
```

If the `lean1` command is unavailable after installation, run `uv tool update-shell` and restart the shell. See the [uv tools guide](https://docs.astral.sh/uv/guides/tools/) for details.

## Quick start

Authenticate Codex with `codex login` and [configure email delivery](#email-notifications). Then check or install Lean and submit a problem:

```sh
lean1 setup
lean1 \
  --email researcher@example.com \
  --problem-text "Prove that the sum of two even natural numbers is even." \
  --max-runs 3 \
  --notify complete
```

`lean1 setup` checks local Lean and Lake binaries and asks before downloading missing tools. The default installation location is `./.elan` in the launch directory. Run setup and research commands from the same directory to reuse it, or select a location explicitly with `--lean-home`.

The example allows three Codex attempts and emails only after verified completion. **Without `--max-runs`, attempts continue until completion, interruption, or a failsafe stop.** Each attempt consumes Codex usage. Codex state is saved in the workspace's `.codex/` directory, seeded with existing authentication and configuration. `lean1 stop` lets the current attempt finish before stopping; `Ctrl+C` interrupts the agent and preserves the workspace.

Before every Codex execution, lean1 runs a short [health check](docs/configuration.md#codex-health-check): Codex must start and give a fixed reply. If it fails, lean1 pauses, emails you, and exits with code `4`; rerun the same command with the same `--workdir` to resume.

A failsafe stops retries if 10 attempts fail within a rolling 60-second window. An attempt counts as failed when Codex exits with an error or produces no final response. Successful attempts that need more research or Lean verification can continue normally. The stop reason is saved in `codex_last_output.json` and the supervisor log.

lean1 automates research attempts and proof checks. Difficult or open problems can remain unresolved, even after many attempts.

## Supply a problem

Choose one input option per invocation:

| Option | Input |
| --- | --- |
| `--problem-text TEXT` | Literal problem statement. |
| `--problem-url URL` | An absolute HTTP or HTTPS link. |
| `--problem-file PATH` | A local text file, PDF, or other document. |
| `--problem-file -` | UTF-8 text from standard input. |
| `--problem VALUE` | Automatically detect a URL or file path, including `@path`; otherwise use text. |

For example:

```sh
lean1 --email researcher@example.com --problem-file ./problem.md --max-runs 3
lean1 --email researcher@example.com --problem-url https://example.org/problem --max-runs 3
lean1 --email researcher@example.com --problem-file - --max-runs 3 < problem.md
```

Use an explicit input option when a statement could be mistaken for a path or URL. Text inputs are included in the agent prompt. Documents are copied into the workspace for inspection. For URLs, lean1 attempts to save a snapshot and also passes the link to the agent, since downloaded excerpts can omit equations, attachments, or other context. A failed download leaves the link available for agent browsing.

## Standard problems from the QIQCOP Zoo

`--standard-problem` replaces the problem input with a campaign over the unsolved problems of [qiqc-op.com](https://qiqc-op.com/):

```sh
lean1 \
  --email researcher@example.com \
  --standard-problem \
  --max-runs 20 \
  --author-name "Ada Lovelace" --author-orcid 0000-0002-1825-0097
```

lean1 picks a random unsolved problem from the site's JSON API, confirms it against the problem's own record and page, and gives the agent a one-line statement:

```text
Solve this problem https://qiqc-op.com/problem/op_f1e5a1cad168b38b/. Make no mistakes.
```

When the Lean proofs pass verification, a new review agent checks whether they resolve the problem exactly as stated on the website. A rejection becomes feedback for the next attempt. An accepted solution is packaged by a further agent as a manuscript in the style of [this Zenodo preprint](https://zenodo.org/records/23215121) and a GitHub-ready repository in the style of [superchannel-amortization](https://github.com/vimohr/superchannel-amortization), committed locally in `release/` and never pushed. An audit agent then searches for prior solutions and checks the package for mistakes and attribution; its defects go back to the publication agent. If the research or the audit agent finds that an existing source already resolves the problem, lean1 records it as solved elsewhere, emails you, and moves on. lean1 then selects the next unsolved problem. `--max-runs` applies per problem; at the limit, lean1 moves on. Any issue, such as a failed health check or an unreachable website, stops the campaign and sends an email. See [Standard problems](docs/standard-problems.md) for the selection checks, stages, layout, and resumption.

## Continue work in a workspace

By default, each invocation creates `./lean1-runs/<timestamp>-<id>/`. Set `--workdir` to use a prepared project or continue an earlier attempt:

```sh
lean1 \
  --email researcher@example.com \
  --problem-file ./problem.md \
  --workdir ./my-research \
  --max-runs 3
```

Reuse the same problem when continuing a workspace. Existing proofs, notes, and logs are preserved, and log numbering continues. Each attempt starts a fresh Codex execution that can inspect saved files and supervisor feedback. Run one supervisor at a time in a given workspace.

## Stop after the current attempt

Run this from the directory where you launched lean1, or from inside its workspace:

```sh
lean1 stop
```

To select a workspace explicitly, including when several runs were launched from the same directory:

```sh
lean1 stop --workdir ./my-research
```

The command acknowledges the request immediately. The supervisor lets the current Codex iteration finish, saves its output, performs any required Lean verification and notifications, and exits before starting another iteration. A request during the restart delay stops further attempts. Resume later by running the original research command with the same `--workdir`.

## Local Codex state

Every Codex attempt uses `CODEX_HOME=<workdir>/.codex`, including when another `CODEX_HOME` is inherited from your shell. Existing workspace credentials and settings are preserved. Missing `auth.json`, `config.toml`, and named `*.config.toml` profile files are copied from the inherited Codex home, or `~/.codex` when none is set. Existing session history and caches are not copied.

To authenticate directly in a workspace, run:

```sh
CODEX_HOME="$PWD/my-research/.codex" codex login
```

Codex's configuration, credentials, and runtime state share this directory; see the [official OpenAI documentation](https://learn.chatgpt.com/docs/config-file/config-advanced#config-and-state-locations). Keep workspace credentials out of version control.

## Email notifications

By default, `--email-backend auto` uses SMTP when `LEAN1_SMTP_HOST` is set; otherwise it runs `mail -s SUBJECT RECIPIENT`.

For SMTP, configure the host and sender in the shell before starting lean1:

```sh
export LEAN1_SMTP_HOST=smtp.example.com
export LEAN1_SMTP_PORT=587
export LEAN1_SMTP_FROM=runner@example.com
export LEAN1_SMTP_USERNAME=runner@example.com
```

For authenticated SMTP, also supply `LEAN1_SMTP_PASSWORD` through the shell environment or a secret manager. SMTP uses STARTTLS by default; authentication is optional for relays that do not require it. See [all SMTP settings](docs/configuration.md#smtp-settings) for SSL and local relay options.

To use local delivery, install and configure a `mail` client and a mail transfer agent. Finding the executable only confirms that the command is available; the mail service must also be able to deliver messages.

`--notify all` sends an email after every attempt and is the default. `--notify complete` sends only after accepted completion. With either setting, lean1 also emails whenever it stops because of an issue: a failed health check, the rapid-failure failsafe, a problem website failure, a failed review, publication, or audit stage, or an unexpected error. Each message includes the final agent response, verification outcome, duration, and workspace path. A copy is saved under `.lean1/notifications/` before delivery. Successful submission to a mail server does not confirm inbox receipt.

## How completion is checked

The agent is instructed to search first for an existing solution, write a research report in `result.md` that opens with this prior-work search and cites every source it uses, save the Lean proof sources, and list the essential theorems in `lean_verification.json`. The mathematics is the contribution; the Lean proof checks the argument.

The supervisor accepts completion only when:

1. Codex exits successfully and its final message ends with the two lines below.
2. The verification manifest identifies valid proof files and named theorems inside the workspace.
3. `lake build` succeeds for a Lake project, and each listed source recompiles with warnings treated as errors.
4. The listed theorems depend only on the permitted axioms: `propext`, `Classical.choice`, and `Quot.sound`.

```text
TASK_STATUS: COMPLETE
LEAN_VERIFIED: YES
```

Failed checks are returned as feedback for the next attempt. Proofs that depend on `sorryAx`, custom axioms, or additional native-evaluation trust axioms are rejected.

Compilation and axiom checks establish properties of the listed formal statements. Human review is still needed to assess whether those statements and their hypotheses faithfully answer the mathematical problem. The [verification guide](docs/verification.md) explains the manifest, checks, and custom prompt format.

## Results and logs

All paths below are relative to the run workspace:

| Path | Contents |
| --- | --- |
| `result.md` and Lean sources | Research report and formal proof produced by the agent. |
| `lean_verification.json` | Proof files and theorem names submitted for verification. |
| `codex_last_message.md` | Latest agent response. |
| `codex_last_output.json` | Latest attempt status, verification outcome, and log paths. |
| `codex_runs.log` | Supervisor history. |
| `.lean1/inputs/` and `.lean1/problem.json` | Saved inputs and source metadata. |
| `.lean1/prompt.txt` | Rendered prompt for the latest attempt. |
| `.lean1/run-*.events.jsonl` | Full Codex event streams. |
| `.lean1/run-*.stderr.log` and `.lean1/run-*.lean.log` | Agent diagnostics and Lean verification logs. |
| `.lean1/verification_feedback.txt` | Feedback carried into the next attempt after an unsuccessful run. |
| `.lean1/notifications/*.eml` | Saved notification messages, including `issue-*.eml` for stops caused by issues. |
| `.lean1/health/` and `.lean1/last_issue.json` | Latest Codex health check logs and the reason for the latest issue stop. |
| `.codex/` | Workspace-local Codex authentication, configuration, sessions, logs, and caches. |

For model selection, retry delays, toolchain discovery, network settings, troubleshooting, and exit codes, see the [configuration reference](docs/configuration.md). Use `lean1 --help` and `lean1 setup --help` for the full command-line interface.

## Development

```sh
uv sync --locked
uv run python -m unittest discover -s tests -t . -v
uv build
```

The tests use simulated Codex, Lean, Lake, and mail executables, a local stand-in for qiqc-op.com, and mocked SMTP delivery. They require no account credentials or live services. Set `LEAN1_LIVE_TESTS=1` to also check problem selection against the real website. See [CONTRIBUTING.md](CONTRIBUTING.md) for the project layout and contribution workflow. Report reproducible problems through [GitHub issues](https://github.com/vimohr/lean1/issues).

## License

This project is licensed under the [MIT License](LICENSE).
