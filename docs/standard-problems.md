# Standard problems from the QIQCOP Zoo

[Back to README](../README.md)

`--standard-problem` runs a campaign over the unsolved problems of the [Quantum Information and Quantum Computation Open Problem Zoo](https://qiqc-op.com/). lean1 selects a problem at random, works on it until a solution is verified and accepted or the attempt limit is reached, and then continues with another unsolved problem.

```sh
lean1 \
  --email researcher@example.com \
  --standard-problem \
  --max-runs 20 \
  --author-name "Ada Lovelace" \
  --author-affiliation "Example University" \
  --author-orcid 0000-0002-1825-0097
```

The campaign continues until an issue occurs, `lean1 stop` is requested, or `--max-problems` problems have been processed. Each issue stops the campaign and sends an email, whatever the `--notify` setting.

## Problem selection

lean1 reads the site's machine-readable index, `https://qiqc-op.com/api/index.json`, and chooses uniformly among problems whose status is `Unsolved` and that this campaign has not attempted before. Before work starts, it confirms the choice in two further documents:

1. The problem's own record, `api/problems/<id>.json`, must name the same problem and also report `Unsolved`. A disagreement can occur while the site is being updated; lean1 then chooses another problem.
2. The problem page, `problem/<id>/`, must load and mention the problem ID.

The index must be valid JSON with well-formed problem IDs (`op_` followed by 16 hexadecimal digits), a status for every problem, and an unsolved count that matches its own list. Network errors and server errors are retried four times with increasing delays of 2, 4, and 8 seconds. Missing documents (HTTP 404) are not retried. If no valid problem can be confirmed, the campaign stops with exit code `5` and sends an email.

Selection does not use the site's `/random/` pages, which its `robots.txt` excludes.

## The problem statement

Each problem workspace receives a one-line statement in `.lean1/inputs/standard_problem.txt`, rendered from the bundled template [`src/lean1/standard_problem.txt`](../src/lean1/standard_problem.txt):

```text
Solve this problem https://qiqc-op.com/problem/op_f1e5a1cad168b38b/. Make no mistakes.
```

This line replaces `{{PROBLEM}}` in the research prompt ([`prompt.txt`](../prompt.txt), or `--prompt-file`). The agent opens the page itself. The website record is also saved as `.lean1/inputs/qiqcop-record.json` for the review and publication agents.

## Stages for each problem

1. **Research.** The normal lean1 loop: Codex attempts, Lean verification, and feedback. The agent first searches for an existing solution and records the outcome at the top of `result.md` as `Prior resolution: NONE FOUND`, `PARTIAL`, or `COMPLETE`. After any attempt that reports `COMPLETE`, research stops, the problem is marked `solved_elsewhere`, and an email quotes the cited sources; no review or publication follows. `--max-runs` limits the attempts per problem. When the limit is reached without an accepted solution, the problem is marked `exhausted` and the next problem is selected. With the default `--max-runs 0`, lean1 keeps working on one problem until it is solved.
2. **Independent review.** After the supervisor verifies the Lean proofs, a fresh Codex execution with [`review_prompt.txt`](../src/lean1/review_prompt.txt) checks whether the verified theorems answer the problem exactly as stated on the website. It checks definitions, quantifiers, hypotheses, scope, the informal steps in `result.md`, and prior work listed on the site. It must write `.lean1/review/review-<run>-<attempt>.json` and end with `REVIEW_VERDICT: ACCEPT` or `REVIEW_VERDICT: REJECT`; the two must agree.
   - A rejection is not an issue. Its listed defects become feedback for the next research attempt.
   - Solution files (`*.lean`, `result.md`, `lean_verification.json`, and Lake configuration) are hashed before and after the review. If the reviewer changes or deletes one, the review is invalid and the campaign stops.
   - An execution without a valid verdict is retried once, then the campaign stops.
3. **Publication package.** After acceptance, a fresh Codex execution with [`publish_prompt.txt`](../src/lean1/publish_prompt.txt) writes `release/` in the problem workspace, modeled on the [Zenodo preprint](https://zenodo.org/records/23215121) and the [superchannel-amortization repository](https://github.com/vimohr/superchannel-amortization). The supervisor then checks the package. It requires the manuscript (`manuscript.tex` and a real `manuscript.pdf`), README, verification guides, provenance, citation, and Zenodo metadata, plus the Lean artifact with its toolchain, a `verify_artifact.py` result recording `PASS`, and a clean Git commit. The package must have no remote, no committed build output, and no Codex or lean1 state. Defects are returned to the agent for one more attempt; after that, the campaign stops.
4. **Release audit.** A fresh Codex execution with [`audit_prompt.txt`](../src/lean1/audit_prompt.txt) audits the package. It runs its own search for prior solutions, checks that every borrowed definition, result, and technique is cited and that every reference is real and correct, checks that the manuscript focuses on the new mathematical result, with the Lean verification as support rather than the main topic, and looks for mistakes in the statement, proof, and documentation. It writes `.lean1/audit/audit-<round>-<attempt>.json` and ends with a matching `AUDIT_VERDICT` line:
   - `PASS`: the problem is marked `published`.
   - `REVISE`: the defects go back to the publication agent, which updates `release/`, and the package is audited again. After three rounds with defects, the campaign stops.
   - `REJECT`: the result itself is wrong or does not answer the problem. The problem is marked `audit_rejected` and an email is sent.
   - `SOLVED_ELSEWHERE`: an existing source resolves the problem. The problem is marked `solved_elsewhere` and an email is sent.
   - The auditor must not change the package or the solution; the supervisor hashes both, including the release commit. An execution without a valid verdict is retried once, then the campaign stops.

The release repository is only committed locally. Nothing is pushed to GitHub or uploaded to Zenodo. Inspect `manuscript.pdf`, `HUMAN-CHECK.md`, and `VERIFY.md` before publishing it yourself. Author details come from `--author-name`, `--author-affiliation`, and `--author-orcid`; without them, the package uses the placeholder `AUTHOR NAME` and lists it as a required edit.

## Health checks

Before every Codex execution, including reviews and publication runs, lean1 runs the [health check](configuration.md#codex-health-check). A failed check pauses the campaign at that point and sends an email.

## Campaign layout and resumption

```text
<campaign>/
  campaign.json              Problem history and status
  codex_runs.log             Campaign log
  .lean1/notifications/      Issue and completion emails
  problems/<id>/             One research workspace per problem
    .lean1/inputs/           standard_problem.txt and qiqcop-record.json
    .lean1/review/           Review prompts, logs, and verdicts
    .lean1/publication/      Publication prompts and logs
    .lean1/audit/            Audit prompts, logs, and verdicts
    release/                 Publication package (Git repository)
```

Problem statuses are `researching`, `accepted` (review passed; packaging or audit pending), `published` (audit passed), `exhausted`, `solved_elsewhere`, and `audit_rejected`. Problems with any status are never selected again by the same campaign.

To resume after an issue or a stop, run the same command with `--workdir <campaign>`. lean1 continues the active problem: research resumes in its workspace, and an accepted problem resumes at the publication stage. The default campaign directory is `./lean1-runs/<timestamp>-standard-<id>/`.

`lean1 stop` stops the campaign after the current attempt, whether it is run in the launch directory, the campaign directory, or a problem workspace.

## Emails

| Event | Sent with `--notify all` | Sent with `--notify complete` |
| --- | --- | --- |
| Problem selected | Yes | No |
| Research attempt finished | Yes | Only with a verified, accepted solution |
| Publication package passed the audit | Yes | Yes |
| Problem already solved elsewhere | Yes | Yes |
| Audit rejected the solution | Yes | Yes |
| Any issue that stops the campaign | Yes | Yes |

Issues are failed health checks, website failures, the rapid-failure failsafe, review, publication, or audit failures, and unexpected errors.

## Another source or a mirror

Set `LEAN1_QIQCOP_SITE` to use a mirror with the same API layout, for example `LEAN1_QIQCOP_SITE=https://mirror.example.org/zoo/`. The test suite uses this to run campaigns against a local server.
