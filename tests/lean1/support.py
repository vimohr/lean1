"""Shared stand-ins for Codex, Lean, Lake, mail, and the QIQCOP website.

No real agents are started and no email is sent. The fake executables record
their invocations in $LEAN1_TEST_ROOT/commands.jsonl.
"""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

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


def counter(label):
    path = root / f"{label}-count"
    count = int(path.read_text()) + 1 if path.exists() else 1
    path.write_text(str(count))
    return count


def message(text):
    print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": text}}))


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
    if "LEAN1_HEALTH_CHECK_OK" in prompt:
        count = counter("health")
        health = os.environ.get("LEAN1_TEST_HEALTH", "ok")
        failing_from = int(os.environ.get("LEAN1_TEST_HEALTH_FAIL_FROM", "1"))
        if health == "fail" and count >= failing_from:
            print(json.dumps({"type": "error", "message": "Codex usage limit reached."}))
            print("health check failed", file=sys.stderr)
            sys.exit(1)
        message("LEAN1_HEALTH_CHECK_OK")
        sys.exit(0)
    if "AUDIT_VERDICT" in prompt:
        count = counter("audit")
        (root / f"audit-prompt-{count}.txt").write_text(prompt)
        mode = os.environ.get("LEAN1_TEST_AUDIT", "pass")
        if mode == "tamper":
            Path("release/README.md").write_text("# Edited by the auditor\n")
        if mode == "garbage":
            message("I could not decide.")
            sys.exit(0)
        verdict = {"revise-once": "revise" if count == 1 else "pass"}.get(mode, mode)
        report = re.search(r"Write your verdict to (\S+) as a JSON object", prompt).group(1)
        problem_id = re.search(r"Problem ID: (op_[0-9a-f]+)", prompt).group(1)
        Path(report).parent.mkdir(parents=True, exist_ok=True)
        Path(report).write_text(json.dumps({
            "problem_id": problem_id, "verdict": "pass" if verdict == "tamper" else verdict,
            "summary": "Checked attribution, references, and prior work.",
            "issues": [] if verdict in {"pass", "tamper"} else ["Lemma 2 is due to Doe (2025) but is not cited."],
            "prior_work": ["Doe, J. A full answer. arXiv:2501.00001 (2025)."] if verdict != "pass" else [],
            "searches": ["arXiv: problem title"],
        }))
        message("Audit complete.\nAUDIT_VERDICT: " + ("PASS" if verdict == "tamper" else verdict.upper()))
        sys.exit(0)
    if "REVIEW_VERDICT" in prompt:
        count = counter("review")
        (root / f"review-prompt-{count}.txt").write_text(prompt)
        mode = os.environ.get("LEAN1_TEST_REVIEW", "accept")
        if mode == "crash":
            print("review crashed", file=sys.stderr)
            sys.exit(3)
        if mode == "tamper":
            Path("Solution.lean").write_text(Path("Solution.lean").read_text() + "\n-- reviewer edit\n")
        report = re.search(r"Write your verdict to (\S+) as a JSON object", prompt).group(1)
        problem_id = re.search(r"Problem ID: (op_[0-9a-f]+)", prompt).group(1)
        accept = mode in {"accept", "tamper"} or mode == "reject-once" and count > 1
        if mode == "garbage":
            message("I could not decide.")
            sys.exit(0)
        Path(report).parent.mkdir(parents=True, exist_ok=True)
        Path(report).write_text(json.dumps({
            "problem_id": problem_id, "verdict": "accept" if accept else "reject",
            "summary": "The theorem matches the website statement." if accept else "The theorem proves only a special case.",
            "issues": [] if accept else ["Solution.answer assumes n = 1; the website asks for every n."],
        }))
        message("Review complete.\nREVIEW_VERDICT: " + ("ACCEPT" if accept else "REJECT"))
        sys.exit(0)
    if "PUBLICATION_STATUS" in prompt:
        count = counter("publish")
        (root / f"publish-prompt-{count}.txt").write_text(prompt)
        mode = os.environ.get("LEAN1_TEST_PUBLISH", "ok")
        release = Path("release")
        files = {
            "README.md": "# Result\n", "LICENSE": "MIT\n", "RIGHTS.txt": "MIT\n",
            "manuscript.tex": "\\documentclass{article}\n", "references.bib": "@misc{zoo}\n",
            "VERIFY.md": "verify\n", "HUMAN-CHECK.md": "check\n", "PROVENANCE.md": "agents\n",
            "REVIEW.txt": "accepted\n", "RELEASE_NOTES.md": "v1.0.0\n", "CITATION.cff": "cff-version: 1.2.0\n",
            ".zenodo.json": json.dumps({"title": "T", "creators": [{"name": "A"}], "description": "D", "license": "mit"}),
            ".gitignore": ".lake/\n", "verify_artifact.py": "print('PASS')\n",
            "artifact/source_manifest.json": "{}", "artifact/ReadStatement.lean": "#check Solution.answer\n",
            "artifact/ProofAudit.lean": "#print axioms Solution.answer\n", ".github/workflows/lean.yml": "name: Lean\n",
            "release-manifest.json": "{}", "artifact/lean/lean-toolchain": "leanprover/lean4:v4.19.0\n",
            "artifact/lean/Solution.lean": Path("Solution.lean").read_text(),
            "verification/2026-01-01-release/result.json": json.dumps({"status": "PASS"}),
        }
        for relative, content in files.items():
            path = release / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        if mode != "incomplete" and not (mode == "incomplete-once" and count == 1):
            (release / "manuscript.pdf").write_bytes(b"%PDF-1.5\n%fake\n")
        git = ["git", "-C", str(release), "-c", "user.name=Test", "-c", "user.email=test@example.org"]
        subprocess.run(git + ["init", "-q", "-b", "main"], check=True)
        subprocess.run(git + ["add", "-A"], check=True)
        subprocess.run(git + ["commit", "-q", "--allow-empty", "-m", "feat: initial release"], check=True)
        message("Package prepared.\nPUBLICATION_STATUS: READY")
        sys.exit(0)
    count_file = Path("attempt-count")
    count = int(count_file.read_text()) + 1 if count_file.exists() else 1
    count_file.write_text(str(count))
    (root / f"prompt-{count}.txt").write_text(prompt)
    with (root / "research-workspaces.txt").open("a") as stream:
        stream.write(os.getcwd() + "\n")
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
    if mode == "prior-solved":
        Path("result.md").write_text("## Prior work\n\nPrior resolution: COMPLETE\n\nDoe, J. A full answer. arXiv:2501.00001.\n")
        message("An existing paper resolves the problem.")
        sys.exit(0)
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
    message(final)
    print(json.dumps({"type": "turn.completed"}))
    sys.exit(7 if mode == "bad-exit" else 0)
