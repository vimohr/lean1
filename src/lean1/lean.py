"""Find Lean without triggering a proxy download; install locally after consent."""

import os
import re
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import urlopen

from .common import RunnerError

DEFAULT_TOOLCHAIN = "leanprover/lean4:stable"
ELAN_INSTALLER = "https://elan.lean-lang.org/elan-init.sh"


@dataclass(frozen=True)
class LeanTools:
    lean: str
    lake: str
    elan: str | None
    env: dict[str, str]
    version: str
    writable_home: Path | None = None


def default_lean_home() -> Path:
    return (Path.cwd() / ".elan").resolve()


def _executable(directory: Path, name: str) -> str | None:
    return shutil.which(name, path=str(directory))


def _project_toolchain(workdir: Path) -> str | None:
    for directory in (workdir, *workdir.parents):
        toolchain_file = directory / "lean-toolchain"
        if toolchain_file.is_file():
            return toolchain_file.read_text(encoding="utf-8").strip()
    return None


def _active_toolchain(workdir: Path, env: dict[str, str]) -> str | None:
    """Read Elan's selection locally; even `elan which` can install overrides."""
    if env.get("ELAN_TOOLCHAIN", "").strip():
        return env["ELAN_TOOLCHAIN"].strip()
    home = Path(env.get("ELAN_HOME") or str(Path.home() / ".elan"))
    settings_file = home / "settings.toml"
    settings = tomllib.loads(settings_file.read_text(encoding="utf-8")) if settings_file.is_file() else {}
    overrides = settings.get("overrides", {})
    if not isinstance(overrides, dict):
        return None
    for directory in (workdir, *workdir.parents):
        overridden = overrides.get(str(directory))
        if overridden:
            return overridden
        toolchain_file = directory / "lean-toolchain"
        if toolchain_file.is_file():
            return toolchain_file.read_text(encoding="utf-8").strip()
    return settings.get("default_toolchain")


def _probe(
    workdir: Path, lean: str, lake: str, env: dict[str, str],
    elan: str | None, writable_home: Path | None = None,
) -> LeanTools | None:
    try:
        # Elan proxies and `elan which` can install missing project overrides.
        # `elan run` without --install explicitly refuses to install toolchains.
        prefix: list[str] = []
        if elan:
            toolchain = _active_toolchain(workdir, env)
            if not isinstance(toolchain, str) or not toolchain or toolchain.startswith("-"):
                return None
            prefix = [elan, "run", toolchain]
        lean_version = subprocess.run(
            prefix + ["lean" if elan else lean, "--version"], cwd=workdir, env=env,
            text=True, capture_output=True, timeout=30,
        )
        if lean_version.returncode:
            return None
        lake_version = subprocess.run(
            prefix + ["lake" if elan else lake, "--version"], cwd=workdir, env=env,
            text=True, capture_output=True, timeout=30,
        )
        if lean_version.returncode or lake_version.returncode:
            return None
        if not re.search(r"\bversion\s+4\.", lean_version.stdout, re.IGNORECASE):
            return None
        return LeanTools(lean, lake, elan, env, lean_version.stdout.strip(), writable_home)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def find_lean(workdir: Path, lean_home: Path) -> LeanTools | None:
    base_env = {key: value for key, value in os.environ.items() if not key.startswith("LEAN1_SMTP_")}
    # Setup and research runs share the launch directory's local installation.
    # Also support a prepared workspace and unpacked Lean distributions.
    locations = [
        (lean_home / "bin", lean_home),
        (workdir / ".elan" / "bin", workdir / ".elan"),
        (Path.cwd(), lean_home),
        (Path.cwd() / "bin", lean_home),
        (workdir, lean_home),
        (workdir / "bin", lean_home),
    ]
    seen: set[Path] = set()
    for binary_dir, home in locations:
        binary_dir, home = binary_dir.resolve(), home.resolve()
        if binary_dir in seen:
            continue
        seen.add(binary_dir)
        lean, lake = _executable(binary_dir, "lean"), _executable(binary_dir, "lake")
        if not lean or not lake:
            continue
        # Keep inherited global Elan settings from directing later downloads
        # into the user's home directory, even when using local binaries.
        env = base_env | {"ELAN_HOME": str(home), "PATH": str(binary_dir) + os.pathsep + base_env.get("PATH", "")}
        elan = _executable(binary_dir, "elan")
        found = _probe(workdir, lean, lake, env, elan, home if elan else None)
        if found:
            return found
    return None


