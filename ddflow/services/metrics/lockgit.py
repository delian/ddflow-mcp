"""The git half of ``ddflow metrics locks``: what a merge changed, and whether it conflicted.

Behind one small port so the report is pure and a test passes a fake. The default asks git
through ``infra.git`` only. Every answer is ``None`` when git could not give it (not installed,
timed out, an object gone, git older than 2.38 for ``merge-tree --write-tree``): "could not
tell", which the report counts as unavailable and never as clean.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ...core import bookkeeping
from ...infra import git as G


class Probes(Protocol):
    def files(self, before: str, after: str) -> list[str] | None:
        """Paths the merge changed on the target (ddflow's own state directory left out)."""

    def conflicts(self, base: str, head: str) -> bool | None:
        """Would merging ``head`` onto ``base`` conflict textually; None when git cannot say."""


class GitProbes:
    def __init__(self, repo: Path) -> None:
        self.repo = repo

    def files(self, before: str, after: str) -> list[str] | None:
        if not (before and after):
            return None
        got = G.paths(self.repo, "diff", "--name-only", before, after)
        return None if got is None else [p for p in got if not bookkeeping.is_state(p)]

    def conflicts(self, base: str, head: str) -> bool | None:
        if not (base and head):
            return None
        r = G.run(self.repo, "merge-tree", "--write-tree", base, head)
        if r.unavailable or r.code not in (0, 1):  # other codes: an error, not a verdict
            return None
        # An unknown object also exits 1; only a real verdict starts with the result tree id.
        tree = r.out.splitlines()[0] if r.out else ""
        if len(tree) not in (40, 64) or any(c not in "0123456789abcdef" for c in tree):
            return None
        return r.code == 1