sys.exit(99)
'''


def install_fake_tools(directory: Path, names: tuple[str, ...] = ("codex", "lean", "lake", "mail")) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        path = directory / name
        path.write_text(f"#!{sys.executable}\n" + FAKE_TOOLS)
        path.chmod(0o755)


def problem_id(number: int) -> str:
    return f"op_{number:016x}"


class FakeZoo:
    """A local HTTP stand-in for qiqc-op.com's JSON API and problem pages."""

    def __init__(self, statuses: dict[str, str]) -> None:
        self.statuses = dict(statuses)
        self.record_statuses: dict[str, str] = {}
        self.failures: dict[str, int] = {}
        self.requests: list[str] = []
        zoo = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: object) -> None:
                pass

            def do_GET(self) -> None:
                zoo.requests.append(self.path)
                status, body, media = zoo.respond(self.path)
                self.send_response(status)
                self.send_header("Content-Type", media)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def site(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/"

    def __enter__(self) -> "FakeZoo":
        self.thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.server.shutdown()
        self.server.server_close()

    def index(self) -> dict[str, object]:
        problems = [
            {"id": identifier, "title": f"Problem {identifier}", "status": status,
             "url": f"{self.site}problem/{identifier}/"}
            for identifier, status in self.statuses.items()
        ]
        unsolved = sum(status == "Unsolved" for status in self.statuses.values())
        return {"counts": {"total": len(problems), "unsolved": unsolved}, "problems": problems}

    def respond(self, path: str) -> tuple[int, bytes, str]:
        for prefix, remaining in list(self.failures.items()):
            if path.startswith(prefix) and remaining:
                self.failures[prefix] = remaining - 1
                return 503, b"temporarily unavailable", "text/plain"
        if path == "/api/index.json":
            return 200, json.dumps(self.index()).encode(), "application/json"
        for identifier, status in self.statuses.items():
            if path == f"/api/problems/{identifier}.json":
                record = {
                    "id": identifier, "aliases": [identifier], "status": self.record_statuses.get(identifier, status),
                    "title": {"text": f"Problem {identifier}"}, "statement": {"text": "Prove the claim."},
                }
                return 200, json.dumps(record).encode(), "application/json"
            if path == f"/problem/{identifier}/":
                return 200, f"<html><body>{identifier}</body></html>".encode(), "text/html"
        return 404, b"not found", "text/plain"
