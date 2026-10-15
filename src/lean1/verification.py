"""Recheck the declared proofs and their transitive axiom dependencies."""

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile

from .common import RunnerError
from .lean import LeanTools

TRUSTED_AXIOMS = {"propext", "Classical.choice", "Quot.sound"}
THEOREM_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_']*(?:\.[A-Za-z_][A-Za-z0-9_']*)*\Z")


@dataclass(frozen=True)
class Verification:
    success: bool
    detail: str
    log_file: str


def within(root: Path, relative: object) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise RunnerError("Verification paths must be nonempty paths relative to the workspace/project.")
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise RunnerError(f"Verification path escapes its root: {relative}")
    return path


def check_axioms(output: str, names: list[str]) -> None:
    for name in names:
        prefix = rf"'{re.escape(name)}'"
        no_axioms = re.search(prefix + r" does not depend on any axioms", output)
        reports = re.findall(prefix + r" depends on axioms:\s*\[([^\]]*)\]", output)
        if not no_axioms and not reports:
            raise RunnerError(f"Lean did not report axiom dependencies for {name}.")
        for report in reports:
            axioms = {item.strip() for item in report.split(",") if item.strip()}
            untrusted = axioms - TRUSTED_AXIOMS
            if untrusted:
                raise RunnerError(f"{name} depends on disallowed axioms: {', '.join(sorted(untrusted))}.")


def verify(workdir: Path, tools: LeanTools, run_number: int, timeout: float) -> Verification:
    log_path = workdir / ".lean1" / f"run-{run_number:04d}.lean.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        manifest = json.loads((workdir / "lean_verification.json").read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise RunnerError("lean_verification.json must be a JSON object.")
        project = within(workdir, manifest.get("project_dir", "."))
        if not project.is_dir():
            raise RunnerError("The verification project directory does not exist.")
        proofs = manifest.get("proofs")
        if not isinstance(proofs, list) or not proofs:
            raise RunnerError("lean_verification.json must list at least one proof and its named theorems.")
        resolved: list[tuple[Path, list[str]]] = []
        for proof in proofs:
            if not isinstance(proof, dict):
                raise RunnerError("Each proof entry must contain a file and theorems.")
            source = within(project, proof.get("file"))
            if source.suffix != ".lean" or not source.is_file():
                raise RunnerError(f"Lean proof file not found: {source}")
            names = proof.get("theorems")
            if not isinstance(names, list) or not names or any(
                not isinstance(name, str) or not THEOREM_NAME.fullmatch(name) for name in names
            ):
                raise RunnerError("Each proof needs fully qualified ASCII theorem names, such as Solution.result.")
            resolved.append((source, names))

        with log_path.open("w", encoding="utf-8") as log_stream:
            def execute(command: list[str]) -> str:
                log_stream.write(f"\nCommand: {command!r}\nDirectory: {project}\n")
                log_stream.flush()
                # Send output directly to disk, including on timeout/interruption.
                start = log_stream.tell()
                result = subprocess.run(
                    command, cwd=project, env=tools.env, stdout=log_stream,
                    stderr=subprocess.STDOUT, timeout=timeout,
                )
                log_stream.flush()
                with log_path.open("r", encoding="utf-8", errors="replace") as reader:
                    reader.seek(start)
                    output = reader.read()
                if result.returncode:
                    raise RunnerError(f"Lean command failed (exit {result.returncode}): {command!r}\n{output[-8000:]}")
                return output

            lake_project = (project / "lakefile.lean").is_file() or (project / "lakefile.toml").is_file()
            if lake_project:
                execute([tools.lake, "build"])
            for source, names in resolved:
                # Re-elaborate the actual proof source, then print dependencies in
                # the same environment; standalone files need no generated oleans.
                with NamedTemporaryFile(
                    mode="w", encoding="utf-8", prefix="Lean1Verification",
                    suffix=".lean", dir=source.parent, delete=False,
                ) as stream:
                    check_file = Path(stream.name)
                    stream.write(source.read_text(encoding="utf-8"))
                    stream.write("\n\nset_option warningAsError true\n")
                    for name in names:
                        stream.write(f"#check {name}\n#print axioms {name}\n")
                try:
                    command = ([tools.lake, "env", tools.lean] if lake_project else [tools.lean])
                    output = execute(command + ["-DwarningAsError=true", str(check_file.relative_to(project))])
                    check_axioms(output, names)
                finally:
                    check_file.unlink(missing_ok=True)
        return Verification(True, "Lean compilation and theorem axiom checks succeeded.", str(log_path))
    except (OSError, ValueError, RunnerError, subprocess.SubprocessError) as exc:
        detail = str(exc)
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write(f"\nVerification failed: {detail}\n")
        return Verification(False, detail, str(log_path))
