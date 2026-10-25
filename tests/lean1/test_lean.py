"""Local Lean discovery and consent-based installation."""

import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from .support import REPO  # noqa: F401

from lean1.common import RunnerError
from lean1.lean import LeanTools, _active_toolchain, _probe, ensure_lean


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


if __name__ == "__main__":
    unittest.main()
