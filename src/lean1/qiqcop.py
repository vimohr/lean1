"""Select unsolved problems from the QIQCOP Zoo (https://qiqc-op.com/).

The site publishes a machine-readable index at api/index.json and one record
per problem at api/problems/<id>.json. Selection uses only these documents;
the site's robots.txt excludes the /random/ pages.
"""

import json
import os
import random
import re
import time
from dataclasses import dataclass
from importlib.resources import files
from typing import Any, Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from . import __version__
from .common import RunnerError
from .issues import EXIT_PROBLEM_SOURCE, SupervisorIssue

DEFAULT_SITE = "https://qiqc-op.com/"
SITE_ENVIRONMENT = "LEAN1_QIQCOP_SITE"
PROBLEM_ID = re.compile(r"op_[0-9a-f]{16}\Z")
MAX_DOCUMENT = 32 * 1024 * 1024
USER_AGENT = f"lean1/{__version__} (+https://github.com/vimohr/lean1)"
FETCH_ATTEMPTS = 4
SELECTION_ATTEMPTS = 5


class ProblemSourceError(SupervisorIssue):
    """The problem website could not supply a valid unsolved problem."""

    def __init__(self, message: str, detail: str = "") -> None:
        super().__init__(message, kind="problem_source", exit_code=EXIT_PROBLEM_SOURCE, detail=detail)


@dataclass(frozen=True)
class ZooProblem:
    id: str
    title: str
    status: str
    site: str = DEFAULT_SITE

    @property
    def url(self) -> str:
        return urljoin(self.site, f"problem/{self.id}/")

    @property
    def json_url(self) -> str:
        return urljoin(self.site, f"api/problems/{self.id}.json")

    @property
    def unsolved(self) -> bool:
        return self.status.strip().lower() == "unsolved"


def site_url(value: str | None = None) -> str:
    site = (value or os.environ.get(SITE_ENVIRONMENT) or DEFAULT_SITE).strip()
    if not re.match(r"https?://[^/\s]+", site):
        raise RunnerError(f"The problem site must be an http(s) URL, not {site!r}.")
    return site if site.endswith("/") else site + "/"


def standard_problem_text(problem: ZooProblem) -> str:
    """Render the bundled one-line standard problem statement."""
    template = files("lean1").joinpath("standard_problem.txt").read_text(encoding="utf-8")
    if template.count("{{PROBLEM_URL}}") != 1:
        raise RunnerError("standard_problem.txt must contain exactly one {{PROBLEM_URL}} placeholder.")
    return template.replace("{{PROBLEM_URL}}", problem.url).strip()


def _title(value: object) -> str:
    if isinstance(value, dict):
        value = value.get("text") or value.get("html") or value.get("tex")
    return value.strip() if isinstance(value, str) else ""


Opener = Callable[..., Any]


