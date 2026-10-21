"""Behavioral checks with local stand-ins; no real agents or emails are sent."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from lean1.common import RunnerError
from lean1.lean import LeanTools, _active_toolchain, _probe, ensure_lean
from lean1.notify import EmailConfig, email_address
from lean1.problems import prepare_problem, resolve_problem
from lean1.runner import RunConfig, is_complete, load_prompt
from lean1.verification import check_axioms, verify


FAKE_TOOLS = r'''
import json
import os
import re
import subprocess
import sys
from pathlib import Path

root = Path(os.environ["LEAN1_TEST_ROOT"])
name = Path(sys.argv[0]).name
with (root / "commands.jsonl").open("a") as stream:
    stream.write(json.dumps({"name": name, "args": sys.argv[1:], "cwd": os.getcwd(), "elan_home": os.environ.get("ELAN_HOME"), "codex_home": os.environ.get("CODEX_HOME"), "smtp_secret_present": bool(os.environ.get("LEAN1_SMTP_PASSWORD"))}) + "\n")
if name == "lean":
    if "--version" in sys.argv:
        if os.environ.get("LEAN1_TEST_PROXY_MISSING"):
            (root / "unapproved-download").write_text("Lean proxy downloaded a toolchain")
        print("Lean (version 4.19.0, x86_64-unknown-linux-gnu, Release)")
        sys.exit(0)
    text = Path(sys.argv[-1]).read_text()
    if "by sorry" in text:
        print("error: declaration uses 'sorry'")
        sys.exit(1)
    if "FAIL_COMPILE" in text:
        print("error: failed to prove the theorem")
        sys.exit(1)
    for theorem in re.findall(r"#print axioms ([A-Za-z0-9_.'\\]+)", text):
        axioms = "assumed" if "axiom assumed" in text else "propext, Classical.choice, Quot.sound"
        print(f"'{theorem}' depends on axioms: [{axioms}]")
    sys.exit(0)
if name == "elan":
    if sys.argv[1] == "which":
        (root / "unapproved-download").write_text("elan which downloaded a project override")
    if sys.argv[1] == "run" and "--install" not in sys.argv:
        print("toolchain not installed", file=sys.stderr)
        sys.exit(1)
    sys.exit(99)
if name == "lake":
    if "--version" in sys.argv:
        print("Lake version 5.0.0")
        sys.exit(0)
    if sys.argv[1] == "env":
        os.execv(sys.argv[2], sys.argv[2:])
    print("Build completed successfully.")
    sys.exit(0)
if name == "mail":
    body = sys.stdin.read()
    with (root / "emails.jsonl").open("a") as stream:
        stream.write(json.dumps({"args": sys.argv[1:], "body": body}) + "\n")
    if os.environ.get("LEAN1_TEST_MAIL_FAIL"):
        print("mail server unavailable", file=sys.stderr)
        sys.exit(1)
    sys.exit(0)
if name == "codex":
    prompt = sys.stdin.read()
    count_file = Path("attempt-count")
    count = int(count_file.read_text()) + 1 if count_file.exists() else 1
    count_file.write_text(str(count))
    (root / f"prompt-{count}.txt").write_text(prompt)
    mode = os.environ.get("LEAN1_TEST_MODE", "success")
    if mode == "startup-failure":
        print("Agent startup failed: authentication is unavailable.", file=sys.stderr)
        sys.exit(7)
    if mode == "empty-response":
        print(json.dumps({"type": "thread.started"}))
        sys.exit(0)
    Path("AGENT_STARTED.txt").write_text("Codex started successfully.\n")
    if mode.startswith("stop-"):
        binary = os.environ.get("LEAN1_TEST_CLI")
        command = [binary] if binary else [sys.executable, "-m", "lean1"]
        arguments = ["stop"]
        stop_cwd = root if mode != "stop-workspace" else Path.cwd()
        if mode == "stop-explicit":
            arguments += ["--workdir", str(Path.cwd())]
            stop_cwd = root / "elsewhere"
            stop_cwd.mkdir(exist_ok=True)
        stopped = subprocess.run(command + arguments, cwd=stop_cwd, text=True, capture_output=True, timeout=10)
        (root / "stop-result.json").write_text(json.dumps({"code": stopped.returncode, "stdout": stopped.stdout, "stderr": stopped.stderr}))
        if stopped.returncode:
            print(stopped.stderr, file=sys.stderr)
            sys.exit(99)
        Path("AFTER_STOP_REQUEST.txt").write_text("The current iteration continued after the stop request.\n")
    source = "namespace Solution\ntheorem answer : 1 = 1 := by rfl\nend Solution\n"
    if mode == "retry-proof" and count == 1:
        source = "namespace Solution\ntheorem answer : 1 = 1 := by sorry\nend Solution\n"
    if mode == "custom-axiom":
        source = "axiom assumed : False\nnamespace Solution\ntheorem answer : False := assumed\nend Solution\n"
    Path("Solution.lean").write_text(source)
    Path("result.md").write_text("The mathematical result and proof.\n")
    if mode != "no-manifest":
        Path("lean_verification.json").write_text(json.dumps({"project_dir": ".", "proofs": [{"file": "Solution.lean", "theorems": ["Solution.answer"]}]}))
    final = "Solved.\nTASK_STATUS: COMPLETE\nLEAN_VERIFIED: YES"
    if mode in {"incomplete", "stop-launch", "stop-workspace", "stop-explicit"} or mode == "retry-status" and count == 1:
        final = "The proof is still in progress."
    print("non-json diagnostic")
    print(json.dumps(["non-event"]))
    print(json.dumps({"type": "item.completed", "item": None}))
    print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": final}}))
    print(json.dumps({"type": "turn.completed"}))
    sys.exit(7 if mode == "bad-exit" else 0)
sys.exit(99)
'''


class InputTests(unittest.TestCase):
    def test_text_url_file_and_forced_literal(self):
        with tempfile.TemporaryDirectory() as temporary:
            problem_file = Path(temporary) / "problem.md"
            problem_file.write_text("Prove α = α.", encoding="utf-8")
            self.assertEqual(resolve_problem("Prove α = α.").kind, "text")
            self.assertEqual(resolve_problem("https://example.org/problem").kind, "url")
            self.assertEqual(resolve_problem(str(problem_file)).kind, "file")
            self.assertEqual(resolve_problem("@" + str(problem_file)).kind, "file")
            self.assertEqual(resolve_problem(str(problem_file), "text").kind, "text")
            with self.assertRaises(RunnerError):
                resolve_problem("./not-a-real-problem.md")
            with self.assertRaises(RunnerError):
                resolve_problem("file:///etc/passwd", "url")
            with patch("sys.stdin", io.StringIO("Prove True.")):
                self.assertEqual(resolve_problem("-", "file").content, "Prove True.")

    def test_binary_file_is_copied_for_agent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "problem.pdf"
            source.write_bytes(b"%PDF-1.7\x00\xff")
            workdir = root / "work"
            workdir.mkdir()
            rendered = prepare_problem(resolve_problem(str(source)), workdir)
            self.assertIn("binary document", rendered)
            self.assertEqual((workdir / ".lean1/inputs/problem.pdf").read_bytes(), source.read_bytes())

    def test_website_snapshot_and_offline_fallback(self):
        headers = Message()
        headers["Content-Type"] = "text/html; charset=utf-8"

        class Response(io.BytesIO):
            def geturl(self):
                return "https://example.org/problem"

        with tempfile.TemporaryDirectory() as temporary:
            workdir = Path(temporary)
            response = Response(b"<h1>Prove this</h1><p>x = x</p><script>secret script</script>")
            response.headers = headers
            with patch("lean1.problems.urlopen", return_value=response):
                prompt = prepare_problem(resolve_problem("https://example.org/problem"), workdir)
            self.assertIn("Prove this", prompt)
            self.assertIn("x = x", prompt)
            self.assertNotIn("secret script", prompt)
            self.assertTrue((workdir / ".lean1/inputs/website.html").exists())
            with patch("lean1.problems.urlopen", side_effect=OSError("offline")):
                prompt = prepare_problem(resolve_problem("https://example.org/problem"), workdir)
            self.assertIn("Use browsing to open the link", prompt)

    def test_prompt_copies_match_and_problem_placeholders_are_literal(self):
        self.assertEqual((REPO / "prompt.txt").read_text(), (REPO / "src/lean1/prompt.txt").read_text())
        with tempfile.TemporaryDirectory() as temporary:
            tools = LeanTools("/lean", "/lake", None, {}, "Lean 4")
            config = RunConfig(Path(temporary), "Prove {{LEAN_COMMAND}} is literal.", tools, EmailConfig("me@example.org", "mail"))
            rendered = load_prompt(config)
            self.assertIn("Prove {{LEAN_COMMAND}} is literal.", rendered)
            self.assertIn("Lean: /lean", rendered)
            self.assertNotIn("L(n,k,m)", rendered)


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workdir = Path(self.temporary.name)
        self.home = self.workdir / "local-elan"
        self.tools = LeanTools("lean", "lake", "elan", {}, "Lean 4")

    def test_no_install_without_permission_headless_or_declined(self):
        with patch("lean1.lean.find_lean", return_value=None), patch("lean1.lean.install_lean") as install:
            with patch("sys.stdin.isatty", return_value=False), self.assertRaises(RunnerError):
                ensure_lean(self.workdir, self.home)
            with patch("sys.stdin.isatty", return_value=True), patch("builtins.input", return_value="no"), self.assertRaises(RunnerError):
                ensure_lean(self.workdir, self.home)
            with self.assertRaises(RunnerError):
                ensure_lean(self.workdir, self.home, no_install=True)
            install.assert_not_called()
            self.assertFalse(self.home.exists())

    def test_local_install_only_after_yes_and_uses_project_toolchain(self):
        (self.workdir / "lean-toolchain").write_text("leanprover/lean4:v4.19.0\n")
        with patch.dict(os.environ, {"ELAN_TOOLCHAIN": ""}), patch("lean1.lean.find_lean", side_effect=[None, self.tools]), patch("sys.stdin.isatty", return_value=True), patch("builtins.input", return_value="yes"), patch("lean1.lean.install_lean") as install, contextlib.redirect_stdout(io.StringIO()):
            result = ensure_lean(self.workdir, self.home)
        self.assertIs(result, self.tools)
        install.assert_called_once_with(self.workdir, self.home, "leanprover/lean4:v4.19.0")

    def test_explicit_install_permission_never_prompts(self):
        with patch("lean1.lean.find_lean", side_effect=[None, self.tools]), patch("builtins.input") as prompt, patch("lean1.lean.install_lean") as install, contextlib.redirect_stdout(io.StringIO()):
            ensure_lean(self.workdir, self.home, allow_install=True, toolchain="leanprover/lean4:v4.19.0")
        prompt.assert_not_called()
        install.assert_called_once()

    def test_default_install_uses_launch_directory_instead_of_home_or_workspace(self):
        launch = self.workdir / "launch"
        launch.mkdir()
        workspace = self.workdir / "research"
        workspace.mkdir()
        env = {
            "HOME": str(self.workdir / "user-home"),
            "XDG_DATA_HOME": str(self.workdir / "user-data"),
            "ELAN_HOME": str(self.workdir / "global-elan"),
            "ELAN_TOOLCHAIN": "",
        }
        with contextlib.chdir(launch), patch.dict(os.environ, env), patch("lean1.lean.find_lean", side_effect=[None, self.tools]), patch("lean1.lean.install_lean") as install, contextlib.redirect_stdout(io.StringIO()):
            ensure_lean(workspace, allow_install=True)
        install.assert_called_once_with(workspace, launch / ".elan", "leanprover/lean4:stable")
        self.assertFalse((workspace / ".elan").exists())
        self.assertFalse((self.workdir / "user-home").exists())
        self.assertFalse((self.workdir / "user-data").exists())
        self.assertFalse((self.workdir / "global-elan").exists())

    def test_existing_lean_needs_no_install(self):
        with patch("lean1.lean.find_lean", return_value=self.tools), patch("lean1.lean.install_lean") as install, contextlib.redirect_stdout(io.StringIO()):
            self.assertIs(ensure_lean(self.workdir, self.home), self.tools)
        install.assert_not_called()

    def test_uninstalled_elan_proxy_never_runs_lean(self):
        (self.workdir / "lean-toolchain").write_text("leanprover/lean4:v4.19.0\n")
        missing = subprocess.CompletedProcess(["elan", "run"], 1, stdout="", stderr="toolchain not installed")
        with patch("lean1.lean.subprocess.run", return_value=missing) as run:
            self.assertIsNone(_probe(self.workdir, "lean", "lake", {"ELAN_HOME": str(self.home)}, "elan"))
        self.assertEqual(run.call_args_list[0].args[0], ["elan", "run", "leanprover/lean4:v4.19.0", "lean", "--version"])
        self.assertFalse(any("--install" in call.args[0] for call in run.call_args_list))
        self.assertFalse(any(call.args[0][0] in {"lean", "lake"} for call in run.call_args_list))

    def test_local_elan_selection_respects_environment_overrides_and_ancestors(self):
        self.home.mkdir()
        child = self.workdir / "project"
        child.mkdir()
        (self.home / "settings.toml").write_text('default_toolchain = "leanprover/lean4:v4.18.0"\n')
        env = {"ELAN_HOME": str(self.home)}
        self.assertEqual(_active_toolchain(child, env), "leanprover/lean4:v4.18.0")
        (self.workdir / "lean-toolchain").write_text("leanprover/lean4:v4.19.0\n")
        self.assertEqual(_active_toolchain(child, env), "leanprover/lean4:v4.19.0")
        settings = 'default_toolchain = "leanprover/lean4:v4.18.0"\n[overrides]\n' + json.dumps(str(self.workdir)) + ' = "leanprover/lean4:v4.20.0"\n'
        (self.home / "settings.toml").write_text(settings)
        self.assertEqual(_active_toolchain(child, env), "leanprover/lean4:v4.20.0")
        self.assertEqual(_active_toolchain(child, env | {"ELAN_TOOLCHAIN": "leanprover/lean4:v4.21.0"}), "leanprover/lean4:v4.21.0")


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

    def test_completion_requires_exact_final_marker_lines(self):
        markers = "TASK_STATUS: COMPLETE\nLEAN_VERIFIED: YES"
        self.assertTrue(is_complete("A result.\n" + markers + "\n"))
        self.assertFalse(is_complete("Do not use " + markers))
        self.assertFalse(is_complete(markers + "\nBut actually incomplete."))
        self.assertFalse(is_complete(None))


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="lean1-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for name in ("codex", "lean", "lake", "mail"):
            path = self.bin / name
            path.write_text(f"#!{sys.executable}\n" + FAKE_TOOLS)
            path.chmod(0o755)
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

    def test_empty_agent_responses_stop_without_complete_only_email(self):
        result = self.run_cli("empty-response", extra=("--notify=complete",), max_runs=0)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual((self.workdir / "attempt-count").read_text(), "10")
        self.assertEqual(self.output()["exit_code"], 0)
        self.assertEqual(self.output()["event_count"], 1)
        self.assertIn("stop_reason", self.output())
        self.assertEqual(self.emails(), [])

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
        elan = self.bin / "elan"
        elan.write_text(f"#!{sys.executable}\n" + FAKE_TOOLS)
        elan.chmod(0o755)
        self.workdir.mkdir()
        (self.workdir / "lean-toolchain").write_text("leanprover/lean4:v4.19.0\n")
        self.env["LEAN1_TEST_PROXY_MISSING"] = "1"
        result = self.run_cli()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("installation is disabled", result.stderr)
        self.assertFalse((self.root / "unapproved-download").exists())
        self.assertFalse(any(entry["name"] == "codex" for entry in self.commands()))


class EmailTests(unittest.TestCase):
    def test_email_rejects_option_and_header_injection(self):
        for value in ("-option@example.org", "a@example.org\nBcc: b@example.org", "a@example.org,b@example.org", "a\x00@example.org", "not-an-email"):
            with self.subTest(value=value), self.assertRaises(RunnerError):
                email_address(value)

    def test_smtp_starttls_authentication_submission_and_saved_email(self):
        env = {
            "LEAN1_SMTP_HOST": "smtp.example.org", "LEAN1_SMTP_FROM": "sender@example.org",
            "LEAN1_SMTP_USERNAME": "sender@example.org", "LEAN1_SMTP_PASSWORD": "secret",
        }
        with patch.dict(os.environ, env, clear=True), tempfile.TemporaryDirectory() as temporary, patch("lean1.notify.smtplib.SMTP") as smtp, contextlib.redirect_stdout(io.StringIO()):
            config = EmailConfig.load("recipient@example.org")
            client = smtp.return_value
            client.send_message.return_value = {}
            self.assertIsNone(config.send(Path(temporary), 1, "Complete", "The proof is verified."))
            client.starttls.assert_called_once()
            client.login.assert_called_once_with("sender@example.org", "secret")
            submitted = client.send_message.call_args.args[0]
            self.assertEqual(submitted["To"], "recipient@example.org")
            self.assertEqual(submitted["From"], "sender@example.org")
            self.assertNotIn("secret", (Path(temporary) / ".lean1/notifications/run-0001.eml").read_text())
            self.assertNotIn("secret", repr(config))


if __name__ == "__main__":
    unittest.main()
