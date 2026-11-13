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

import os
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
    if ref.startswith("-"):  # never let a ref be read as an option
        return "", f"{ref!r} is not a branch name or sha"
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
        names.append(os.fsdecode(f))
    return tuple(sorted(set(names)))


def _both_changed(repo: Path, item: str, against: str, timeout_s: float) -> tuple[str, ...] | None:
    """Paths changed on both sides since their merge base, as git spells them (NUL separated):
    a superset of the conflicted paths, with names exact whatever characters they carry."""
    base = G.run(repo, "merge-base", item, against, timeout=timeout_s)
    if not base.ok:
        return None
    sides = [
        G.run(
            repo, "diff", "--name-only", "-z", base.out, x, z=False, binary=True, timeout=timeout_s
        )
        for x in (item, against)
    ]
    if not all(r.ok for r in sides):
        return None
    one, two = ({os.fsdecode(n) for n in (r.out_bytes or b"").split(b"\0") if n} for r in sides)
    return tuple(sorted(one & two))


def _scratch(repo: Path, item: str, against: str, timeout_s: float) -> Probe:
    """The scratch merge under the probe's deadline. `ci.merge_tree` carries the deadline
    into the git calls that build the throwaway worktree and the merge inside it, so the
    probe is bounded in place (no worker thread to outlive it): past ``timeout_s`` git is
    killed, the worktree removed, and the probe comes back unavailable (never clean)."""
    with CI.merge_tree(repo, item, against, deadline_s=timeout_s) as (_tree, why):
        if _tree is not None:
            return Probe(CLEAN, SCRATCH, (), item, against)
        kind = getattr(why, "kind", "")
        if kind == CI.CONFLICT:
            # The scratch merge names its files only in prose, which a path with a comma or a
            # semicolon makes ambiguous: so the paths are every one both sides changed (exact
            # names, a superset of the conflicted ones), never a parse of the prose.
            names = _both_changed(repo, item, against, timeout_s)
            note = "" if names is not None else "the conflicting paths could not be listed"
            return Probe(CONFLICT, SCRATCH, names or (), item, against, note)
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
        return _scratch(repo, a, b, timeout_s)
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
        return _scratch(repo, a, b, timeout_s)
    return Probe(UNAVAILABLE, MERGE_TREE, (), a, b, (r.err or r.out)[-300:])
