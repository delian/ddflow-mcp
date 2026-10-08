"""``version cut`` bumps the project's own version files (B174).

``[flow.version_files]`` maps a repo-relative path to a regex with exactly ONE capture
group -- the version text -- and the cut replaces that group with the new version
(``1.2.3``, without the tag prefix) and commits the result on the branch the tag names:
the release branch under gitflow (so it travels in the release request in pr mode), the
release source itself for a trunk or maintenance cut.

Everything is checked against the release source BEFORE anything is written: a missing
file, a pattern that matches nothing, or one that matches twice (which of two
``version = ...`` lines is the project's?) refuses the whole cut. A half-bumped release
is worse than none, and a guess about which line is the version is exactly the silent
wrong answer this refuses to give. A file already at the new version is simply left alone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..config import Config
from ..core import flow as F
from ..infra import git as GIT
from ..infra import worktree as W


class VersionFileError(ValueError):
    """The bump cannot be made as configured; the message says which file and why.

    ``unavailable`` is "git could not run" (exit 2), as opposed to a refusal (exit 3).
    """

    def __init__(self, message: str, *, unavailable: bool = False):
        super().__init__(message)
        self.unavailable = unavailable


@dataclass
class Prepared:
    edits: dict[str, str]  # path -> new text (only files that change)
    paths: list[str]  # every configured path, in order

    def describe(self, version: str) -> str:
        return (
            f"bump {', '.join(self.paths)} to {version}"
            if self.edits
            else f"{', '.join(self.paths)} already at {version}"
        )


def _replace_group(rx: re.Pattern[str], text: str, version: str) -> str:
    def swap(m: re.Match[str]) -> str:
        lo = m.start(1) - m.start(0)
        hi = m.end(1) - m.start(0)
        return m.group(0)[:lo] + version + m.group(0)[hi:]

    return rx.sub(swap, text)


def prepare(repo: Path, cfg: Config, *, version: str, ref: str) -> Prepared:
    """Read every configured file at ``ref`` and compute its new text. Raises
    VersionFileError for the first thing that cannot be bumped."""
    edits: dict[str, str] = {}
    for path, pattern in cfg.flow.version_files.items():
        bad = F.version_file_problem(path, pattern)
        if bad:
            raise VersionFileError(bad)
        # Not `W.git`: it strips the output, and a file's own trailing newline is its content.
        shown = GIT.run(repo, "show", f"{ref}:{path}", binary=True)
        if not shown.ok:
            raise VersionFileError(
                f"[flow.version_files] {path!r} does not exist on {ref}: "
                f"{shown.err or 'git show failed'}"
            )
        text = (shown.out_bytes or b"").decode("utf-8")
        rx = re.compile(pattern, re.MULTILINE)
        hits = list(rx.finditer(text))
        found = len(hits)
        if found == 1 and hits[0].start(1) == -1:
            raise VersionFileError(
                f"[flow.version_files] {path!r}: {pattern!r} matched but its capture group took "
                f"no part in the match (an optional or alternative group), so there is no "
                f"version text to replace"
            )
        if found != 1:
            what = "matches nothing" if found == 0 else f"matches {found} times"
            raise VersionFileError(
                f"[flow.version_files] {path!r}: {pattern!r} {what} in {path} on {ref}; it "
                f"must match exactly once (anchor it with ^ and $)"
            )
        new = _replace_group(rx, text, version)
        if new != text:
            edits[path] = new
    return Prepared(edits, list(cfg.flow.version_files))


def commit_on(repo: Path, cfg: Config, branch: str, prep: Prepared, *, message: str) -> list[str]:
    """Write and commit the bump on local ``branch``. Returns the files changed.

    Never into a tree somebody else has the branch checked out in: a throwaway worktree
    holds it unless it is the primary checkout's own branch.
    """
    if not prep.edits:
        return []
    from . import changelog_cut as CC
    from .export.query import ExportError

    try:
        root, throwaway = CC._tree_for(repo, cfg, branch)
    except ExportError as exc:
        from .export.query import EXIT_UNAVAILABLE

        raise VersionFileError(str(exc), unavailable=exc.code == EXIT_UNAVAILABLE) from exc
    if not throwaway:
        return _commit_edits(root, prep, message)
    base = (root / cfg.worktree.root).resolve()
    base.mkdir(parents=True, exist_ok=True)
    with W.scratch_tree(root, branch, prefix=f".version-{W.safe_name(branch)}-", under=base) as s:
        if s.path is None:
            raise VersionFileError(
                f"could not stage {branch} for the bump: {s.add.err}", unavailable=True
            )
        return _commit_edits(s.path, prep, message)


def _commit_edits(tree: Path, prep: Prepared, message: str) -> list[str]:
    """Write ``prep``'s edits in ``tree`` and commit them; the paths changed."""
    paths = list(prep.edits)
    dirty = GIT.status_run(tree, *paths)
    entries = GIT.parse_status(dirty)
    if entries is None:
        raise VersionFileError(f"git status failed: {dirty.err}", unavailable=True)
    if entries:
        raise VersionFileError(
            f"{', '.join(paths)} has uncommitted changes; refusing to commit them into "
            f"the release (commit or discard them)"
        )
    for path, text in prep.edits.items():
        (tree / path).write_text(text, encoding="utf-8")
    try:
        for step in (("add", "--", *paths), ("commit", "-m", message, "--", *paths)):
            r = W.git(tree, *step)
            if not r.ok:
                raise VersionFileError(
                    f"git {step[0]} of the version files failed: {r.err or r.out}"
                )
    except VersionFileError:
        # Never leave a half-bumped working tree behind (a failing hook, a signing error).
        W.git(tree, "reset", "-q", "--", *paths)
        W.git(tree, "checkout", "-q", "--", *paths)
        raise
    return paths
