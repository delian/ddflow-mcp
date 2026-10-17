"""What `ddflow pins regenerate` wrote, by digest (bug B1579b8a1a9).

A pin file is regenerated, never leased or hand-merged, but committing the regenerated file
needed a lease on it, and another item usually held one. As the upgrade does
(`services.upgrade_written`), a regeneration records the SHA-256 of every file whose bytes it
changed or created; the commit hook waves a staged path through when the bytes being committed
are exactly those. Any later edit has another digest and needs a lease like any other change.

The record is machine-local (`.ddflow/local/pins-written.json`, git-ignored), the regenerating
agent's own word, not a signature: the lease guard protects against honest collisions, and
anything that can write there can already set `[enforce].commit_without_lease` to "off".
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from ..infra.fsio import atomic_write
from . import upgrade_written as UW

FILE = Path(".ddflow") / "local" / "pins-written.json"
#: Digests kept per path: a path regenerated again keeps its last few, never an unbounded list.
KEEP = 8


def snapshot(repo: Path, files: Iterable[str]) -> dict[str, str | None]:
    """``{repo-relative file: digest or None}`` for every file under ``files`` (a directory
    is walked); the "before" of a regeneration."""
    root = Path(repo)
    paths: list[Path] = []
    for f in files:
        p = root / f
        paths += sorted(q for q in p.rglob("*") if q.is_file()) if p.is_dir() else [p]
    return UW.digests(repo, paths)


def written(repo: Path, before: dict[str, str | None], files: Iterable[str]) -> dict[str, str]:
    """The files that now hold other bytes than in ``before`` (or are new), with their digests."""
    now = snapshot(repo, files)
    return {rel: d for rel, d in sorted(now.items()) if d is not None and before.get(rel) != d}


def load(repo: Path) -> dict[str, set[str]]:
    """``{path: digests}`` recorded so far; empty when there is no record or it is unreadable."""
    try:
        raw = json.loads((Path(repo) / FILE).read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        k: {d for d in v if isinstance(d, str)}
        for k, v in raw.items()
        if isinstance(k, str) and isinstance(v, list)
    }


def record(repo: Path, wrote: dict[str, str]) -> None:
    """Add ``wrote`` (path -> digest) to the machine-local record."""
    if not wrote:
        return
    path = Path(repo) / FILE
    try:
        raw = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        raw = {}
    raw = raw if isinstance(raw, dict) else {}
    for rel, digest in wrote.items():
        had = [d for d in raw.get(rel, []) if isinstance(d, str) and d != digest]
        raw[rel] = [*had[-(KEEP - 1) :], digest]
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(raw, indent=1, sort_keys=True) + "\n")
