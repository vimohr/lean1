# lean1

An installable version of `runner.py`: give it a mathematical problem and an email address, and it supervises a Codex agent until the solution passes Lean verification. It preserves the original restart loop, research instructions, completion markers, saved output, and email notifications.

## Install

Requires Python 3.11 or newer, [uv](https://docs.astral.sh/uv/guides/tools/), and an installed, authenticated [Codex CLI](https://learn.chatgpt.com/docs/non-interactive-mode). The runner uses your existing Codex authentication and model configuration.

```sh
uv tool install .
lean1 --help
lean1 setup
```

From another directory, use `uv tool install /path/to/this/repo`. Once the repository is hosted, `uv tool install git+https://github.com/YOUR_USER/YOUR_REPO.git` works too. For editable development, use `uv tool install --editable .`.

`uv tool install` installs a wheel and has no interactive post-install hook. **Lean is checked on the first run, or immediately with `lean1 setup`.** If working local Lean 4 and Lake binaries are absent, lean1 asks before downloading the official Elan installer and a Lean toolchain. Installation defaults to `./.elan` in the directory where you launch `lean1` and leaves shell profiles untouched. `lean1 setup` and subsequent runs launched from that directory reuse this installation, including runs with a separate agent workspace.

Discovery checks the configured local Elan directory, the agent workspace's `.elan`, and Lean/Lake binaries directly in the launch directory or workspace, including their `bin/` subdirectories. Use `--lean-home /path/to/elan` to select another installation explicitly. Inherited `ELAN_HOME`, home-directory installations, and external binaries on PATH do not replace the local check; `ELAN_HOME` is set to the selected local directory for the agent and Lean commands. The check reads the local toolchain selection and uses `elan run` without `--install`, so an uninstalled toolchain is not silently downloaded during the check. See the [Lean toolchain documentation](https://lean-lang.org/doc/reference/latest/Build-Tools-and-Distribution/Managing-Toolchains-with-Elan/).

```sh
# Explicit permission for unattended setup; also accepted on a normal run.
lean1 setup --install-lean

# Choose a local installation location and a toolchain if installation is needed.
lean1 setup --lean-home ./tools/elan --lean-toolchain leanprover/lean4:v4.19.0

# Check only, with no installation prompt or download.
lean1 setup --no-install-lean
```

Automatic installation supports Linux and macOS and requires `sh` and `curl`. On Windows, install Elan manually first. When installing, a workspace's `lean-toolchain` or `ELAN_TOOLCHAIN` is respected; otherwise the default is `leanprover/lean4:stable`. `--lean-toolchain` selects the toolchain **if installation is needed**. Project toolchain files continue to select the version used by Lean.

## Give it a problem

```sh
lean1 --email="you@example.com" --problem="Prove that the sum of two even natural numbers is even."
lean1 --email="you@example.com" --problem="https://example.org/problem"
lean1 --email="you@example.com" --problem="./problem.md"
```

`--problem` recognizes http(s) URLs, existing file paths, and `@path`; otherwise it treats the value as text. Explicit variants remove ambiguity:

```sh
lean1 --email="you@example.com" --problem-text="The problem statement goes here."
lean1 --email="you@example.com" --problem-url="https://example.org/problem"
lean1 --email="you@example.com" --problem-file="./problem.pdf"
cat problem.md | lean1 --email="you@example.com" --problem-file=- --install-lean
```

Text files are included in the prompt and copied into the workspace. Binary documents such as PDFs are copied for the agent to inspect. Websites are downloaded when possible; the original link is always passed to the agent, which is instructed to inspect it for equations, attachments, and other context missing from the saved excerpt. A failed snapshot download falls back to agent browsing.

Each invocation creates `./lean1-runs/<timestamp>-<id>/`. The package does not use the original repository's `workspace/` or `controlled-clifford-hierarchy/`. To work in a prepared repository or continue a previous attempt, set `--workdir`:

```sh
lean1 --email="you@example.com" --problem-file="./problem.md" --workdir="./my-research"
lean1 --email="you@example.com" --problem="Prove ..." --max-runs=5 --notify=complete
```

Use the same problem when continuing a workspace. Existing solution files and notes are preserved, and attempts continue from them. The supervisor uses fresh Codex executions, as the original runner did; previous conversation history is available through the saved messages/files rather than a resumed Codex thread. Avoid running multiple supervisors in the same workspace concurrently.

The command keeps retrying by default. `--max-runs` limits attempts; it never labels an unfinished problem solved. `Ctrl+C` stops the agent and preserves progress. Open problems may remain unresolved indefinitely. This tool automates attempts and verification; it cannot guarantee a mathematical solution.

Other useful options: `--model`, `--restart-delay`, `--verification-timeout`, `--codex-command`, and `--no-network`. Codex retains the `workspace-write` sandbox. Web search and network access are enabled by default so the agent can read links, install project dependencies, and repair Lean. A private Lean installation outside the workspace is added as a writable directory so the agent can manage toolchains. `--no-network` disables agent web search and sandbox network access; Python can still download a supplied website snapshot or install Lean after consent.

## Email

The default remains the original local command: `mail -s SUBJECT RECIPIENT`. Install/configure a mail client and a functioning MTA on your machine, or configure SMTP. Checking that the `mail` executable exists cannot establish whether your MTA actually delivers externally.

SMTP uses Python's standard library. Set these in your shell or secret manager:

```sh
export LEAN1_SMTP_HOST=smtp.example.com
export LEAN1_SMTP_PORT=587
export LEAN1_SMTP_FROM=runner@example.com
export LEAN1_SMTP_USERNAME=runner@example.com
# Set LEAN1_SMTP_PASSWORD through your secret manager or shell environment.
```

With `LEAN1_SMTP_HOST` set, `--email-backend=auto` selects SMTP; otherwise it selects `mail`. `--email-backend=mail` or `smtp` chooses explicitly. `LEAN1_SMTP_SECURITY` accepts `starttls` (default), `ssl` (default port 465), or `plain` for a local relay. SMTP authentication is optional. SMTP credentials are removed from the environments passed to Codex and Lean.

The original behavior of emailing after every attempt is preserved (`--notify=all`). `--notify=complete` sends only after accepted completion. Emails include the final message, verification outcome, runtime, and workspace path. A copy is saved in `.lean1/notifications/` before delivery; failures are logged. SMTP/mail success means submission to the mail server, not confirmation of inbox receipt.

## Prompt and Lean verification

`prompt.txt` preserves the original research workflow, insistence on new proofs/counterexamples, Lean repair instructions, and strict termination conditions. The Clifford-specific research problem has been replaced with `{{PROBLEM}}`, and environment-specific paths are filled in at runtime. The template shipped in the wheel is `src/lean1/prompt.txt`; keep these two copies in sync when editing. A test checks that they match. Use `--prompt-file ./custom-prompt.txt` to supply your own template with exactly one `{{PROBLEM}}` placeholder.

The agent must save `result.md`, its Lean sources, and a `lean_verification.json` manifest in the workspace:

```json
{
  "project_dir": ".",
  "proofs": [
    {"file": "Solution.lean", "theorems": ["Solution.main_result"]}
  ]
}
```

`project_dir` is relative to the workspace, proof files are relative to that directory, and theorem names are fully qualified ASCII identifiers. List the named theorems covering all essential claims. For a Lake project, the supervisor runs `lake build`; it then recompiles each listed source with warnings treated as errors, appending theorem checks and `#print axioms` commands to a temporary copy. Standalone Lean files also work. The copies are removed after checking.

A task is complete only when Codex exits successfully, its final message ends with both original markers, and the Python Lean check succeeds:

```text
TASK_STATUS: COMPLETE
LEAN_VERIFIED: YES
```

The axiom audit permits only `propext`, `Classical.choice`, and `Quot.sound`; it rejects `sorryAx`, custom axioms, and extra trust axioms used by native evaluation. This follows Lean's [proof-validation guidance](https://lean-lang.org/doc/reference/latest/ValidatingProofs/). Failed verification is sent back to the next agent attempt. Checking compilation and axioms cannot automatically establish that a theorem faithfully expresses the requested mathematical problem, or detect all vacuous/overly strong hypotheses; the prompt requires that correspondence to be explained in the report.

Saved artifacts:

- `result.md` and Lean sources: the agent's solution and formal proof.
- `codex_last_message.md`: latest agent response.
- `codex_last_output.json`: compact attempt status and references to logs.
- `codex_runs.log`: supervisor history.
- `.lean1/prompt.txt`: rendered prompt sent to the latest attempt.
- `.lean1/inputs/` and `.lean1/problem.json`: saved problem sources and metadata.
- `.lean1/run-*.events.jsonl`, `run-*.stderr.log`, `run-*.lean.log`: full logs streamed to disk.
- `.lean1/verification_feedback.txt`: feedback for the next attempt.
- `.lean1/notifications/*.eml`: saved notification emails.

Exit codes: `0` verified completion and email submission; `1` attempt limit reached; `2` setup/configuration error; `3` verified completion but email delivery failed; `130` interrupted.

## Development

```sh
uv run python -m unittest discover -s tests -v
uv build
python runner.py --help
```

The compatibility `runner.py` calls the same CLI. Runtime dependencies are all in the Python standard library. Builds include the package and its prompt, plus tests and the compatibility script in the source distribution. Generated research logs and the existing workspace/hierarchy trees are excluded.
