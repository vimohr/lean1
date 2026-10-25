"""Run configuration helpers."""

import unittest
from pathlib import Path

from .support import REPO  # noqa: F401

from lean1.config import RunConfig
from lean1.lean import LeanTools
from lean1.notify import EmailConfig


class ConfigTests(unittest.TestCase):
    def test_agent_command_and_labelled_subjects(self):
        tools = LeanTools("lean", "lake", "elan", {}, "Lean 4", writable_home=Path("/opt/elan"))
        config = RunConfig(Path("/work"), "", tools, EmailConfig("me@example.org", "mail"), codex="/bin/codex", model="m", network=False)
        command = config.agent_command()
        self.assertEqual(command[0], "/bin/codex")
        self.assertIn("--add-dir", command)
        self.assertNotIn("--search", command)
        self.assertEqual(config.subject("Done"), "Done")
        labelled = RunConfig(Path("/work"), "", tools, EmailConfig("me@example.org", "mail"), label="op_1")
        self.assertEqual(labelled.subject("Done"), "[op_1] Done")


if __name__ == "__main__":
    unittest.main()
