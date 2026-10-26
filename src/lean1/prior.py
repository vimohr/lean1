"""Read the prior-work verdict that the research agent records at the top of result.md.

The research prompt asks for a line `Prior resolution: NONE FOUND | PARTIAL | COMPLETE`
inside a "Prior work" section. A campaign uses a COMPLETE verdict to stop work on a
problem that an existing source already resolves.
"""

import re
from dataclasses import dataclass
from pathlib import Path

RESULT_FILE = "result.md"
SECTION_LIMIT = 8000

# Tolerate Markdown decoration around the label and value, such as bold, code, or list markers.
_VERDICT = re.compile(
    r"^[\s>*_`#-]*Prior resolution[*_`]*:[\s*_`]*(NONE\s+FOUND|PARTIAL|COMPLETE)\b",
    re.IGNORECASE | re.MULTILINE,
)
_HEADING = re.compile(r"^(#{1,6})[ \t]*Prior work\b.*$", re.IGNORECASE | re.MULTILINE)


@dataclass(frozen=True)
class PriorWork:
    """The first verdict in result.md and the text of its "Prior work" section."""

    status: str
    section: str

    @property
    def solved(self) -> bool:
        return self.status == "COMPLETE"


def _section(text: str, verdict_start: int) -> str:
    heading = _HEADING.search(text, 0, verdict_start + 1)
    if heading:
        start, level = heading.start(), len(heading.group(1))
        body_start = heading.end()
    else:
        start = text.rfind("\n", 0, verdict_start) + 1
        level, body_start = 6, start
    following = re.compile(rf"^#{{1,{level}}}[ \t]", re.MULTILINE).search(text, body_start + 1)
    section = text[start:following.start() if following else len(text)].strip()
    if len(section) > SECTION_LIMIT:
        section = section[:SECTION_LIMIT].rstrip() + f"\n\n[truncated; see {RESULT_FILE}]"
    return section


def read_prior_work(workspace: Path) -> PriorWork | None:
    """Return the recorded verdict, or None if result.md is missing or has no verdict line."""
    path = workspace / RESULT_FILE
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    match = _VERDICT.search(text)
    if match is None:
        return None
    status = " ".join(match.group(1).upper().split())
    return PriorWork(status, _section(text, match.start()))


__all__ = ["PriorWork", "RESULT_FILE", "read_prior_work"]
