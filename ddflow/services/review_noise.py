"""What a review diff leaves out besides generated files (B-delta-review-output-mixed-with-other-agents).

Other agents' event shards reach a branch through the base's own commits (they are dropped by a
pathspec in the callers), and a gitlink of a nested repository nobody registered as a submodule
(added by accident) is a commit id of something that is not this project's code. A REGISTERED
submodule's pointer change is the item's work and stays. When in doubt a section stays: a
review never loses work to this filter."""

from __future__ import annotations

import re
from pathlib import Path

from ..infra import worktree as W

#: Only a plain path is judged: a name with a space, a quote, a backslash or non-ASCII bytes
#: (git C-quotes some, leaves others in an ambiguous `a/X b/X` header) is KEPT, never guessed at.
_HEADER = re.compile(r"^diff --git a/(?P<p>[\w./@+-]+) b/(?P=p)$")
_GITLINK_MODE = re.compile(
    r"^(?:(?:new file|deleted file|old|new) mode 160000|index \S+ 160000)$", re.M
)


def _paths_of(repo: Path, *source: str) -> set[str]:
    """The submodule paths git's own config parser reads from ``source`` (`git config`'s
    `--blob <ref>:.gitmodules` or `--file <path>`): quoting and comments are git's to read."""
    r = W.git(repo, "config", *source, "--get-regexp", r"^submodule\..*\.path$")
    if not r.ok:
        return set()
    return {ln.split(" ", 1)[1].strip() for ln in r.out.splitlines() if " " in ln}


def registered_submodules(repo: Path, *refs: str) -> set[str]:
    """The submodule paths `.gitmodules` registers at any of ``refs`` or in ``repo``'s working
    tree: a diff's two sides (and an uncommitted `git submodule add`) all count."""
    found: set[str] = set()
    for ref in refs:
        if ref:
            found |= _paths_of(repo, "--blob", f"{ref}:.gitmodules")
    on_disk = Path(repo) / ".gitmodules"
    if on_disk.is_file():
        found |= _paths_of(repo, "--file", str(on_disk))
    return found


def _before_first_hunk(section: list[str]) -> list[str]:
    """The extended header lines of a file section: everything up to its first hunk."""
    for i, line in enumerate(section):
        if line.startswith("@@"):
            return section[:i]
    return section


def without_stray_gitlinks(diff: str, registered: set[str]) -> str:
    """``diff`` without the file sections that change a gitlink at a path not in ``registered``.
    A section whose header is not a plain `a/P b/P` is kept."""
    if "160000" not in diff:
        return diff
    sections: list[list[str]] = []
    for line in diff.splitlines(keepends=True):
        if line.startswith("diff --git "):
            sections.append([])
        if sections:
            sections[-1].append(line)

    def stray(sec: list[str]) -> bool:
        head = "".join(ln for ln in _before_first_hunk(sec))
        m = _HEADER.match(sec[0].rstrip("\n"))
        if not m or not _GITLINK_MODE.search(head):
            return False
        return m["p"] not in registered

    return "".join("".join(sec) for sec in sections if not stray(sec))
