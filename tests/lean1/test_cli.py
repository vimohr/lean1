"""Command-line pipeline tests with simulated Codex, Lean, Lake, and mail."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from .support import REPO, FakeZoo, install_fake_tools, problem_id


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="lean1-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bin = self.root / "bin"
        install_fake_tools(self.bin)
        self.workdir = self.root / "work"
        self.env = os.environ.copy() | {
            "PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", ""),
            "PYTHONPATH": str(REPO / "src"),
            "ELAN_HOME": str(self.root / "uninstalled-elan"),
            "CODEX_HOME": str(self.root / "codex-seed"),
            "XDG_DATA_HOME": str(self.root / "data"),
            "LEAN1_TEST_ROOT": str(self.root),
            "LEAN1_SMTP_PASSWORD": "test-secret-must-not-reach-agent",
        }

    def run_command(self, arguments, mode="success"):
        binary = os.environ.get("LEAN1_TEST_CLI")
        command = [binary] if binary else [sys.executable, "-m", "lean1"]
        environment = self.env | {"LEAN1_TEST_MODE": mode}
        if binary:
            # The installed tool must find its wheel's modules and prompt, even
            # when run elsewhere with no source tree on Python's import path.
            environment.pop("PYTHONPATH", None)
        return subprocess.run(command + arguments, cwd=self.root, env=environment, text=True, capture_output=True, timeout=30)

    def run_cli(self, mode="success", extra=(), max_runs=1):
        return self.run_command([
            "--email=researcher@example.org", "--problem-text=Prove α = α; retain `literal` syntax.",
            "--workdir", str(self.workdir), "--max-runs", str(max_runs), "--restart-delay=0",
            "--no-install-lean", "--email-backend=mail",
            "--mail-command", str(self.bin / "mail"), "--codex-command", str(self.bin / "codex"),
            *extra,
        ], mode)

    def run_setup(self, extra=()):
        return self.run_command(["setup", "--no-install-lean", *extra])

    def output(self):
        return json.loads((self.workdir / "codex_last_output.json").read_text())

    def emails(self):
        path = self.root / "emails.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def commands(self):
        return [json.loads(line) for line in (self.root / "commands.jsonl").read_text().splitlines()]

    def test_setup_and_runs_reuse_local_binaries_with_separate_workspace(self):
        current_bin = self.bin
        for layout in (".elan/bin", ".", "bin", "work/.elan/bin"):
            with self.subTest(layout=layout):
                binary_dir = self.root / layout
                binary_dir.mkdir(parents=True, exist_ok=True)
                for name in ("lean", "lake"):
                    (current_bin / name).rename(binary_dir / name)
                current_bin = binary_dir
                workspace_local = layout == "work/.elan/bin"
                setup_args = ("--workdir", str(self.workdir)) if workspace_local else ()
                result = self.run_setup(setup_args)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertFalse(any(entry["name"] in {"codex", "mail"} for entry in self.commands()[-2:]))
                result = self.run_cli()
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                expected_home = (self.workdir if workspace_local else self.root) / ".elan"
                lean_commands = [entry for entry in self.commands() if entry["name"] in {"lean", "lake", "codex"}]
                self.assertEqual(lean_commands[-1]["elan_home"], str(expected_home))
                self.assertTrue(self.output()["task_complete"])
        self.assertFalse((self.root / "data").exists())
        self.assertFalse((self.root / "uninstalled-elan").exists())

    def test_global_installations_do_not_replace_missing_local_binaries(self):
        user_home = self.root / "user-home"
        inherited_home = self.root / "inherited-elan"
        for home in (user_home / ".elan", self.root / "data/lean1/elan", inherited_home):
            binary_dir = home / "bin"
            binary_dir.mkdir(parents=True)
            for name in ("lean", "lake"):
                binary = binary_dir / name
                binary.write_bytes((self.bin / name).read_bytes())
                binary.chmod(0o755)
        for name in ("lean", "lake"):
            (self.bin / name).unlink()
        self.env.update({
            "HOME": str(user_home),
            "ELAN_HOME": str(inherited_home),
            "PATH": str(inherited_home / "bin") + os.pathsep + self.env["PATH"],
        })
        result = self.run_setup()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("installation is disabled", result.stderr)
        self.assertFalse((self.root / ".elan").exists())
        self.assertFalse((self.root / "commands.jsonl").exists())

    def test_explicit_lean_home_selects_another_installation(self):
        lean_home = self.root / "custom-elan"
        binary_dir = lean_home / "bin"
        binary_dir.mkdir(parents=True)
        for name in ("lean", "lake"):
            (self.bin / name).rename(binary_dir / name)
        extra = ("--lean-home", str(lean_home))
        result = self.run_setup(extra)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = self.run_cli(extra=extra)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        commands = [entry for entry in self.commands() if entry["name"] in {"lean", "lake", "codex"}]
        self.assertTrue(all(entry["elan_home"] == str(lean_home) for entry in commands))
        self.assertFalse((self.root / ".elan").exists())

    def test_complete_pipeline_recompiles_proof_and_sends_email(self):
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.output()["task_complete"])
        self.assertTrue(self.output()["verification"]["success"])
        self.assertIn("Task complete: True", self.emails()[0]["body"])
        self.assertEqual(self.emails()[0]["args"][-1], "researcher@example.org")
        self.assertTrue((self.workdir / ".lean1/run-0001.events.jsonl").exists())
        self.assertTrue((self.workdir / ".lean1/notifications/run-0001.eml").exists())
        checks = [entry for entry in self.commands() if entry["name"] == "lean" and "--version" not in entry["args"]]
        self.assertEqual(len(checks), 1)
        self.assertIn("-DwarningAsError=true", checks[0]["args"])
        self.assertFalse(any(entry["smtp_secret_present"] for entry in self.commands() if entry["name"] != "mail"))
        self.assertEqual(list(self.workdir.glob("Lean1Verification*.lean")), [])
        self.assertIn("Prove α = α; retain `literal` syntax.", (self.root / "prompt-1.txt").read_text())

    def test_failed_proof_is_retried_with_feedback_then_succeeds(self):
        result = self.run_cli("retry-proof", max_runs=2)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.output()["run_number"], 2)
        self.assertEqual(len(self.emails()), 2)
        self.assertIn("Task complete: False", self.emails()[0]["body"])
        self.assertIn("declaration uses 'sorry'", (self.root / "prompt-2.txt").read_text())

    def test_complete_only_email_and_existing_workspace_log_numbering(self):
        result = self.run_cli("retry-status", extra=("--notify=complete",), max_runs=2)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(self.emails()), 1)
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.output()["run_number"], 3)
        self.assertTrue((self.workdir / ".lean1/run-0001.events.jsonl").exists())

    def test_completion_markers_never_bypass_manifest_compiler_or_axiom_checks(self):
        for mode in ("no-manifest", "custom-axiom", "bad-exit"):
            with self.subTest(mode=mode):
                self.workdir = self.root / mode
                result = self.run_cli(mode)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertFalse(self.output()["task_complete"])

    def test_limit_does_not_send_success_email(self):
        result = self.run_cli("incomplete", extra=("--notify=complete",))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertFalse(self.output()["task_complete"])
        self.assertEqual(self.emails(), [])

    def test_rapid_startup_failures_stop_and_allow_continuation(self):
        result = self.run_cli("startup-failure", max_runs=0)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual((self.workdir / "attempt-count").read_text(), "10")
        self.assertEqual(self.output()["run_number"], 10)
        self.assertIn("10 times within 60 seconds", self.output()["stop_reason"])
        self.assertIn("authentication is unavailable", self.output()["stderr"])
        self.assertIn("Failsafe:", result.stdout)
        self.assertIn("stopped incomplete", self.emails()[-1]["args"][1])
        self.assertIn(self.output()["stop_reason"], self.emails()[-1]["body"])
        self.assertFalse((self.workdir / "AGENT_STARTED.txt").exists())
        self.assertFalse((self.workdir / ".lean1/active_run.json").exists())
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.output()["run_number"], 11)
        self.assertNotIn("stop_reason", self.output())

    def test_empty_agent_responses_stop_and_always_report_the_failsafe(self):
        result = self.run_cli("empty-response", extra=("--notify=complete",), max_runs=0)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual((self.workdir / "attempt-count").read_text(), "10")
        self.assertEqual(self.output()["exit_code"], 0)
        self.assertEqual(self.output()["event_count"], 1)
        self.assertIn("stop_reason", self.output())
        # The failsafe is an issue, so it is reported even with --notify=complete.
        self.assertEqual(len(self.emails()), 1)
        self.assertIn("stopped incomplete", self.emails()[0]["args"][1])
        self.assertIn(self.output()["stop_reason"], self.emails()[0]["body"])

    def test_stop_finishes_current_iteration_saves_results_and_allows_continuation(self):
        for mode in ("stop-launch", "stop-workspace", "stop-explicit"):
            with self.subTest(mode=mode):
                self.workdir = self.root / mode
                result = self.run_cli(mode, max_runs=3)
                self.assertEqual(result.returncode, 130, result.stdout + result.stderr)
                stopped = json.loads((self.root / "stop-result.json").read_text())
                self.assertEqual(stopped["code"], 0, stopped)
                self.assertEqual((self.workdir / "attempt-count").read_text(), "1")
                self.assertTrue((self.workdir / "AFTER_STOP_REQUEST.txt").exists())
                self.assertEqual(self.output()["final_message"], "The proof is still in progress.")
                self.assertTrue(self.output()["stop_requested"])
                self.assertIn("stopped incomplete", self.emails()[-1]["args"][1])
                self.assertFalse((self.workdir / ".lean1/active_run.json").exists())
                self.assertFalse((self.workdir / ".lean1/stop_requested").exists())
                result = self.run_cli()
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(self.output()["run_number"], 2)
                self.assertFalse(self.output()["stop_requested"])
        self.assertEqual(list((self.root / ".lean1/active-runs").glob("*.json")), [])

    def test_stop_still_verifies_and_notifies_a_completed_solution(self):
        result = self.run_cli("stop-success", extra=("--notify=complete",), max_runs=3)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.output()["run_number"], 1)
        self.assertTrue(self.output()["stop_requested"])
        self.assertTrue(self.output()["verification"]["success"])
        self.assertIn("completed task", self.emails()[0]["args"][1])

    def test_stop_respects_complete_only_notifications(self):
        result = self.run_cli("stop-launch", extra=("--notify=complete",), max_runs=0)
        self.assertEqual(result.returncode, 130, result.stdout + result.stderr)
        self.assertEqual(self.output()["run_number"], 1)
        self.assertEqual(self.emails(), [])

    def test_stop_without_a_running_supervisor_needs_no_dependencies(self):
        for name in ("codex", "lean", "lake", "mail"):
            (self.bin / name).unlink()
        result = self.run_command(["stop"])
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("No active lean1 run", result.stderr)
        result = self.run_command(["stop", "--workdir", str(self.workdir)])
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertFalse(self.workdir.exists())

    def test_codex_state_uses_workspace_home_over_inherited_home(self):
        inherited = self.root / "inherited-codex"
        inherited.mkdir()
        (inherited / "auth.json").write_text('{"token": "test-login"}')
        (inherited / "config.toml").write_text('model = "configured-model"\n')
        (inherited / "review.config.toml").write_text('model_reasoning_effort = "high"\n')
        (inherited / "history.jsonl").write_text("global history must stay global\n")
        self.env["CODEX_HOME"] = str(inherited)
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        codex = [entry for entry in self.commands() if entry["name"] == "codex"]
        local_home = self.workdir / ".codex"
        self.assertEqual(codex[0]["codex_home"], str(local_home))
        for name in ("auth.json", "config.toml", "review.config.toml"):
            self.assertEqual((local_home / name).read_bytes(), (inherited / name).read_bytes())
            if os.name == "posix":
                self.assertEqual((local_home / name).stat().st_mode & 0o777, 0o600)
        self.assertFalse((local_home / "history.jsonl").exists())
        (local_home / "auth.json").write_text("workspace-login")
        (local_home / "config.toml").write_text("workspace-configuration")
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((local_home / "auth.json").read_text(), "workspace-login")
        self.assertEqual((local_home / "config.toml").read_text(), "workspace-configuration")
        self.assertEqual((inherited / "auth.json").read_text(), '{"token": "test-login"}')

    def test_default_codex_home_seeds_from_user_home_without_writing_there(self):
        user_home = self.root / "user-home"
        original = user_home / ".codex"
        original.mkdir(parents=True)
        (original / "auth.json").write_text("existing-login")
        self.env.pop("CODEX_HOME", None)
        self.env["HOME"] = str(user_home)
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        codex = [entry for entry in self.commands() if entry["name"] == "codex"]
        self.assertEqual(codex[0]["codex_home"], str(self.workdir / ".codex"))
        self.assertEqual((self.workdir / ".codex/auth.json").read_text(), "existing-login")
        self.assertEqual(list(original.iterdir()), [original / "auth.json"])

    def test_mail_failure_keeps_verified_result_and_unsent_email(self):
        self.env["LEAN1_TEST_MAIL_FAIL"] = "1"
        result = self.run_cli()
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertTrue(self.output()["task_complete"])
        self.assertIn("mail server unavailable", self.output()["notification_error"])
        self.assertTrue((self.workdir / ".lean1/notifications/run-0001.eml").exists())

    def test_lake_project_is_built_before_proof_check(self):
        self.workdir.mkdir()
        (self.workdir / "lakefile.toml").write_text('name = "solution"\n')
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lake = [entry for entry in self.commands() if entry["name"] == "lake"]
        self.assertTrue(any(entry["args"] == ["build"] for entry in lake))
        self.assertTrue(any(entry["args"][0] == "env" for entry in lake))

    def test_relative_executable_paths_survive_workspace_change(self):
        self.env["PATH"] = "bin" + os.pathsep + os.environ.get("PATH", "")
        result = self.run_cli(extra=("--codex-command=./bin/codex", "--mail-command=./bin/mail"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.output()["task_complete"])

    def test_missing_project_toolchain_cannot_download_without_permission(self):
        install_fake_tools(self.bin, ("elan",))
        self.workdir.mkdir()
        (self.workdir / "lean-toolchain").write_text("leanprover/lean4:v4.19.0\n")
        self.env["LEAN1_TEST_PROXY_MISSING"] = "1"
        result = self.run_cli()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("installation is disabled", result.stderr)
        self.assertFalse((self.root / "unapproved-download").exists())
        self.assertFalse(any(entry["name"] == "codex" for entry in self.commands()))


class StandardProblemCliTests(unittest.TestCase):
    """End-to-end campaigns against a local stand-in for qiqc-op.com."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="lean1-standard-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bin = self.root / "bin"
        install_fake_tools(self.bin)
        self.ids = [problem_id(number) for number in range(1, 4)]
        self.solved = problem_id(9)
        self.zoo = FakeZoo({identifier: "Unsolved" for identifier in self.ids} | {self.solved: "Solved"}).__enter__()
        self.addCleanup(self.zoo.__exit__)
        self.campaign = self.root / "campaign"
        self.env = os.environ.copy() | {
            "PATH": str(self.bin) + os.pathsep + os.environ.get("PATH", ""),
            "PYTHONPATH": str(REPO / "src"),
            "ELAN_HOME": str(self.root / "uninstalled-elan"),
            "CODEX_HOME": str(self.root / "codex-seed"),
            "LEAN1_TEST_ROOT": str(self.root),
            "LEAN1_QIQCOP_SITE": self.zoo.site,
        }

    def run_cli(self, *extra, **modes):
        binary = os.environ.get("LEAN1_TEST_CLI")
        command = [binary] if binary else [sys.executable, "-m", "lean1"]
        environment = self.env | {f"LEAN1_TEST_{key.upper()}": value for key, value in modes.items()}
        if binary:
            environment.pop("PYTHONPATH", None)
        arguments = [
            "--email=researcher@example.org", "--standard-problem", "--workdir", str(self.campaign),
            "--restart-delay=0", "--no-install-lean", "--email-backend=mail",
            "--mail-command", str(self.bin / "mail"), "--codex-command", str(self.bin / "codex"),
            "--health-check-retries=0", "--author-name=Ada Lovelace", *extra,
        ]
        return subprocess.run(command + arguments, cwd=self.root, env=environment, text=True, capture_output=True, timeout=120)

    def state(self):
        return json.loads((self.campaign / "campaign.json").read_text())["problems"]

    def emails(self):
        path = self.root / "emails.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def subjects(self):
        return [email["args"][1] for email in self.emails()]

    def test_campaign_solves_reviews_packages_and_continues(self):
        result = self.run_cli("--max-problems=2", "--max-runs=1", "--notify=complete")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        problems = self.state()
        self.assertEqual([entry["status"] for entry in problems], ["published", "published"])
        chosen = [entry["id"] for entry in problems]
        self.assertEqual(len(set(chosen)), 2)
        self.assertNotIn(self.solved, chosen)
        for identifier in chosen:
            workspace = self.campaign / "problems" / identifier
            standard = (workspace / ".lean1/inputs/standard_problem.txt").read_text()
            self.assertEqual(standard, f"Solve this problem {self.zoo.site}problem/{identifier}/. Make no mistakes.\n")
            self.assertIn(standard.strip(), (workspace / ".lean1/prompt.txt").read_text())
            self.assertTrue((workspace / "release/manuscript.pdf").is_file())
            log = subprocess.run(["git", "-C", str(workspace / "release"), "log", "--format=%s"], text=True, capture_output=True)
            self.assertEqual(log.stdout.strip(), "feat: initial release")
        self.assertEqual((self.root / "review-count").read_text(), "2")
        self.assertIn("Ada Lovelace", (self.root / "publish-prompt-1.txt").read_text())
        packaged = [subject for subject in self.subjects() if "Solved and packaged" in subject]
        self.assertEqual(len(packaged), 2)
        # Health checks precede each research, review, publication, and audit execution.
        self.assertEqual((self.root / "health-count").read_text(), "8")
        self.assertEqual((self.root / "audit-count").read_text(), "2")
        self.assertTrue(all(entry["audit"] for entry in problems))

    def test_rejected_solution_is_retried_with_review_feedback_then_skipped(self):
        result = self.run_cli("--max-problems=1", "--max-runs=2", "--notify=complete", review="reject")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.state()[0]["status"], "exhausted")
        self.assertIn("the website asks for every n", (self.root / "prompt-2.txt").read_text())
        self.assertFalse((self.root / "publish-count").exists())
        self.assertEqual(self.emails(), [])

    def test_problem_solved_elsewhere_is_skipped_without_review(self):
        result = self.run_cli("--max-problems=1", "--notify=complete", mode="prior-solved")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.state()[0]["status"], "solved_elsewhere")
        self.assertEqual(len((self.root / "research-workspaces.txt").read_text().splitlines()), 1)
        self.assertIn("Prior resolution: COMPLETE", (self.root / "prompt-1.txt").read_text())
        self.assertFalse((self.root / "review-count").exists())
        self.assertFalse((self.root / "publish-count").exists())
        self.assertEqual(len(self.emails()), 1)
        self.assertIn("Already solved elsewhere", self.subjects()[0])
        self.assertIn("arXiv:2501.00001", self.emails()[0]["body"])

    def test_audit_revisions_reach_the_publication_agent(self):
        result = self.run_cli("--max-problems=1", "--max-runs=1", audit="revise-once")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.state()[0]["status"], "published")
        self.assertEqual((self.root / "publish-count").read_text(), "2")
        self.assertIn("Lemma 2 is due to Doe (2025) but is not cited.", (self.root / "publish-prompt-2.txt").read_text())
        self.assertIn("Independent prior-work search", (self.root / "audit-prompt-1.txt").read_text())

    def test_audit_finding_a_prior_solution_skips_the_problem(self):
        result = self.run_cli("--max-problems=1", "--max-runs=1", "--notify=complete", audit="solved_elsewhere")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.state()[0]["status"], "solved_elsewhere")
        self.assertIn("Already solved elsewhere", self.subjects()[-1])
        self.assertIn("arXiv:2501.00001", self.emails()[-1]["body"])

    def test_auditor_editing_the_release_stops_the_campaign(self):
        result = self.run_cli("--max-problems=1", "--max-runs=1", audit="tamper")
        self.assertEqual(result.returncode, 6, result.stdout + result.stderr)
        self.assertEqual(self.state()[0]["status"], "accepted")
        self.assertIn("modified the release", self.subjects()[-1])
        self.assertIn("release/README.md", self.emails()[-1]["body"])

    def test_review_accepting_after_one_rejection_is_published(self):
        result = self.run_cli("--max-problems=1", "--max-runs=3", review="reject-once")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.state()[0]["status"], "published")
        self.assertEqual((self.root / "review-count").read_text(), "2")

    def test_health_failure_pauses_with_email_and_resumes_the_same_problem(self):
        result = self.run_cli("--max-problems=1", "--notify=complete", health="fail")
        self.assertEqual(result.returncode, 4, result.stdout + result.stderr)
        self.assertEqual(len(self.state()), 1)
        self.assertFalse((self.root / "research-workspaces.txt").exists())
        self.assertEqual(len(self.emails()), 1)
        self.assertIn("lean1 stopped", self.subjects()[0])
        self.assertIn("Codex usage limit reached.", self.emails()[0]["body"])
        identifier = self.state()[0]["id"]
        result = self.run_cli("--max-problems=1", "--notify=complete")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([entry["id"] for entry in self.state()], [identifier])
        self.assertEqual(self.state()[0]["status"], "published")

    def test_failure_after_a_solution_stops_before_review(self):
        self.env["LEAN1_TEST_HEALTH_FAIL_FROM"] = "2"
        result = self.run_cli("--max-problems=1", health="fail")
        self.assertEqual(result.returncode, 4, result.stdout + result.stderr)
        self.assertFalse((self.root / "review-count").exists())
        self.assertIn("before review of run #1", self.subjects()[-1])

    def test_website_failure_stops_with_email(self):
        self.env["LEAN1_QIQCOP_SITE"] = self.zoo.site + "offline/"
        result = self.run_cli()
        self.assertEqual(result.returncode, 5, result.stdout + result.stderr)
        self.assertIn("could not download", self.subjects()[0])
        self.assertFalse((self.root / "research-workspaces.txt").exists())

    def test_invalid_option_combinations(self):
        result = self.run_cli("--no-network")
        self.assertEqual(result.returncode, 2)
        self.assertIn("needs network access", result.stderr)
        result = subprocess.run(
            [sys.executable, "-m", "lean1", "--email=a@example.org", "--problem-text=x", "--max-problems=2"],
            cwd=self.root, env=self.env, text=True, capture_output=True, timeout=30,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("applies only with --standard-problem", result.stderr)
        result = self.run_cli("--problem-text=x")
        self.assertEqual(result.returncode, 2)
        self.assertIn("not allowed with argument", result.stderr)

    def test_graceful_stop_ends_the_campaign_after_the_current_attempt(self):
        result = self.run_cli("--max-runs=3", mode="stop-launch")
        self.assertEqual(result.returncode, 130, result.stdout + result.stderr)
        stopped = json.loads((self.root / "stop-result.json").read_text())
        self.assertEqual(stopped["code"], 0, stopped)
        self.assertEqual(len(self.state()), 1)
        self.assertEqual(self.state()[0]["status"], "researching")
        self.assertFalse((self.campaign / ".lean1/active_run.json").exists())


if __name__ == "__main__":
    unittest.main()