def install_lean(workdir: Path, lean_home: Path, toolchain: str) -> None:
    if os.name != "posix":
        raise RunnerError("Automatic Lean installation supports Linux/macOS. Install Elan manually on Windows and rerun lean1.")
    shell = shutil.which("sh")
    if not shell:
        raise RunnerError("Installing Elan requires a POSIX sh and curl on PATH.")
    if not shutil.which("curl"):
        raise RunnerError("The official Elan installer requires curl on PATH.")
    env = {key: value for key, value in os.environ.items() if not key.startswith("LEAN1_SMTP_")}
    env["ELAN_HOME"] = str(lean_home)
    lean_home.mkdir(parents=True, exist_ok=True)
    elan = _executable(lean_home / "bin", "elan")
    try:
        if not elan:
            with TemporaryDirectory(prefix="lean1-elan-", dir=workdir) as temporary:
                installer = Path(temporary) / "elan-init.sh"
                with urlopen(ELAN_INSTALLER, timeout=60) as response:
                    installer.write_bytes(response.read())
                subprocess.run(
                    [shell, str(installer), "-y", "--no-modify-path", "--default-toolchain", "none"],
                    cwd=workdir, env=env, check=True,
                )
            elan = _executable(lean_home / "bin", "elan")
        if not elan:
            raise RunnerError(f"Elan installation did not create {lean_home / 'bin' / 'elan'}.")
        # Elan installation alone does not guarantee that Lean is downloaded.
        subprocess.run([elan, "toolchain", "install", toolchain], cwd=workdir, env=env, check=True)
        subprocess.run([elan, "default", toolchain], cwd=workdir, env=env, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RunnerError(f"Local Lean installation failed: {exc}. Check connectivity and rerun lean1 setup.") from exc


def ensure_lean(
    workdir: Path, lean_home: Path | None = None, *,
    allow_install: bool = False, no_install: bool = False, toolchain: str | None = None,
) -> LeanTools:
    lean_home = (lean_home or default_lean_home()).expanduser().resolve()
    tools = find_lean(workdir, lean_home)
    if tools:
        print(f"Lean available: {tools.version}", flush=True)
        return tools
    if no_install:
        raise RunnerError("A working Lean 4 and Lake were not found; installation is disabled by --no-install-lean.")
    requested = toolchain or os.environ.get("ELAN_TOOLCHAIN")
    if not requested:
        project_toolchain = _project_toolchain(workdir)
        requested = DEFAULT_TOOLCHAIN if project_toolchain is None else project_toolchain
    if not requested or requested.startswith("-") or any(character.isspace() for character in requested):
        raise RunnerError("The Lean toolchain must be a single nonempty Elan toolchain identifier.")
    if not allow_install:
        if not sys.stdin.isatty():
            raise RunnerError(
                "A working Lean 4 and Lake were not found. Rerun interactively to approve a local install, "
                "or pass --install-lean to give permission explicitly."
            )
        try:
            answer = input(
                f"Lean 4/Lake are missing or unusable. Download Elan and {requested} into {lean_home}?\n"
                "This also allows this agent to manage Lean toolchains in that directory; "
                "shell profiles will not be changed. [y/N] "
            )
        except EOFError:
            answer = ""
        if answer.strip().lower() not in {"y", "yes"}:
            raise RunnerError("Lean installation was declined. Install Lean 4 and Lake, then rerun lean1.")
    print(f"Installing {requested} locally in {lean_home} ...", flush=True)
    install_lean(workdir, lean_home, requested)
    tools = find_lean(workdir, lean_home)
    if not tools:
        raise RunnerError("Lean/Lake still fail their version checks after installation. Check your project's lean-toolchain and ELAN_TOOLCHAIN.")
    print(f"Lean available: {tools.version}", flush=True)
    return tools