class ZooClient:
    """Fetch and validate QIQCOP documents, retrying transient failures."""

    def __init__(
        self, site: str | None = None, *, opener: Opener = urlopen,
        sleep: Callable[[float], None] = time.sleep, attempts: int = FETCH_ATTEMPTS,
        timeout: float = 30, backoff: float = 2.0,
    ) -> None:
        self.site = site_url(site)
        self.opener, self.sleep = opener, sleep
        self.attempts, self.timeout, self.backoff = attempts, timeout, backoff

    def _get(self, url: str, accept: str) -> bytes:
        failures: list[str] = []
        for attempt in range(1, self.attempts + 1):
            try:
                request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
                with self.opener(request, timeout=self.timeout) as response:
                    data = response.read(MAX_DOCUMENT + 1)
                if len(data) > MAX_DOCUMENT:
                    raise ProblemSourceError(f"{url} exceeds {MAX_DOCUMENT} bytes.")
                return data
            except HTTPError as exc:
                failures.append(f"attempt {attempt}: HTTP {exc.code} {exc.reason}")
                # A missing document will not appear by retrying.
                if exc.code in {400, 401, 403, 404, 410}:
                    break
            except (URLError, OSError, ValueError) as exc:
                failures.append(f"attempt {attempt}: {exc}")
            if attempt < self.attempts:
                self.sleep(self.backoff * 2 ** (attempt - 1))
        raise ProblemSourceError(f"could not download {url}", "\n".join(failures))

    def get_json(self, url: str) -> object:
        data = self._get(url, "application/json")
        try:
            return json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProblemSourceError(f"{url} did not return valid JSON: {exc}") from exc

    def index(self) -> list[ZooProblem]:
        url = urljoin(self.site, "api/index.json")
        document = self.get_json(url)
        if not isinstance(document, dict) or not isinstance(document.get("problems"), list):
            raise ProblemSourceError(f"{url} has no 'problems' list; the site format may have changed.")
        problems: list[ZooProblem] = []
        seen: set[str] = set()
        for entry in document["problems"]:
            if not isinstance(entry, dict):
                raise ProblemSourceError(f"{url} contains a malformed problem entry.")
            identifier, status = entry.get("id"), entry.get("status")
            if not isinstance(identifier, str) or not PROBLEM_ID.fullmatch(identifier):
                raise ProblemSourceError(f"{url} contains an invalid problem id: {identifier!r}.")
            if not isinstance(status, str) or not status.strip():
                raise ProblemSourceError(f"{url} gives no status for {identifier}.")
            if identifier in seen:
                continue
            seen.add(identifier)
            problems.append(ZooProblem(identifier, _title(entry.get("title")) or identifier, status.strip(), self.site))
        counts = document.get("counts")
        expected = counts.get("unsolved") if isinstance(counts, dict) else None
        found = sum(problem.unsolved for problem in problems)
        if isinstance(expected, int) and expected != found:
            raise ProblemSourceError(
                f"{url} reports {expected} unsolved problems but lists {found}; the index may be incomplete."
            )
        return problems

    def record(self, problem: ZooProblem) -> dict[str, Any]:
        record = self.get_json(problem.json_url)
        if not isinstance(record, dict):
            raise ProblemSourceError(f"{problem.json_url} is not a JSON object.")
        identifiers = {record.get("id"), *(record.get("aliases") or [])}
        if problem.id not in identifiers:
            raise ProblemSourceError(f"{problem.json_url} describes {record.get('id')!r}, not {problem.id}.")
        return record

    def check_page(self, problem: ZooProblem) -> None:
        page = self._get(problem.url, "text/html")
        if problem.id.encode() not in page:
            raise ProblemSourceError(f"{problem.url} does not mention {problem.id}.")


def pick_unsolved(
    client: ZooClient, exclude: Iterable[str] = (), rng: random.Random | None = None,
) -> tuple[ZooProblem, dict[str, Any]]:
    """Pick a uniformly random unsolved problem, confirmed by its own record and page."""
    rng = rng or random.SystemRandom()
    excluded = set(exclude)
    candidates = [problem for problem in client.index() if problem.unsolved and problem.id not in excluded]
    rejected: list[str] = []
    for _ in range(SELECTION_ATTEMPTS):
        if not candidates:
            break
        problem = candidates.pop(rng.randrange(len(candidates)))
        record = client.record(problem)
        status = record.get("status")
        # The index and the record can briefly disagree while the site updates.
        if not isinstance(status, str) or status.strip().lower() != "unsolved":
            rejected.append(f"{problem.id}: index says Unsolved, record says {status!r}")
            continue
        client.check_page(problem)
        title = _title(record.get("title")) or problem.title
        return ZooProblem(problem.id, title, status.strip(), problem.site), record
    if rejected:
        raise ProblemSourceError("could not confirm an unsolved problem", "\n".join(rejected))
    raise ProblemSourceError(
        f"no unsolved problems remain on {client.site} that this campaign has not attempted"
        if excluded else f"{client.site} lists no unsolved problems"
    )
