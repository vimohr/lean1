"""Manifest validation and axiom audits."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from .support import REPO  # noqa: F401

from lean1.common import RunnerError
from lean1.lean import LeanTools
from lean1.verification import check_axioms, verify


class VerificationTests(unittest.TestCase):
    def test_axiom_audit_accepts_standard_axioms_and_rejects_hidden_dependencies(self):
        check_axioms("'S.result' depends on axioms: [propext,\n Classical.choice, Quot.sound]", ["S.result"])
        check_axioms("'S.result' does not depend on any axioms", ["S.result"])
        for output in (
            "'S.result' depends on axioms: [sorryAx]",
            "'S.result' depends on axioms: [fakeAssumption]",
            "'S.result' depends on axioms: [Lean.trustCompiler]",
            "'S.other' depends on axioms: [propext]",
        ):
            with self.subTest(output=output), self.assertRaises(RunnerError):
                check_axioms(output, ["S.result"])

    def test_manifest_cannot_escape_workspace_or_inject_lean_commands(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "Solution.lean").write_text("theorem solution : True := by trivial\n")
            tools = LeanTools("lean", "lake", None, {}, "Lean 4")
            cases = [
                {"project_dir": "..", "proofs": [{"file": "Solution.lean", "theorems": ["solution"]}]},
                {"proofs": [{"file": "../Solution.lean", "theorems": ["solution"]}]},
                {"proofs": [{"file": "Solution.lean", "theorems": ["solution\n#eval dangerous"]}]},
                {"proofs": []},
            ]
            for manifest in cases:
                (root / "lean_verification.json").write_text(json.dumps(manifest))
                with patch("lean1.verification.subprocess.run") as run:
                    self.assertFalse(verify(root, tools, 1, 10).success)
                run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
