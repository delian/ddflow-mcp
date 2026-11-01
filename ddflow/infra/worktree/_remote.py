"""Remotes and tags (RESEARCH R16), and pushing/3-way-applying."""

from __future__ import annotations

from pathlib import Path

from ...core.flow import parse_version
from .. import git as G
from .. import worktree as _W


def push(path: Path, remote: str, branch: str, *, timeout: int = 300) -> G.GitResult:
    """Push ``branch`` and set its upstream. Never ``--force``.

    A rejected push means someone else moved the remote branch -- a reviewer's suggested
    change committed from the forge UI is the usual one. Forcing would discard it, so
    the rejection is returned for the agent to `git pull` and retry.
    """
    return _W.git(path, "push", "--set-upstream", remote, f"{branch}:{branch}", timeout=timeout)


def fetch(repo: Path, remote: str, *refs: str) -> G.GitResult:
    return _W.git(repo, "fetch", "--quiet", remote, *refs)


def rev(repo: Path, ref: str) -> str:
    r = _W.git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    return r.out if r.ok else ""


def version_tags(repo: Path, prefix: str) -> list[tuple[str, str]]:
    """``(tag, version)`` for every tag ``<prefix>MAJOR.MINOR.PATCH``, newest version first."""
    r = _W.git(repo, "tag", "--list", f"{prefix}*")
    found = []
    for line in r.out.splitlines() if r.ok else []:
        name = line.strip()
        ver = name[len(prefix) :]
        parsed = parse_version(ver)
        if parsed is not None:
            found.append((parsed, name, ver))
    return [(name, ver) for _, name, ver in sorted(found, reverse=True)]


def reachable_tag(repo: Path, prefix: str, ref: str) -> tuple[str, str]:
    """The highest version tag reachable from ``ref`` -- the version that ref is AT.

    Not simply the highest tag in the repo: under gitflow a hotfix tag on production is
    not on develop until it is back-merged, and a version computed from a tag the branch
    does not contain would count the same commits twice.
    """
    for tag, ver in version_tags(repo, prefix):
        if _W.git(repo, "merge-base", "--is-ancestor", tag, ref).ok:
            return tag, ver
    return "", ""


def log_messages(repo: Path, since: str, ref: str) -> list[str]:
    """Full commit messages in ``since..ref`` (all of ``ref`` when ``since`` is empty).

    ``--no-merges``: a merge commit carries no change of its own, and counting one made
    every gitflow release look unreleased -- the back-merge of the tag into develop is a
    commit after the tag, so develop always had "one commit to release".
    """
    rng = f"{since}..{ref}" if since else ref
    r = _W.git(repo, "log", "--no-merges", "--format=%B%x00", rng)
    if not r.ok:
        return []
    return [m.strip() for m in r.out.split("\x00") if m.strip()]


def tag(repo: Path, name: str, ref: str, message: str) -> G.GitResult:
    """An ANNOTATED tag: it records who and when, and `git describe` sees it by default."""
    return _W.git(repo, "tag", "--annotate", name, "-m", message, ref)


def apply_3way(tree: Path, patch: str) -> G.GitResult:
    """`git apply --3way` a patch into ``tree``'s index and files.

    Three-way, so a hunk that does not apply cleanly becomes a conflict in the file --
    something an agent can resolve -- rather than a rejected patch it has to redo by hand.
    """
    return _W.git(tree, "apply", "--3way", "--index", "-", input=patch)
