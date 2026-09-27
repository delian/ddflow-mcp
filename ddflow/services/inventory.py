"""B20: inventory ratchets, not count ratchets.

A lesson may declare the PATTERN it forbids and the SITES where that pattern currently
occurs. Re-scanning later and diffing the two says *which* sites appeared — not how many.

The failure this replaces, from the project ddflow was extracted from: a count-based clone
ratchet sat red for roughly 350 commits. It was advisory, so it never blocked; it reported a
number, so every reader learned to skip it; and **24 new clones arrived through that gap**.

    "A count says 'worse' and never 'which'."

A number cannot be acted on and cannot be reviewed. A list can: a new entry is a specific
line somebody can open, and a disappeared entry is progress the inventory should record. So
the stored artefact is the list, and the list may only shrink.

**Site identity deliberately excludes the line number.** `path:line` churns on every edit
above a site, which would manufacture a matched pair of "new site" and "fixed site" findings
out of an unrelated change — and a ratchet that cries wolf is one that gets turned off,
which is the very failure mode this exists to correct. A site is therefore
``<path>: <matched text>``, which is stable under edits elsewhere and readable without
tooling. Two identical matches in one file collapse to one site; that is the accepted cost.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path

from ..core.model import Lesson
from ..infra import worktree as W

#: How much of a matched line becomes part of the site's identity. Long enough to tell two
#: occurrences apart, short enough that reflowing a line does not read as a new site.
SITE_TEXT_CHARS = 60

#: Files never scanned, whatever the globs say. Binary and vendored trees produce matches
#: nobody will act on, and a ratchet full of noise is one nobody reads.
SKIP_DIRS = (".git", ".ddflow", "node_modules", ".venv", "venv", "__pycache__", "dist", "build")

#: How many new sites are named inline before the rest are summarised. A finding that prints
#: forty paths is one nobody reads to the end, and the point is that each one is actionable.
SITES_SHOWN = 5


class PatternError(ValueError):
    """The lesson's pattern is not a usable regex."""


@dataclass
class SiteDiff:
    """What changed since the lesson recorded its inventory."""

    lesson: str
    appeared: list[str] = field(default_factory=list)
    gone: list[str] = field(default_factory=list)
    unchanged: int = 0

    @property
    def regressed(self) -> bool:
        return bool(self.appeared)

    def render(self) -> str:
        parts = []
        if self.appeared:
            parts.append(
                f"{len(self.appeared)} NEW site(s): " + "; ".join(self.appeared[:SITES_SHOWN])
            )
            if len(self.appeared) > SITES_SHOWN:
                parts[-1] += f" (+{len(self.appeared) - SITES_SHOWN} more)"
        if self.gone:
            parts.append(f"{len(self.gone)} fixed — update the inventory")
        return f"{self.lesson}: " + " | ".join(parts) if parts else f"{self.lesson}: unchanged"


def _candidates(repo: Path, globs: list[str]) -> list[Path]:
    """Files to scan: git's tracked set, filtered by ``globs``.

    Tracked files rather than a walk, because a walk finds `.venv` and a build tree and the
    operator's scratch files — matches in code nobody owns. Falls back to a filtered walk
    when this is not a git repository, so the feature still works in a bare directory.
    """
    listed = W.git(repo, "ls-files", "-z")
    if listed.ok and listed.out:
        rels = [r for r in listed.out.split("\0") if r]
    else:
        rels = [
            str(p.relative_to(repo))
            for p in repo.rglob("*")
            if p.is_file() and not any(part in SKIP_DIRS for part in p.parts)
        ]
    out = []
    for rel in sorted(rels):
        if any(part in SKIP_DIRS for part in Path(rel).parts):
            continue
        if globs and not any(fnmatch(rel, g) for g in globs):
            continue
        out.append(repo / rel)
    return out


def scan(repo: Path, pattern: str, globs: list[str] | None = None) -> list[str]:
    """Every site matching ``pattern``, as ``<path>: <matched text>``.

    Raises `PatternError` on a bad regex rather than returning nothing: an inventory that is
    empty because the pattern did not compile reads exactly like one that is empty because
    the code is clean, and it would silently ratchet the real sites away.
    """
    try:
        rx = re.compile(pattern)
    except re.error as exc:
        raise PatternError(f"{pattern!r} is not a valid regex: {exc}") from exc
    sites: list[str] = []
    for path in _candidates(Path(repo), list(globs or [])):
        try:
            text = path.read_text("utf-8")
        except (OSError, UnicodeDecodeError):
            continue  # binary or unreadable: not a site anybody can act on
        rel = path.relative_to(repo).as_posix()
        for line in text.splitlines():
            if rx.search(line):
                snippet = " ".join(line.split())[:SITE_TEXT_CHARS]
                site = f"{rel}: {snippet}"
                if site not in sites:
                    sites.append(site)
    return sites


def diff(repo: Path, lesson: Lesson) -> SiteDiff | None:
    """Re-scan and compare against the lesson's recorded inventory.

    None when the lesson declares no pattern — most lessons are prose rules with nothing
    mechanical to check, and inventing a scan for them would produce findings about nothing.
    """
    if not lesson.pattern:
        return None
    found = set(scan(repo, lesson.pattern, lesson.globs))
    known = set(lesson.sites)
    return SiteDiff(
        lesson=lesson.id,
        appeared=sorted(found - known),
        gone=sorted(known - found),
        unchanged=len(found & known),
    )


def checkable(lessons: dict[str, Lesson]) -> dict[str, Lesson]:
    """Live lessons that declare a pattern — the ones a ratchet can act on.

    One definition of "checkable", so the count a caller reports and the set it checks
    cannot disagree.
    """
    return {k: v for k, v in lessons.items() if v.pattern and not v.superseded_by}


def regressions(repo: Path, lessons: dict[str, Lesson]) -> list[SiteDiff]:
    """Every lesson whose forbidden pattern has REAPPEARED somewhere new.

    Superseded lessons are skipped: a rule the project has explicitly replaced should not
    keep failing a check, which is how a stale ratchet trains readers to ignore the rest.
    """
    out = []
    # Through `checkable()`, so "live and has a pattern" is decided in ONE place. This used
    # to repeat the `superseded_by` filter inline, and once `lessons_verify` also filtered,
    # the inline copy became unreachable — a guard that cannot fail, which a mutation run
    # duly reported as vacuous. Filtering here instead means any caller may pass the raw
    # lesson map and get the same answer.
    for lesson in sorted(checkable(lessons).values(), key=lambda x: x.id):
        d = diff(repo, lesson)
        if d and d.regressed:
            out.append(d)
    return out
