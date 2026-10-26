"""Parsing of release audits and detection of changes made by the auditor."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from .support import problem_id

from lean1.audit import AuditError, audited_files, changed_files, parse_audit, revision_feedback

PROBLEM_ID = problem_id(42)


class AuditVerdictTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.report = Path(temporary.name) / "audit.json"

    def write(self, **values):
        report = {"problem_id": PROBLEM_ID, "verdict": "pass", "summary": "No defects.", "issues": [], "prior_work": []} | values
        self.report.write_text(json.dumps(report))

    def test_each_verdict_is_parsed(self):
        cases = {
            "pass": {},
            "revise": {"issues": ["Lemma 2 is not cited."]},
            "reject": {"issues": ["The bound in Theorem 1 is wrong."]},
            "solved_elsewhere": {"issues": ["Already published."], "prior_work": ["Doe, J. arXiv:2501.00001."]},
        }
        for verdict, values in cases.items():
            with self.subTest(verdict=verdict):
                self.write(verdict=verdict, **values)
                parsed = parse_audit(f"Done.\nAUDIT_VERDICT: {verdict.upper()}\n", self.report, PROBLEM_ID)
                self.assertEqual(parsed.verdict, verdict)
                self.assertEqual(parsed.issues, values.get("issues", []))

    def test_invalid_audits_are_rejected(self):
        cases = [
            ("no marker", "Looks fine.", {}),
            ("marker not last", "AUDIT_VERDICT: PASS\nBut one more thing.", {}),
            ("disagreement", "AUDIT_VERDICT: REVISE", {}),
            ("other problem", "AUDIT_VERDICT: PASS", {"problem_id": problem_id(7)}),
            ("unknown verdict", "AUDIT_VERDICT: PASS", {"verdict": "maybe"}),
            ("no summary", "AUDIT_VERDICT: PASS", {"summary": ""}),
            ("bad prior work", "AUDIT_VERDICT: PASS", {"prior_work": "none"}),
            ("revise without issues", "AUDIT_VERDICT: REVISE", {"verdict": "revise"}),
            ("solved without source", "AUDIT_VERDICT: SOLVED_ELSEWHERE", {"verdict": "solved_elsewhere", "issues": ["Known."]}),
        ]
        for label, message, values in cases:
            with self.subTest(label=label):
                self.write(**values)
                with self.assertRaises(AuditError):
                    parse_audit(message, self.report, PROBLEM_ID)
        self.report.unlink()
        with self.assertRaisesRegex(AuditError, "did not write"):
            parse_audit("AUDIT_VERDICT: PASS", self.report, PROBLEM_ID)

    def test_revision_feedback_lists_issues_and_sources(self):
        self.write(verdict="revise", issues=["Lemma 2 is due to Doe (2025)."], prior_work=["Doe, J. arXiv:2501.00001."])
        feedback = revision_feedback(parse_audit("AUDIT_VERDICT: REVISE", self.report, PROBLEM_ID))
        self.assertIn("- Lemma 2 is due to Doe (2025).", feedback)
        self.assertIn("arXiv:2501.00001", feedback)
        self.assertIn("do not change the proofs", feedback)


class AuditedFilesTests(unittest.TestCase):
    def test_edits_additions_deletions_and_commits_in_the_release_are_detected(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            release = workspace / "release"
            release.mkdir()
            (workspace / "Solution.lean").write_text("theorem t : True := trivial\n")
            (release / "README.md").write_text("# Result\n")
            git = ["git", "-C", str(release), "-c", "user.name=T", "-c", "user.email=t@example.org"]
            subprocess.run(git + ["init", "-q"], check=True)
            subprocess.run(git + ["add", "-A"], check=True)
            subprocess.run(git + ["commit", "-q", "-m", "feat: initial release"], check=True)
            before = audited_files(workspace)
            (workspace / "notes.txt").write_text("scratch outside the release is allowed\n")
            self.assertEqual(changed_files(before, audited_files(workspace)), [])
            (release / "extra.md").write_text("added\n")
            self.assertEqual(changed_files(before, audited_files(workspace)), ["release/extra.md"])
            (release / "extra.md").unlink()
            (release / "README.md").write_text("# Edited\n")
            (workspace / "Solution.lean").unlink()
            self.assertEqual(changed_files(before, audited_files(workspace)), ["Solution.lean", "release/README.md"])
            (release / "README.md").write_text("# Result\n")
            (workspace / "Solution.lean").write_text("theorem t : True := trivial\n")
            subprocess.run(git + ["commit", "-q", "--allow-empty", "-m", "chore: amend"], check=True)
            self.assertEqual(changed_files(before, audited_files(workspace)), ["release commit"])


if __name__ == "__main__":
    unittest.main()
