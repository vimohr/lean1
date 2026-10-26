# Proof verification

[Back to README](../README.md)

lean1 checks the Lean sources and theorem dependencies declared by the agent. These checks support review of the formal proof; the mathematical statement, hypotheses, and correspondence to the requested problem also need review.

## Required artifacts

The agent is instructed to leave these files in the workspace:

- `result.md`: a "Prior work" section with the line `Prior resolution: NONE FOUND`, `PARTIAL`, or `COMPLETE`, followed by the mathematical argument, with every borrowed result cited, and its correspondence to named Lean theorems.
- Lean sources and any project files needed to reproduce the proof.
- `lean_verification.json`: the manifest used by the supervisor.

The manifest has this form:

```json
{
  "project_dir": ".",
  "proofs": [
    {
      "file": "Solution.lean",
      "theorems": ["Solution.main_result"]
    }
  ]
}
```

`project_dir` is relative to the workspace. Each proof file is relative to that project directory. Paths must stay within their respective roots, including after resolving symlinks. The manifest must list at least one existing `.lean` source, with at least one fully qualified ASCII theorem name per source.

For a Lake project in `formal/`, set `"project_dir": "formal"` and list proof files relative to `formal/`. Include every theorem needed to cover the essential mathematical claims. Each source should be syntactically complete, with namespaces and sections closed so that additional checks can be appended.

## Verification sequence

Verification starts when Codex exits with code `0` and its final message ends with these exact lines:

```text
TASK_STATUS: COMPLETE
LEAN_VERIFIED: YES
```

The supervisor then:

1. Validates the manifest, source paths, and theorem names.
2. Runs `lake build` when the selected project contains `lakefile.lean` or `lakefile.toml`.
3. Creates a temporary copy beside each listed source, appending `#check` and `#print axioms` commands for its listed theorems.
4. Compiles each copy with `-DwarningAsError=true`, using `lake env` for Lake projects.
5. Checks the reported transitive axiom dependencies of every listed theorem.

The temporary copies are removed after checking. Standalone sources work without a Lake project. `--verification-timeout` applies to each individual build or compiler command.

Only these axioms are permitted:

- `propext`
- `Classical.choice`
- `Quot.sound`

Theorems with no axiom dependencies are also accepted. Dependencies on `sorryAx`, custom axioms, or additional axioms introduced by native evaluation fail the check. See Lean's [proof-validation guidance](https://lean-lang.org/doc/reference/latest/ValidatingProofs/) for the significance of axiom audits.

Verification output is saved in `.lean1/run-<number>.lean.log`. A failed check produces `.lean1/verification_feedback.txt`, which is included in the next attempt's prompt. Completion markers alone cannot bypass verification.

## Scope of the checks

The supervisor compiles the sources and checks the dependencies of the names supplied in the manifest. It does not infer which mathematical claims are essential, or establish that a formal statement faithfully models the input problem. A theorem can compile while expressing a weaker claim or using assumptions that make the result vacuous.

The bundled prompt requires the agent to explain the correspondence in `result.md` and to avoid weakening the problem, assuming its conclusion, or substituting unrelated theorems. Review that explanation alongside the formal statements when assessing a result.

## Independent review of standard problems

With `--standard-problem`, a verified solution is not yet accepted. A separate Codex execution acts as a referee and decides whether the verified theorems answer the problem exactly as stated on qiqc-op.com: the same objects, quantifiers, conventions, and scope, with faithful definitions and no extra hypotheses. A rejection returns the referee's issues to the research agent as feedback. The referee must not edit solution files; the supervisor compares their hashes before and after the review. See [Standard problems](standard-problems.md#stages-for-each-problem).

The review is an automated check by another agent. It reduces, but does not replace, the human review described above.

## Custom prompts

The bundled research instructions are in [`src/lean1/prompt.txt`](../src/lean1/prompt.txt), with an identical top-level [`prompt.txt`](../prompt.txt) for convenient inspection and editing.

To use a different template:

```sh
lean1 \
  --email researcher@example.com \
  --problem-file ./problem.md \
  --prompt-file ./custom-prompt.txt \
  --max-runs 3
```

A template must contain exactly one `{{PROBLEM}}` placeholder. The renderer also substitutes these optional placeholders:

| Placeholder | Replacement |
| --- | --- |
| `{{LEAN_COMMAND}}` | Selected Lean executable path. |
| `{{LAKE_COMMAND}}` | Selected Lake executable path. |
| `{{ELAN_COMMAND}}` | Selected Elan executable path, or a message when Elan is unavailable. |

Substitution is performed once, so braces and placeholder-like text in a supplied problem remain literal. The supervisor appends continuation guidance and any saved verification feedback after rendering.

Custom templates should explain the report, proof manifest, and completion markers. The supervisor enforces the same completion checks regardless of the template used.
