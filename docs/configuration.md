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

A research invocation requires `--email` and exactly one problem input option. The [README](../README.md#supply-a-problem) describes input formats.

| Option | Default | Behavior |
| --- | --- | --- |
| `--workdir PATH` | `./lean1-runs/<timestamp>-<id>` | Directory containing the agent's files and run state. |
| `--max-runs N` | `0` | Maximum attempts in this invocation; `0` retries until completion. |
| `--restart-delay SECONDS` | `3` | Delay between incomplete attempts; zero is allowed. |
| `--verification-timeout SECONDS` | `1800` | Timeout for each Lean or Lake verification command; must be positive. |
| `--model NAME` | Codex configuration | Override the model for agent attempts. |
| `--codex-command PATH` | `codex` | Codex executable name or path. |
| `--prompt-file PATH` | Bundled template | Custom template with exactly one `{{PROBLEM}}` placeholder. See [prompt customization](verification.md#custom-prompts). |
| `--notify all\|complete` | `all` | Notify after every attempt or only accepted completion. |
| `--email-backend auto\|mail\|smtp` | `auto` | Choose the notification backend. |
| `--mail-command PATH` | `mail` | Local mail executable name or path. |
| `--no-network` | Network enabled | Disable Codex web search and network access in its workspace sandbox. |

`--version` reports the installed version. Run `lean1 --help` or `lean1 setup --help` for all options.

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
| `0` | Verified completion with successful email submission, or a successful setup command. |
| `1` | The attempt limit was reached without an accepted solution. |
| `2` | A configuration, setup, or execution error prevented the command from completing. |
| `3` | The solution passed verification, but its completion email could not be delivered. |
| `130` | The command was interrupted. |

## Troubleshooting

| Symptom | Next step |
| --- | --- |
| `lean1` is not found | Run `uv tool update-shell`, restart the shell, and check the installation with `uv tool list`. |
| Codex is not found or cannot authenticate | Install the [Codex CLI](https://learn.chatgpt.com/docs/codex/cli), run `codex login`, and check `--codex-command` if using a custom path. |
| Lean is installed globally but is not detected | Select it explicitly, for example `lean1 setup --lean-home ~/.elan --no-install-lean`. |
| Lean setup fails in a noninteractive session | Install tools beforehand, or permit local installation with `--install-lean`. |
| A project needs a missing toolchain | Check its `lean-toolchain` file and install that version using `lean1 setup --workdir ./my-project --install-lean`. |
| Email delivery fails | Check the chosen backend and SMTP or local mail configuration. Inspect saved `.eml` messages and `notification_error` in `codex_last_output.json`. |
| The agent claims completion but lean1 retries | Read `.lean1/verification_feedback.txt` and the corresponding `.lean1/run-*.lean.log`. See the [verification guide](verification.md). |
| An attempt ends without a solution | Inspect `codex_last_message.md`, `codex_last_output.json`, and `.lean1/run-*.stderr.log`. Continue with the same problem and `--workdir`, or adjust the attempt limit. |
