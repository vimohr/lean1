# Configuration reference

[Back to README](../README.md)

## Lean setup

Lean is checked by `lean1 setup` and before each research invocation. Installing the Python package does not download Lean.

```sh
lean1 setup
lean1 setup --install-lean
lean1 setup --no-install-lean
```

The first command asks before installing missing tools when run interactively. `--install-lean` permits installation without a prompt. `--no-install-lean` fails if working Lean 4 and Lake binaries cannot be found. Noninteractive runs need `--install-lean` if installation is required. These flags also work on research invocations.

The default Elan directory is `./.elan` relative to the directory where lean1 is launched. Shell profiles are left untouched. Select another installation with `--lean-home`:

```sh
lean1 setup --lean-home ./tools/elan --install-lean
lean1 setup --lean-home ~/.elan --no-install-lean
```

Use the same `--lean-home` on subsequent invocations if that directory differs from the default. `lean1 setup --workdir ./my-project` checks the toolchain for an existing project without starting an agent or sending email.

Discovery checks the selected Elan directory, the workspace's `.elan`, and Lean/Lake binaries in the launch directory or workspace, including their `bin/` subdirectories. Home-directory installations and binaries elsewhere on `PATH` are used only when selected through one of these locations. Inherited `ELAN_HOME` does not replace this local discovery; the subprocess environment uses the selected installation.

