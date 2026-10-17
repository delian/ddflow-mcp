"""What an `upgrade --apply` wrote, by digest (upgrade-output-needs-lease-to-commit).

The commit hook refuses a staged path no lease covers. An upgrade rewrites files the hook
does not count as ddflow's own (`.claude/settings.json`, ...), and the agent that ran it holds
no lease on them. The apply therefore records, in its closing `upgrade.applied` event, the
SHA-256 of every file whose bytes it changed or created (`written`, repo-relative path ->
digest). The hook waves a staged path through when the bytes being committed are exactly
those: the upgrade's own output, as written. Any later edit has another digest and needs a
lease like any other change.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from pathlib import Path


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _rel(repo: Path, path: Path) -> str | None:
    try:
        return path.resolve().relative_to(Path(repo).resolve()).as_posix()
    except (OSError, ValueError):
        return None  # outside the repository: not a path a commit of it can carry


def digests(repo: Path, paths: Iterable[Path]) -> dict[str, str | None]:
    """``{repo-relative path: digest of its bytes, or None when it is absent}``."""
    out: dict[str, str | None] = {}
    for p in paths:
        rel = _rel(repo, p)
        if rel is None:
            continue
        try:
            out[rel] = digest(p.read_bytes()) if p.is_file() else None
        except OSError:
            out[rel] = None
    return out


def written(repo: Path, before: Mapping[str, str | None], paths: Iterable[Path]) -> dict[str, str]:
    """The files among ``paths`` that exist now and differ from ``before`` (absent counts as
    different), with their digests."""
    now = digests(repo, paths)
    return {rel: d for rel, d in sorted(now.items()) if d is not None and before.get(rel) != d}
