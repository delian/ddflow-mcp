"""The conflict probe: would an item's branch merge textually clean with main or a peer?

One question, asked of git through `infra.git` (r-adapters): ``git merge-tree --write-tree
--name-only -z A B``. Its exit code is the answer: 0 clean, 1 conflicts (the conflicted paths
follow the tree id). Never ``--quiet`` (git 2.43 answers 129 to it). Where merge-tree cannot be
used (git older than ``[locks].probe_merge_tree_min_git``, or a 129 answer) the probe falls back
to a scratch merge (`ci.merge_tree`, a throwaway worktree), which gives the same verdict slower.

A probe that could not run -- git missing, a timeout, an unknown ref, a lock held -- is
``unavailable``, never ``clean``: the commit-time check then decides (fail open to it, not to a
pass). Pure of the log: `api/probe.py` records the event.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..infra import git as G
from . import ci as CI

CLEAN, CONFLICT, UNAVAILABLE = "clean", "conflict", "unavailable"
MERGE_TREE, SCRATCH, NONE = "merge-tree", "scratch-merge", "none"
#: git's answer to an option it does not know (2.43 to `merge-tree --quiet`).
USAGE = 129

#: Said in every result text: what "clean" does and does not promise.
LIMIT = (
    "Note: clean means textually clean only; a semantic break (a function renamed on one side and "
    "used on the other) still needs the gates and ci on the merge result"
)
_VERSION = re.compile(r"(\d+(?:\.\d+)+)")
_FILES = re.compile(r"conflicting files: ([^;]*)")


@dataclass(frozen=True)
class Probe:
    result: str  # clean | conflict | unavailable
    method: str = NONE  # merge-tree | scratch-merge | none
    paths: tuple[str, ...] = ()
    item_sha: str = ""
    against_sha: str = ""
    reason: str = ""  # why unavailable, or the scratch merge's failure

    @property
    def clean(self) -> bool:
        return self.result == CLEAN


def _tuple(text: str) -> tuple[int, ...]:
    m = _VERSION.search(text)
    return tuple(int(x) for x in m.group(1).split(".")) if m else (0,)


def git_version(repo: Path | str, timeout_s: float) -> tuple[int, ...] | None:
    """The version numbers of the git on PATH; None when it cannot be asked."""
    r = G.run(repo, "--version", timeout=timeout_s)
    return _tuple(r.out) if r.ok and _VERSION.search(r.out) else None


def tip(repo: Path | str, ref: str, timeout_s: float) -> tuple[str, str]:
    """``(sha, "")`` for the commit ``ref`` names; ``("", why)`` when it names none, or git
    could not be asked (git missing, a timeout): "could not tell" is not "no such ref"."""
    r = G.run(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", timeout=timeout_s)
    if r.ok:
        return r.out, ""
    if r.unavailable:
        return "", r.err[-300:]
    return "", f"{ref!r} is not a commit of this repository"


def _parse(out: bytes) -> tuple[str, ...]:
    """The conflicted paths of ``merge-tree --name-only -z``: the tree id, then one NUL
    terminated path each up to an empty field (the informational messages follow it)."""
    fields = out.split(b"\0")[1:]
    names: list[str] = []
    for f in fields:
        if not f:
            break
        names.append(f.decode("utf-8", "replace"))
    return tuple(sorted(set(names)))


def _scratch(repo: Path, item: str, against: str) -> Probe:
    with CI.merge_tree(repo, item, against) as (_tree, why):
        if _tree is not None:
            return Probe(CLEAN, SCRATCH, (), item, against)
        kind = getattr(why, "kind", "")
        if kind == CI.CONFLICT:
            found = _FILES.search(str(why))
            names = tuple(n.strip() for n in found.group(1).split(",")) if found else ()
            return Probe(CONFLICT, SCRATCH, names, item, against)
        return Probe(UNAVAILABLE, SCRATCH, (), item, against, str(why)[-300:])


def run(
    repo: Path | str,
    item_ref: str,
    against_ref: str,
    *,
    timeout_s: float,
    min_git: str = "2.38",
) -> Probe:
    """Probe ``item_ref`` against ``against_ref`` (branch names or shas of this repository)."""
    repo = Path(repo)
    (a, why_a), (b, why_b) = tip(repo, item_ref, timeout_s), tip(repo, against_ref, timeout_s)
    if not (a and b):
        return Probe(UNAVAILABLE, NONE, (), a, b, why_a or why_b)
    have = git_version(repo, timeout_s)
    if have is None:
        return Probe(UNAVAILABLE, NONE, (), a, b, "git could not be run")
    if have < _tuple(min_git):
        return _scratch(repo, a, b)
    r = G.run(
        repo, "merge-tree", "--write-tree", "--name-only", "-z", a, b, binary=True,
        timeout=timeout_s,
    )  # fmt: skip
    if r.unavailable:
        return Probe(UNAVAILABLE, MERGE_TREE, (), a, b, r.err[-300:])
    if r.code == 0:
        return Probe(CLEAN, MERGE_TREE, (), a, b)
    if r.code == 1:
        return Probe(CONFLICT, MERGE_TREE, _parse(r.out_bytes or b""), a, b)
    if r.code == USAGE:  # an option this git does not know: the scratch merge answers instead
        return _scratch(repo, a, b)
    return Probe(UNAVAILABLE, MERGE_TREE, (), a, b, (r.err or r.out)[-300:])
