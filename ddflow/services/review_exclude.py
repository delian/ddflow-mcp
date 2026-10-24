"""Files a reviewer is never sent (`[review].exclude_globs`): generated and pinned text.

A re-pinned golden transcript turned a 3 KB knob change into an 11-chunk review. The
files `pins.toml` owns are regenerated, never read, so the diff the reviewers get leaves
them out -- and the output says how many files and bytes it left out, so a skipped file
is never silent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core import globs, unidiff
from . import pins

#: How many excluded paths the one-line summary names.
NAMED = 5


@dataclass
class Excluded:
    """What was left out of a diff: (path, bytes) per file."""

    files: list[tuple[str, int]] = field(default_factory=list)

    @property
    def bytes(self) -> int:
        return sum(n for _p, n in self.files)

    def line(self) -> str:
        names = ", ".join(p for p, _n in self.files[:NAMED])
        more = f" and {len(self.files) - NAMED} more" if len(self.files) > NAMED else ""
        return (
            f"excluded from review ([review].exclude_globs / pin files): {len(self.files)} "
            f"file(s), {self.bytes} bytes -- {names}{more}"
        )

    def empty_note(self) -> str:
        return f" after leaving out {len(self.files)} excluded file(s)" if self.files else ""

    def evidence(self) -> dict[str, Any]:
        return {
            "excluded_files": len(self.files),
            "excluded_bytes": self.bytes,
            "excluded_paths": [p for p, _n in self.files],
        }


TAG = "review-exclude:"
NONE = "none"


def patterns(
    cfg: Any,
    repo: Path,
    tags: list[str] | None = None,
    exclude: str = "",
    include_pinned: bool = False,
) -> tuple[list[str], list[str]]:
    """(globs, pinned paths) for one review. The globs come from, in order: this run's
    ``exclude`` (comma-separated; ``none`` leaves out nothing), the item's
    ``review-exclude:<glob>`` tags, else `[review].exclude_globs`. The pinned paths are
    the files `pins.toml` owns, unless `[review].exclude_pin_files` is off or this run
    asked for ``include_pinned``."""
    pinned = []
    if cfg.review.exclude_pin_files and not include_pinned:
        pinned = pins.pin_files(repo, cfg)
    if exclude.strip():
        given = [g.strip() for g in exclude.split(",") if g.strip()]
        return ([] if given == [NONE] else given), pinned
    tagged = [t[len(TAG) :] for t in tags or [] if t.startswith(TAG) and t[len(TAG) :]]
    return (tagged or list(cfg.review.exclude_globs or [])), pinned


def _hit(path: str, pats: list[str], pinned: list[str]) -> bool:
    if any(globs.match(path, g, bare_any_depth=True) for g in pats):
        return True
    # A pin file is an exact path (or a directory), never an any-depth pattern.
    return any(path == f or (f.endswith("/") and path.startswith(f)) for f in pinned)


def excluded_outright(
    cfg: Any, repo: Path, paths: list[str], tags=None, exclude="", include_pinned=False
) -> bool:
    """Would `[review]` exclusion drop EVERY path in ``paths``? True only for a non-empty
    list, judged with the SAME inputs as the review that governs them (``tags``, an ad-hoc
    ``exclude``, ``include_pinned`` -- for a plain `ddflow review` run those are the
    defaults, and `apply` passes the run's own): a change made entirely of excluded files
    (a lockfile-only dependency refresh) has nothing a reviewer is ever sent, so the
    review gates record UNAVAILABLE naming them and `complete` does not demand an
    independent reviewer (which would mean sending content the project excluded) --
    B-lockfile-only-item-cannot-complete."""
    if not paths:
        return False
    pats, pinned = patterns(cfg, repo, tags, exclude, include_pinned)
    if not pats and not pinned:
        return False
    return all(_hit(p, pats, pinned) for p in paths)


def filter_diff(
    diff: str, pats: list[str], pinned: list[str] | None = None
) -> tuple[str, Excluded]:
    """``diff`` without the sections whose file matches a pattern, and what was dropped."""
    gone = Excluded()
    pinned = pinned or []
    if not pats and not pinned:
        return diff, gone
    kept: list[str] = []
    for section in unidiff.sections(diff):
        fd = unidiff.parse_section(section)
        # A rename out of a pinned tree still has a side worth reading: only a section
        # whose every path is excluded goes.
        if all(_hit(p, pats, pinned) for p in fd.paths):
            gone.files.append((fd.path, len(section.encode("utf-8", "replace"))))
        else:
            kept.append(section)
    if not gone.files:
        return diff, gone
    return "".join(kept), gone


def apply(diff, cfg, repo, say, evidence, tags=None, exclude="", include_pinned=False):
    """(diff, evidence, gone): the diff reviewers get, the evidence with what was left out,
    and the exclusions; one line says so when anything was.

    The exclusion is NEVER relaxed to keep a review possible: a diff it empties entirely
    is still dropped (its content may be a secret the project excluded, not noise), and
    the caller records the review UNAVAILABLE naming the files (B-lockfile-only-item-
    cannot-complete)."""
    if not diff.strip():
        return diff, evidence, Excluded()
    kept, gone = filter_diff(diff, *patterns(cfg, repo, tags, exclude, include_pinned))
    if gone.files:
        say(gone.line())
        evidence = {**(evidence or {}), **gone.evidence()}
    return kept, evidence, gone
