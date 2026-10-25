"""Codex command construction, event parsing, timeouts, and environments."""

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

from .support import REPO  # noqa: F401

from lean1.codex import codex_command, codex_environment, error_message, execute_codex, final_agent_message


def script(directory: Path, body: str) -> str:
    path = directory / "fake-codex"
    path.write_text(f"#!{sys.executable}\nimport json, sys, time\nsys.stdin.read()\n{body}\n")
    path.chmod(0o755)
    return str(path)


class CommandTests(unittest.TestCase):
    def test_network_model_and_writable_home_flags(self):
        workdir = Path("/work")
        command = codex_command("codex", workdir=workdir, model="gpt-test", writable_home=Path("/elan"))
        self.assertEqual(command[:3], ["codex", "--search", "exec"])
        self.assertIn("sandbox_workspace_write.network_access=true", command)
        self.assertEqual(command[command.index("--model") + 1], "gpt-test")
        self.assertEqual(command[command.index("--add-dir") + 1], "/elan")
        self.assertEqual(command[-1], "-")
        offline = codex_command("codex", workdir=workdir, network=False, writable_home=workdir / ".elan")
        self.assertNotIn("--search", offline)
        self.assertNotIn("--add-dir", offline)
        self.assertNotIn("--model", offline)
        self.assertIn("sandbox_workspace_write.network_access=false", offline)

    def test_environment_is_local_and_omits_smtp_secrets(self):
        with tempfile.TemporaryDirectory() as temporary:
            workdir = Path(temporary)
            env = codex_environment(workdir, {"PATH": "/bin", "LEAN1_SMTP_PASSWORD": "secret", "CODEX_HOME": str(workdir / "none")})
            self.assertEqual(env["CODEX_HOME"], str((workdir / ".codex").resolve()))
            self.assertNotIn("LEAN1_SMTP_PASSWORD", env)
            self.assertEqual(env["PATH"], "/bin")


class EventTests(unittest.TestCase):
    def test_final_message_and_error_extraction(self):
        self.assertEqual(final_agent_message({"type": "item.completed", "item": {"type": "agent_message", "text": "hi"}}), "hi")
        self.assertIsNone(final_agent_message({"type": "item.completed", "item": {"type": "reasoning", "text": "hi"}}))
        self.assertIsNone(final_agent_message({"type": "item.completed", "item": None}))
        self.assertEqual(error_message({"type": "error", "message": "quota"}), "quota")
        self.assertEqual(error_message({"type": "turn.failed", "error": {"message": "bad model"}}), "bad model")
        self.assertEqual(error_message({"type": "item.completed", "item": {"type": "error", "message": "warn"}}), "warn")
        self.assertIsNone(error_message({"type": "turn.completed"}))


class ExecuteTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.prompt = self.root / "prompt.txt"
        self.prompt.write_text("Hello")

    def execute(self, executable: str, **options):
        return execute_codex(
            [executable], cwd=self.root, env={"PATH": "/usr/bin:/bin"}, prompt_file=self.prompt,
            events_file=self.root / "logs" / "events.jsonl", stderr_file=self.root / "logs" / "stderr.log",
            echo=False, **options,
        )

    def test_last_agent_message_errors_and_logs_are_recorded(self):
        events = [
            "not json", json.dumps([1]),
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "first"}}),
            json.dumps({"type": "error", "message": "transient"}),
            json.dumps({"type": "error", "message": "transient"}),
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "last"}}),
        ]
        body = "\n".join(f"print({line!r})" for line in events) + "\nprint('diagnostic', file=sys.stderr)\nsys.exit(4)"
        result = self.execute(script(self.root, body))
        self.assertEqual(result.returncode, 4)
        self.assertEqual(result.final_message, "last")
        self.assertEqual(result.event_count, 4)
        self.assertEqual(result.errors, ("transient",))
        self.assertFalse(result.timed_out)
        self.assertEqual(len(result.events_file.read_text().splitlines()), 6)
        self.assertIn("diagnostic", result.stderr_file.read_text())

    def test_timeout_stops_a_hanging_execution(self):
        started = time.monotonic()
        result = self.execute(script(self.root, "time.sleep(60)"), timeout=0.5)
        self.assertTrue(result.timed_out)
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(result.final_message)
        self.assertLess(time.monotonic() - started, 20)


if __name__ == "__main__":
    unittest.main()
