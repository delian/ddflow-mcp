"""Data model and pure helpers of the cleanup survey — no queue or git I/O.

These are the former ``ddflow/services/cleanup.py`` types and formatting helpers, split
out so each file stays under 30k chars (rule r-small-files). This submodule is the base
of the split's own layering: it imports nothing from the rest of the package, so
``dispose``/``survey``/``apply`` build on it without a cycle. The public API is
re-exported lazily by ``ddflow/services/cleanup/__init__.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


def _key(path: str) -> str:
    return str(Path(path).resolve()) if path else ""


@dataclass
class TreeState:
    name: str
    path: str = ""
    branch: str = ""
    item: str = ""
    kind: str = "orphan"
    ahead: int = 0
    behind: int = 0
    dirty_files: int = 0
    item_state: str = ""
    action: str = ""
    done: str = ""

    def render(self) -> str:
        bits = [f"{self.name:<24} {self.kind:<13}"]
        if self.branch:
            bits.append(f"{self.branch}")
        facts = []
        if self.ahead:
            facts.append(f"{self.ahead} unmerged commit(s)")
        if self.dirty_files:
            facts.append(f"{self.dirty_files} uncommitted file(s)")
        if self.item:
            facts.append(f"item {self.item} is {self.item_state or 'unknown'}")
        line = " ".join(bits) + (f"  [{', '.join(facts)}]" if facts else "")
        return line + (f"\n    -> {self.done or self.action}" if (self.done or self.action) else "")


@dataclass
class Plan:
    trees: list[TreeState] = field(default_factory=list)
    stale_branches: list[TreeState] = field(default_factory=list)

    @property
    def needs_human(self) -> list[TreeState]:
        return [t for t in self.trees if t.kind in ("dirty", "unreadable")]

    @property
    def actionable(self) -> list[TreeState]:
        return [t for t in self.trees + self.stale_branches if t.action]


@dataclass
class _Protected:
    """Trees and branches that are someone else's to remove, as ``item -- holder``."""

    held_paths: dict[str, str] = field(default_factory=dict)
    held_branches: dict[str, str] = field(default_factory=dict)
    adopted_paths: dict[str, str] = field(default_factory=dict)
    #: branch -> the sentence, for a removed item whose holder's work is on it (B71dedb76d2).
    stranded: dict[str, str] = field(default_factory=dict)

    def why(self, path: str, branch: str) -> tuple[str, str]:
        """(kind, reason) when the tree at ``path`` on ``branch`` must be left alone."""
        who = self.held_paths.get(_key(path)) or (branch and self.held_branches.get(branch))
        if who and branch in self.stranded:
            return "stranded", self.stranded[branch]
        if who:
            return "held", (
                f"LEAVE ALONE — item {who} holds a live lease on it: an agent is working "
                f"here now, and a fresh claim looks exactly like finished work."
            )
        who = self.adopted_paths.get(_key(path))
        if who:
            return "adopted", (
                f"LEAVE ALONE — adopted by item {who}: the agent harness's own working "
                f"tree, not ddflow's. The harness removes it, not cleanup."
            )
        return "", ""


def stranded_text(
    item: str,
    holder: str,
    branch: str,
    worktree: str = "",
    clone: str = "",
    mine: str | None = None,
) -> str:
    """THE sentence for a removed item whose lease is still held (B71dedb76d2): who holds
    it, which branch their work is on, and the exact way out. A lease is evidence of
    intent and is never stolen; removal does not destroy the holder's branch.

    ``mine`` is THIS clone's id (None = not known). A lease taken from another clone cannot
    be released here (`release` refuses it), so that clone's holder is told to do it
    (fix-removed-item-residuals); the remedy is never one this clone would refuse."""
    on = f" on branch {branch}" if branch else ""
    tree = f" (worktree {worktree})" if worktree else ""
    if clone and mine is not None and clone != mine:
        return (
            f"item {item} removed from the queue, lease still held by "
            f"{holder or 'unknown'} in another clone ({clone}){on}: nothing is stranded in "
            f"this clone; the holder, in that clone, releases it and keeps or deletes the branch"
        )
    fix = f"`ddflow release {item}`"
    if branch:
        fix += (
            f", then keep the branch with `git branch -m {branch} keep/{item}` "
            f"or delete it with `git branch -D {branch}`"
        )
    return (
        f"item {item} removed from the queue, lease still held by "
        f"{holder or 'unknown'}{on}{tree}: release it with {fix}"
    )


@dataclass(frozen=True)
class Stranded:
    item: str
    holder: str
    branch: str
    worktree: str

    def render(self) -> str:
        return stranded_text(self.item, self.holder, self.branch, self.worktree)  # same clone


@dataclass(frozen=True)
class Disposal:
    """What `dispose_tree` did: ``outcome`` is "removed", "refused" (the tree is someone's:
    ``kind`` says whose, "held" or "adopted") or "failed" (git said no, or the tree holds
    work and was not forced); ``why`` is the sentence for the one who asked. A removal that
    worked but could not delete the branch has ``branch_left`` set to git's answer."""

    outcome: str
    why: str = ""
    kind: str = ""
    branch_left: str = ""
    had_branch: bool = False

    @property
    def removed(self) -> bool:
        return self.outcome == "removed"
