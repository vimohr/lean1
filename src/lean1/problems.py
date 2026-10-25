"""Normalize problem inputs and save copies that survive agent restarts."""

import hashlib
import shutil
import sys
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from . import __version__
from .common import RunnerError, atomic_write, write_json

MAX_DOWNLOAD = 10 * 1024 * 1024
MAX_INLINE_TEXT = 100_000


@dataclass(frozen=True)
class Problem:
    kind: str
    source: str
    content: str = ""


def is_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)


def resolve_problem(value: str, kind: str = "auto") -> Problem:
    if not value.strip():
        raise RunnerError("The problem must not be empty.")
    if kind == "auto":
        if is_url(value):
            kind = "url"
        elif value.startswith("@"):
            kind, value = "file", value[1:]
        else:
            try:
                file_exists = Path(value).expanduser().is_file()
            except (OSError, ValueError):
                file_exists = False
            if file_exists or value.startswith(("./", "../", "/", "~/")):
                kind = "file"
            else:
                kind = "text"
    if kind == "url":
        if not is_url(value):
            raise RunnerError("Problem URLs must be absolute http:// or https:// links.")
        return Problem("url", value.strip())
    if kind == "file":
        if value == "-":
            return resolve_problem(sys.stdin.read(), "text")
        path = Path(value).expanduser().resolve()
        if not path.is_file():
            raise RunnerError(f"Problem file not found: {path}")
        return Problem("file", str(path))
    return Problem("text", "command line", value.strip())


class PageText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "template"}:
            self.hidden += 1
        elif tag in {"p", "div", "br", "li", "h1", "h2", "h3", "tr", "pre"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "template"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)

    def text(self) -> str:
        return "\n".join(
            line.strip() for line in "".join(self.parts).splitlines() if line.strip()
        )


def inline_text(data: bytes, encoding: str = "utf-8") -> str:
    try:
        decoded = data.decode(encoding)
    except (UnicodeError, LookupError):
        return ""
    if "\x00" in decoded:
        return ""
    if len(decoded) > MAX_INLINE_TEXT:
        return decoded[:MAX_INLINE_TEXT] + "\n[Excerpt only; read the complete saved file.]"
    return decoded


def prepare_problem(problem: Problem, workdir: Path) -> str:
    inputs = workdir / ".lean1" / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    details: dict[str, object] = {"kind": problem.kind, "source": problem.source}
    if problem.kind == "text":
        atomic_write(inputs / "problem.txt", problem.content + "\n")
        content = problem.content
        rendered = f"Problem supplied as text:\n\n{content}"
    elif problem.kind == "file":
        source = Path(problem.source)
        destination = inputs / source.name
        if source != destination:
            shutil.copy2(source, destination)
        # A PDF or other large attachment need not be loaded into memory just
        # to include a short text excerpt and record its identity.
        with destination.open("rb") as stream:
            data = stream.read(MAX_INLINE_TEXT * 4)
        content = inline_text(data)
        rendered = (
            f"Problem supplied in a file. Original path: {source}\n"
            f"Read the complete local copy: {destination.relative_to(workdir)}\n\n"
            + (content or "This is a binary document; inspect the saved file with suitable tools.")
        )
        with destination.open("rb") as stream:
            details["sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
    else:
        rendered = (
            f"Problem website: {problem.source}\n"
            "Open the original link, identify the exact problem and its background, "
            "and preserve mathematical notation. A page excerpt may omit equations, "
            "images, attachments, or content rendered by JavaScript."
        )
        try:
            request = Request(problem.source, headers={"User-Agent": f"lean1/{__version__}"})
            with urlopen(request, timeout=30) as response:
                data = response.read(MAX_DOWNLOAD + 1)
                media_type = response.headers.get_content_type()
                encoding = response.headers.get_content_charset() or "utf-8"
                details["resolved_url"] = response.geturl()
            if len(data) > MAX_DOWNLOAD:
                raise RunnerError("Website download exceeds 10 MiB; ask the agent to open the link.")
            suffix = ".html" if media_type == "text/html" else ".pdf" if media_type == "application/pdf" else ".txt"
            snapshot = inputs / f"website{suffix}"
            snapshot.write_bytes(data)
            details["snapshot"] = str(snapshot.relative_to(workdir))
            details["sha256"] = hashlib.sha256(data).hexdigest()
            content = inline_text(data, encoding)
            if media_type == "text/html":
                parser = PageText()
                parser.feed(data.decode(encoding, errors="replace"))
                content = parser.text()[:MAX_INLINE_TEXT]
            rendered += f"\nSaved page/document: {snapshot.relative_to(workdir)}"
            if content:
                rendered += "\n\nPage excerpt (consult the original for context):\n\n" + content
        except (OSError, URLError, RunnerError, ValueError, LookupError) as exc:
            details["download_error"] = str(exc)
            rendered += f"\nPython could not save a page snapshot: {exc}\nUse browsing to open the link."
    write_json(workdir / ".lean1" / "problem.json", details)
    return rendered