Discovery reads the local toolchain selection and uses `elan run` without `--install`. A check therefore does not silently download an absent project toolchain. See [Elan toolchain management](https://lean-lang.org/doc/reference/latest/Build-Tools-and-Distribution/Managing-Toolchains-with-Elan/).

When installation is needed, the requested toolchain is selected in this order:

1. `--lean-toolchain`.
2. `ELAN_TOOLCHAIN`.
3. The nearest `lean-toolchain` file in the workspace or its ancestors.
4. `leanprover/lean4:stable`.

For a reproducible project, keep a `lean-toolchain` file with a pinned version. `--lean-toolchain` chooses what to install when tools are missing; project files and Elan selection still determine the version used by commands. An existing working installation is reused.

Automatic installation supports Linux and macOS and requires `sh` and `curl`. On Windows, prepare Elan and its toolchain manually, then pass `--lean-home` if needed.

## Research options

A research invocation requires `--email` and exactly one problem input option. The [README](../README.md#supply-a-problem) describes input formats. `--standard-problem` instead runs a campaign over unsolved problems from qiqc-op.com; see [Standard problems](standard-problems.md).

| Option | Default | Behavior |
| --- | --- | --- |
| `--workdir PATH` | `./lean1-runs/<timestamp>-<id>` | Directory containing the agent's files and run state. |
| `--max-runs N` | `0` | Maximum attempts in this invocation, or per problem with `--standard-problem`; `0` retries until completion or the failsafe stops retries. |
| `--max-problems N` | `0` | With `--standard-problem`, stop after `N` problems; `0` continues until an issue or a stop request. |
| `--restart-delay SECONDS` | `3` | Delay between incomplete attempts; zero is allowed. |
| `--verification-timeout SECONDS` | `1800` | Timeout for each Lean or Lake verification command; must be positive. |
| `--model NAME` | Codex configuration | Override the model for agent attempts. |
| `--codex-command PATH` | `codex` | Codex executable name or path. |
| `--prompt-file PATH` | Bundled template | Custom template with exactly one `{{PROBLEM}}` placeholder. See [prompt customization](verification.md#custom-prompts). |
| `--notify all\|complete` | `all` | Notify after every attempt or only accepted completion. |
| `--email-backend auto\|mail\|smtp` | `auto` | Choose the notification backend. |
| `--mail-command PATH` | `mail` | Local mail executable name or path. |
| `--no-network` | Network enabled | Disable Codex web search and network access in its workspace sandbox. Not allowed with `--standard-problem`. |
| `--no-health-check` | Enabled | Skip the [Codex health check](#codex-health-check). |
| `--health-check-timeout SECONDS` | `300` | Time allowed for one health check. |
| `--health-check-retries N` | `1` | Further health checks, 30 seconds apart, before pausing. |
| `--author-name`, `--author-affiliation`, `--author-orcid` | Unset | Author metadata for [publication packages](standard-problems.md#stages-for-each-problem). |

`--version` reports the installed version. Run `lean1 --help`, `lean1 setup --help`, or `lean1 stop --help` for command options.

Retries stop automatically after 10 failed Codex attempts within a rolling 60-second window, even with `--max-runs 0`. A failure means a nonzero exit code or no nonempty final agent response; incomplete research and failed Lean verification alone do not count. The window includes each attempt's runtime and the delays between attempts. Older failures expire, and a new supervisor invocation starts with a fresh count.

The failsafe exits with code `1`, preserves attempt logs and results, and records `stop_reason` in `codex_last_output.json` and `codex_runs.log`. The final email reports that retries have stopped and includes the reason. It is sent with either `--notify` setting, because the failsafe is an issue. Inspect `.lean1/run-*.stderr.log` and fix any startup, authentication, or configuration errors before running again with the same workspace.

## Codex health check

Before every Codex execution (each research attempt and, with `--standard-problem`, each review, publication, and audit run), lean1 starts Codex with a minimal prompt that asks for one fixed reply. The check uses the same executable, model, sandbox, network setting, and workspace `CODEX_HOME` as the real execution. It runs in `<workdir>/.lean1/health/`, so the solution files are untouched.

A check fails if Codex cannot be started, exits with an error, exceeds `--health-check-timeout`, or gives a different reply. Authentication problems, exhausted usage limits, unavailable models, and network failures are therefore detected before a long attempt starts. A failed check is repeated `--health-check-retries` times, 30 seconds apart. If every check fails, lean1 pauses:

- No further Codex execution starts.
- An email is sent whatever the `--notify` setting. Its subject starts with `lean1 stopped:`, and its body contains Codex's error messages, its stderr, and how to resume.
- The reason is saved in `.lean1/last_issue.json`, and the check's logs in `.lean1/health/`.
- lean1 exits with code `4`.

Fix the cause, then run the same command with the same `--workdir` to resume. Each check costs one short Codex request. Disable checks with `--no-health-check`.

## Issue emails

lean1 always sends an email when it stops because of an issue, also with `--notify complete`. Issues are a failed health check, the rapid-failure failsafe, problem website failures, review, publication, or audit failures, and unexpected supervisor errors. Graceful stops, `Ctrl+C`, and reaching `--max-runs` or `--max-problems` are not issues. Issue emails are saved as `.lean1/notifications/issue-*.eml` before delivery.

## Graceful stopping

`lean1 stop` requests a stop after the current Codex iteration, Lean verification, and notification delivery finish. It returns immediately and requires no problem, email, Codex, or Lean options. Use `lean1 stop --workdir PATH` to select a workspace explicitly.

Without `--workdir`, lean1 first looks for an active supervisor in the current directory or a parent workspace. Otherwise, it selects the only active run registered in the current launch directory's `.lean1/active-runs/`. If several runs are active there, the command lists their workspaces and requires an explicit selection. If no active run is found, it exits with code `2`.

Active supervisors record their identity in `<workdir>/.lean1/active_run.json`. A stop request is stored in `<workdir>/.lean1/stop_requested` and applies only to that supervisor invocation. These files and the launch-directory registration are removed when the supervisor exits. A later invocation in the workspace starts normally. Requests during the restart delay prevent another Codex attempt from starting.

The stop command exits with code `0` when the request is recorded. The supervisor exits with code `130` if stopped without a verified solution. A solution verified during the final iteration retains the normal completion exit code (`0`, or `3` if completion email delivery fails). `Ctrl+C` still interrupts the active agent immediately.

## Codex home

lean1 sets the Codex subprocess's `CODEX_HOME` to the absolute path `<workdir>/.codex`. Each workspace therefore keeps its own Codex sessions, logs, caches, credentials, and configuration. Continuing with the same `--workdir` reuses that directory.

Missing `auth.json`, `config.toml`, and named `*.config.toml` profile files are seeded from the inherited `CODEX_HOME`, or `~/.codex` when the variable is unset. That source is read only; existing files in the workspace's `.codex/` are never overwritten. New directories and seeded files are private to the current user on POSIX systems. Other files, including prior session history, skills, and caches, are not copied.

To log in or adjust the workspace's configuration directly, give Codex the same home:

```sh
CODEX_HOME="$PWD/my-research/.codex" codex login
```

The [official OpenAI configuration guide](https://learn.chatgpt.com/docs/config-file/config-advanced#config-and-state-locations) describes the state stored under `CODEX_HOME`. Keep this directory out of version control because it can contain credentials.

## Agent permissions and network access

Codex runs in the `workspace-write` sandbox with command approval prompts disabled. Web search and sandbox network access are enabled by default so the agent can inspect problem sources, install dependencies, and manage Lean tooling. An Elan installation outside the workspace is added as a writable directory when needed.

`--no-network` disables agent web search and sandbox network access. It does not disable Python's attempt to download a supplied URL snapshot, or Lean installation permitted during setup. Use a local problem input and `--no-install-lean` when those downloads are also unwanted.

## SMTP settings

Set these environment variables before starting lean1. It reads the process environment and does not automatically load `.env` files.

| Variable | Default | Purpose |
| --- | --- | --- |
| `LEAN1_SMTP_HOST` | Unset | SMTP server. Setting it makes `--email-backend auto` select SMTP. |
| `LEAN1_SMTP_PORT` | `587`, or `465` for SSL | SMTP port, from 1 to 65535. |
| `LEAN1_SMTP_FROM` | Username, if it is an email address | Sender address. Required if the username cannot serve as the sender. |
| `LEAN1_SMTP_USERNAME` | Unset | Authentication username; omit for an unauthenticated relay. |
| `LEAN1_SMTP_PASSWORD` | Unset | Authentication password. |
| `LEAN1_SMTP_SECURITY` | `starttls` | `starttls`, `ssl`, or `plain`. Use `plain` only for a suitable local relay. |

The sender and recipient must each be a single plain email address, without a display name or recipient list. `--email-backend smtp` requires `LEAN1_SMTP_HOST`. `--email-backend mail` selects the local command even if SMTP settings are present.

lean1 removes `LEAN1_SMTP_*` variables from environments passed to Codex, Lean, Lake, and the Elan installer. Notification messages are saved before submission, so a failed delivery can be inspected in `.lean1/notifications/`.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Verified completion with successful email submission, a successful setup command, or a recorded stop request. |
| `1` | The attempt limit or the rapid-failure failsafe stopped retries without an accepted solution. |
| `2` | A configuration, setup, or execution error prevented the command from completing. |
| `3` | The solution passed verification, but its completion email could not be delivered. |
| `4` | The Codex health check failed; lean1 paused and sent an email. |
| `5` | No valid unsolved problem could be obtained from the problem website. |
| `6` | The review, publication, or audit agent failed to produce a valid result, the auditor changed files, or the package still had defects after three audit rounds. |
| `130` | The research command was interrupted, or gracefully stopped without a verified solution. |

## Troubleshooting

| Symptom | Next step |
| --- | --- |
| `lean1` is not found | Run `uv tool update-shell`, restart the shell, and check the installation with `uv tool list`. |
| Codex is not found or cannot authenticate | Install the [Codex CLI](https://learn.chatgpt.com/docs/codex/cli), check `--codex-command` if using a custom path, and authenticate with `CODEX_HOME="$PWD/my-research/.codex" codex login` for an existing workspace. |
| Lean is installed globally but is not detected | Select it explicitly, for example `lean1 setup --lean-home ~/.elan --no-install-lean`. |
| Lean setup fails in a noninteractive session | Install tools beforehand, or permit local installation with `--install-lean`. |
| A project needs a missing toolchain | Check its `lean-toolchain` file and install that version using `lean1 setup --workdir ./my-project --install-lean`. |
| Email delivery fails | Check the chosen backend and SMTP or local mail configuration. Inspect saved `.eml` messages and `notification_error` in `codex_last_output.json`. |
| lean1 paused after a failed health check | Read the issue email or `.lean1/last_issue.json` and `.lean1/health/check.stderr.log`. Common causes are an expired login, an exhausted usage limit, or a model name that is unavailable for the account. Rerun with the same `--workdir` once Codex works. |
| A standard-problem campaign stops with exit code `5` | Check that `https://qiqc-op.com/api/index.json` loads. If every unsolved problem has been attempted, start a new campaign directory. |
| The agent claims completion but lean1 retries | Read `.lean1/verification_feedback.txt` and the corresponding `.lean1/run-*.lean.log`. See the [verification guide](verification.md). |
| An attempt ends without a solution | Inspect `codex_last_message.md`, `codex_last_output.json`, and `.lean1/run-*.stderr.log`. Continue with the same problem and `--workdir`, or adjust the attempt limit. |
