"""The standard-problem campaign: selection, progression, resumption, and issues."""

import contextlib
import io
import json
import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from .support import FakeZoo, problem_id

from lean1.audit import AuditVerdict
from lean1.campaign import AUDIT_ROUNDS, CampaignConfig, CampaignState, prepare_workspace, run_campaign
from lean1.common import RunnerError, write_json
from lean1.config import RunConfig
from lean1.control import request_stop
from lean1.issues import EXIT_AGENT_STAGE, EXIT_HEALTH_CHECK, EXIT_PROBLEM_SOURCE, EXIT_STOPPED
from lean1.lean import LeanTools
from lean1.notify import EmailConfig
from lean1.publication import PublicationError
from lean1.qiqcop import ZooClient, ZooProblem
from lean1.runner import Outcome


SOLVED = "## Prior work\n\nPrior resolution: COMPLETE\n\nDoe, J. A full answer. arXiv:2501.00001.\n"


class StateTests(unittest.TestCase):
    def test_state_round_trip_and_active_problem(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "campaign.json"
            state = CampaignState(path)
            first = state.add(ZooProblem(problem_id(1), "One", "Unsolved"), Path(temporary) / "one")
            state.update(first, status="exhausted")
            second = state.add(ZooProblem(problem_id(2), "Two", "Unsolved"), Path(temporary) / "two")
            reloaded = CampaignState(path)
            self.assertEqual(reloaded.attempted(), {problem_id(1), problem_id(2)})
            self.assertEqual(reloaded.active()["id"], second["id"])
            state.update(second, status="published")
            self.assertIsNone(CampaignState(path).active())

    def test_corrupt_state_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "campaign.json"
            for content in ("{broken", json.dumps({"problems": "none"})):
                path.write_text(content)
                with self.subTest(content=content), self.assertRaises(RunnerError):
                    CampaignState(path)
                self.assertEqual(path.read_text(), content)

    def test_workspace_receives_exact_problem_text_and_record(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            problem = ZooProblem("op_f1e5a1cad168b38b", "Lockability", "Unsolved")
            text = prepare_workspace(problem, {"id": problem.id, "status": "Unsolved"}, workspace)
            expected = "Solve this problem https://qiqc-op.com/problem/op_f1e5a1cad168b38b/. Make no mistakes."
            self.assertEqual(text, expected)
            self.assertEqual((workspace / ".lean1/inputs/standard_problem.txt").read_text(), expected + "\n")
            self.assertEqual(json.loads((workspace / ".lean1/inputs/qiqcop-record.json").read_text())["id"], problem.id)
            details = json.loads((workspace / ".lean1/problem.json").read_text())
            self.assertEqual(details["kind"], "standard")
            self.assertEqual(len(details["record_sha256"]), 64)


class CampaignTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="lean1-campaign-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.enterContext(contextlib.chdir(self.root))
        self.ids = [problem_id(number) for number in range(1, 5)]
        self.zoo = FakeZoo({identifier: "Unsolved" for identifier in self.ids} | {problem_id(99): "Solved"}).__enter__()
        self.addCleanup(self.zoo.__exit__)
        self.campaign_root = self.root / "campaign"
        self.run = RunConfig(
            self.root / "unused", "", LeanTools("lean", "lake", None, {}, "Lean 4"),
            EmailConfig("me@example.org", "mail"), notify="complete", max_runs=2,
        )
        self.emails: list[tuple[str, str]] = []

    def campaign(self, outcomes, *, max_problems=0, publish=None, client=None, audits=()):
        """Run with simulated research outcomes and audit verdicts (default: pass).

        Return the exit code and the research configurations used.
        """
        outcomes = iter(outcomes)
        audits = iter(audits)
        self.feedback: list[str] = []
        runs: list[RunConfig] = []

        def supervise(config):
            runs.append(config)
            outcome = next(outcomes)
            if outcome.status == "complete":
                write_json(config.workdir / "codex_last_output.json", {"acceptance": {"report": str(config.workdir / "review.json")}})
            if outcome.status == "solved_elsewhere":
                (config.workdir / "result.md").write_text(SOLVED)
            return outcome

        def released(config, **arguments):
            self.feedback.append(arguments["feedback"])
            return config.workdir / "release"

        def audit(config, **arguments):
            verdict = next(audits, "pass")
            issues = [] if verdict == "pass" else ["Lemma 2 is due to Doe (2025) but is not cited."]
            prior = ["Doe, J. A full answer. arXiv:2501.00001."] if verdict == "solved_elsewhere" else []
            return AuditVerdict(verdict, "Audited.", issues, prior, config.workdir / f"audit-{arguments['round_number']}.json")

        def send(workdir, name, subject, body):
            self.emails.append((subject, body))

        config = CampaignConfig(
            self.campaign_root, self.run, max_problems=max_problems,
            client=client or ZooClient(self.zoo.site, sleep=lambda _: None), rng=random.Random(3),
        )
        with patch("lean1.campaign.supervise_outcome", side_effect=supervise), patch("lean1.campaign.publish_solution", side_effect=publish or released), patch("lean1.campaign.audit_release", side_effect=audit), patch.object(EmailConfig, "send_named", side_effect=send), contextlib.redirect_stdout(io.StringIO()):
            code = run_campaign(config)
        return code, runs

    def state(self):
        return json.loads((self.campaign_root / "campaign.json").read_text())["problems"]

    def test_solved_problems_are_published_and_the_next_problem_follows(self):
        code, runs = self.campaign([Outcome(0, "complete"), Outcome(0, "complete")], max_problems=2)
        self.assertEqual(code, 0)
        problems = self.state()
        self.assertEqual([entry["status"] for entry in problems], ["published", "published"])
        self.assertEqual(len({entry["id"] for entry in problems}), 2)
        self.assertTrue({entry["id"] for entry in problems} <= set(self.ids))
        for run, entry in zip(runs, problems):
            self.assertEqual(run.problem, f"Solve this problem {self.zoo.site}problem/{entry['id']}/. Make no mistakes.")
            self.assertEqual(run.workdir, self.campaign_root / "problems" / entry["id"])
            self.assertEqual(run.label, entry["id"])
            self.assertIsNotNone(run.acceptance)
            self.assertIsNotNone(run.external_stop)
            self.assertEqual(entry["release"], str(run.workdir / "release"))
        packaged = [subject for subject, _ in self.emails if "Solved and packaged" in subject]
        self.assertEqual(len(packaged), 2)

    def test_exhausted_problem_moves_on_without_repeating_it(self):
        code, runs = self.campaign([Outcome(1, "limit"), Outcome(0, "complete")], max_problems=2)
        self.assertEqual(code, 0)
        self.assertEqual([entry["status"] for entry in self.state()], ["exhausted", "published"])
        self.assertNotEqual(runs[0].workdir, runs[1].workdir)

    def test_every_issue_stops_and_leaves_the_problem_to_resume(self):
        for outcome in (Outcome(EXIT_HEALTH_CHECK, "health_check"), Outcome(1, "failsafe"), Outcome(EXIT_AGENT_STAGE, "review")):
            with self.subTest(outcome=outcome):
                code, _ = self.campaign([outcome])
                self.assertEqual(code, outcome.code)
                self.assertEqual(self.state()[-1]["status"], "researching")
                identifier = self.state()[-1]["id"]
                code, runs = self.campaign([Outcome(0, "complete")], max_problems=1)
                self.assertEqual(code, 0)
                self.assertEqual(runs[0].workdir.name, identifier)
                self.assertEqual(self.state()[-1]["status"], "published")

    def test_stop_ends_the_campaign_without_selecting_another_problem(self):
        code, runs = self.campaign([Outcome(EXIT_STOPPED, "stopped")])
        self.assertEqual(code, EXIT_STOPPED)
        self.assertEqual(len(self.state()), 1)
        self.assertEqual(self.emails, [])

    def test_stop_request_for_the_campaign_reaches_the_problem_run(self):
        def supervise(config):
            request_stop(self.campaign_root)
            self.assertTrue(config.external_stop())
            return Outcome(EXIT_STOPPED, "stopped")

        config = CampaignConfig(self.campaign_root, self.run, client=ZooClient(self.zoo.site), rng=random.Random(0))
        with patch("lean1.campaign.supervise_outcome", side_effect=supervise), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_campaign(config), EXIT_STOPPED)

    def test_fetch_failure_stops_with_an_email(self):
        client = ZooClient(self.zoo.site + "missing/", sleep=lambda _: None)
        code, runs = self.campaign([], client=client)
        self.assertEqual(code, EXIT_PROBLEM_SOURCE)
        self.assertEqual(runs, [])
        self.assertEqual(len(self.emails), 1)
        subject, body = self.emails[0]
        self.assertIn("lean1 stopped: could not download", subject)
        self.assertIn("api/index.json", body)
        self.assertIn("--standard-problem", body)

    def test_running_out_of_problems_is_reported(self):
        code, _ = self.campaign([Outcome(1, "limit")] * 4)
        self.assertEqual(code, EXIT_PROBLEM_SOURCE)
        self.assertEqual(len(self.state()), 4)
        self.assertIn("no unsolved problems remain", self.emails[-1][0])

    def test_publication_failure_stops_and_resumes_without_more_research(self):
        def failing(config, **arguments):
            raise PublicationError("the publication package is incomplete after 2 attempts", "Missing manuscript.pdf.")

        code, _ = self.campaign([Outcome(0, "complete")], publish=failing)
        self.assertEqual(code, EXIT_AGENT_STAGE)
        self.assertEqual(self.state()[0]["status"], "accepted")
        self.assertIn("publication package is incomplete", self.emails[-1][0])
        self.assertIn("Missing manuscript.pdf.", self.emails[-1][1])
        code, runs = self.campaign([], max_problems=1)
        self.assertEqual(code, 0)
        self.assertEqual(runs, [])
        self.assertEqual(self.state()[0]["status"], "published")

    def test_unexpected_error_is_reported_once_and_raised(self):
        def broken(config):
            raise OSError("disk full")

        config = CampaignConfig(self.campaign_root, self.run, client=ZooClient(self.zoo.site), rng=random.Random(0))
        with patch("lean1.campaign.supervise_outcome", side_effect=broken), patch.object(EmailConfig, "send_named", return_value=None) as email, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RunnerError, "disk full"):
                run_campaign(config)
        email.assert_called_once()
        self.assertIn("campaign error: disk full", email.call_args.args[2])

    def test_audit_defects_return_to_the_publication_agent(self):
        code, _ = self.campaign([Outcome(0, "complete")], max_problems=1, audits=["revise", "pass"])
        self.assertEqual(code, 0)
        entry = self.state()[0]
        self.assertEqual(entry["status"], "published")
        self.assertTrue(entry["audit"].endswith("audit-2.json"))
        self.assertEqual(self.feedback[0], "")
        self.assertIn("Lemma 2 is due to Doe (2025) but is not cited.", self.feedback[1])
        self.assertIn("passed an independent audit", next(body for subject, body in self.emails if "Solved and packaged" in subject))

    def test_audit_finding_a_prior_solution_or_an_error_moves_on(self):
        for verdict, status, subject in (("solved_elsewhere", "solved_elsewhere", "Already solved elsewhere"), ("reject", "audit_rejected", "Audit rejected")):
            with self.subTest(verdict=verdict):
                self.emails.clear()
                code, runs = self.campaign([Outcome(0, "complete"), Outcome(1, "limit")], max_problems=2, audits=[verdict])
                self.assertEqual(code, 0)
                self.assertEqual(len(runs), 2)
                first = self.state()[-2]
                self.assertEqual(first["status"], status)
                body = next(body for title, body in self.emails if subject in title)
                self.assertIn("Lemma 2 is due to Doe", body)
                self.assertFalse(any("Solved and packaged" in title for title, _ in self.emails))

    def test_persistent_audit_defects_stop_the_campaign(self):
        code, _ = self.campaign([Outcome(0, "complete")], audits=["revise"] * AUDIT_ROUNDS)
        self.assertEqual(code, EXIT_AGENT_STAGE)
        self.assertEqual(self.state()[0]["status"], "accepted")
        self.assertEqual(len(self.feedback), AUDIT_ROUNDS)
        self.assertIn(f"after {AUDIT_ROUNDS} audit rounds", self.emails[-1][0])

    def test_problem_solved_elsewhere_is_reported_and_skipped(self):
        code, runs = self.campaign([Outcome(0, "solved_elsewhere"), Outcome(0, "complete")], max_problems=2)
        self.assertEqual(code, 0)
        self.assertTrue(runs[0].stop_on_prior_solution)
        first, second = self.state()
        self.assertEqual((first["status"], second["status"]), ("solved_elsewhere", "published"))
        self.assertEqual(first["prior_work"], str(runs[0].workdir / "result.md"))
        # Sent even with --notify complete, and it quotes the cited source.
        subject, body = next(email for email in self.emails if "Already solved elsewhere" in email[0])
        self.assertIn(first["id"], subject)
        self.assertIn("arXiv:2501.00001", body)

    def test_resumed_problem_with_a_recorded_prior_solution_is_not_researched_again(self):
        code, _ = self.campaign([Outcome(EXIT_STOPPED, "stopped")])
        self.assertEqual(code, EXIT_STOPPED)
        (Path(self.state()[0]["workspace"]) / "result.md").write_text(SOLVED)
        code, runs = self.campaign([], max_problems=1)
        self.assertEqual(code, 0)
        self.assertEqual(runs, [])
        self.assertEqual(self.state()[0]["status"], "solved_elsewhere")

    def test_notify_all_announces_each_selected_problem(self):
        self.run = RunConfig(self.run.workdir, "", self.run.tools, self.run.email, notify="all")
        self.campaign([Outcome(1, "limit")], max_problems=1)
        self.assertIn("Starting Problem", self.emails[0][0])


if __name__ == "__main__":
    unittest.main()
