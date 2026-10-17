"""What an `upgrade --apply` wrote, by digest (upgrade-output-needs-lease-to-commit).

The commit hook refuses a staged path no lease covers. An upgrade rewrites files the hook
does not count as ddflow's own (`.claude/settings.json`, ...), and the agent that ran it holds
no lease on them. The apply therefore records, in its closing `upgrade.applied` event (done or failed), the
SHA-256 of every file whose bytes it changed or created (`written`, repo-relative path ->
digest). The hook waves a staged path through when the bytes being committed are exactly
those: the upgrade's own output, as written. Any later edit has another digest and needs a
lease like any other change.

The record is the agent's own word, not a signature: an agent that appends a forged event
can exempt a file of its choosing. That is no new reach. The lease guard is a protection
against honest collisions between agents, and anything that can append to the log can already
set `[enforce].commit_without_lease` to "off" in `.ddflow/config.toml`, which a commit needs
no lease to carry.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

from ..core.digest import hasher
from ..infra import git as G


def digest(data: bytes) -> str:
    return hasher(data).hexdigest()


def _rel(repo: Path, path: Path) -> str | None:
    try:
        return path.resolve().relative_to(Path(repo).resolve()).as_posix()
    except (OSError, ValueError):
        return None  # outside the repository: not a path a commit of it can carry


def _stored_id(repo: Path, rel: str) -> str | None:
    """The blob id git would store for ``rel`` (clean filters and eol conversion applied), or
    None when git cannot say."""
    r = G.run(repo, "hash-object", "--path", rel, "--", rel, timeout=30)
    out = r.out.strip()
    return out if r.ok and out else None


def digests(repo: Path, paths: Iterable[Path]) -> dict[str, str | None]:
    """``{repo-relative path: digest, or None when it is absent}``. The digest is the blob id git
    would store for the file (so a clean filter or autocrlf, which change the bytes on `git add`,
    do not make the output look edited); the SHA-256 of the bytes when git cannot say."""
    out: dict[str, str | None] = {}
    for p in paths:
        rel = _rel(repo, p)
        if rel is None:
            continue
        try:
            out[rel] = (_stored_id(repo, rel) or digest(p.read_bytes())) if p.is_file() else None
        except OSError:
            out[rel] = None
    return out


def written(repo: Path, before: Mapping[str, str | None]) -> dict[str, str]:
    """The files named in ``before`` (path -> digest then) that exist now with other bytes
    (absent counts as different), with their digests now."""
    now = digests(repo, [Path(repo) / rel for rel in before])
    return {rel: d for rel, d in sorted(now.items()) if d is not None and before.get(rel) != d}
