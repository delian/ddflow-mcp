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

_HEADER = re.compile(r'^diff --git a/(?P<path>[^"]+?) b/(?P=path)$')
_GITLINK_MODE = re.compile(
    r"^(?:(?:new file|deleted file|old|new) mode 160000|index \S+ 160000)$", re.M
)
_HEADER_LINES = 8


def _paths_of(text: str) -> set[str]:
    return {
        ln.split("=", 1)[1].strip()
        for ln in text.splitlines()
        if ln.strip().startswith("path") and "=" in ln
    }


def registered_submodules(repo: Path, *refs: str) -> set[str]:
    """The submodule paths `.gitmodules` registers at any of ``refs`` or in ``repo``'s working
    tree: a diff's two sides (and an uncommitted `git submodule add`) all count."""
    found: set[str] = set()
    for ref in refs:
        shown = W.git(repo, "show", f"{ref}:.gitmodules") if ref else None
        if shown is not None and shown.ok:
            found |= _paths_of(shown.out)
    on_disk = Path(repo) / ".gitmodules"
    if on_disk.is_file():
        found |= _paths_of(on_disk.read_text("utf-8", errors="replace"))
    return found


def without_stray_gitlinks(diff: str, registered: set[str]) -> str:
    """``diff`` without the file sections that change a gitlink at a path not in ``registered``.
    A section whose path cannot be read (git quotes unusual names) is kept."""
    if "160000" not in diff:
        return diff
    sections: list[list[str]] = []
    for line in diff.splitlines(keepends=True):
        if line.startswith("diff --git "):
            sections.append([])
        if sections:
            sections[-1].append(line)

    def stray(sec: list[str]) -> bool:
        head = "".join(sec[:_HEADER_LINES])
        m = _HEADER.match(sec[0].rstrip("\n"))
        return bool(m) and bool(_GITLINK_MODE.search(head)) and m["path"] not in registered

    return "".join("".join(sec) for sec in sections if not stray(sec))
