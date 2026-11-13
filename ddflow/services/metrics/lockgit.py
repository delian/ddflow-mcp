"""The git half of ``ddflow metrics locks``: what a merge changed, and whether it conflicted.

Behind one small port so the report is pure and a test passes a fake. The conflict verdict is
``probe.run``'s (clean -> False, conflict -> True, unavailable -> None) so this report and the
probe cannot drift; an older git falls back to a scratch merge, never "could not tell" for a
version reason. Every answer is ``None`` when git truly could not answer (not installed, timed
out, an object gone): "could not tell", which the report counts as unavailable and never as
clean.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from ...core import bookkeeping
from ...infra import git as G
from ..perf.suitewaste import tree_key
from ..probe import CLEAN, CONFLICT
from ..probe import run as probe_run


class Probes(Protocol):
    def files(self, before: str, after: str) -> list[str] | None:
        """Paths the merge changed on the target (ddflow's own state directory left out)."""

    def code_tree(self, sha: str) -> str | None:
        """A key equal for two commits with equal code paths (state and documents left out)."""

    def conflicts(self, base: str, head: str) -> bool | None:
        """Would merging ``head`` onto ``base`` conflict textually; None when git cannot say."""


class GitProbes:
    def __init__(
        self, repo: Path, timeout_s: float, min_git: str, doc_globs: Sequence[str] = ()
    ) -> None:
        # Both knobs are REQUIRED so GitProbes has no private copy of a config default that could
        # drift from [locks].probe_timeout_s / probe_merge_tree_min_git (the caller passes them).
        self.repo = repo
        self.timeout_s = timeout_s
        self.min_git = min_git
        #: [gates].doc_globs: documents `code_tree` leaves out (the caller passes the knob).
        self.doc_globs = tuple(doc_globs)

    def files(self, before: str, after: str) -> list[str] | None:
        if not (before and after):
            return None
        got = G.paths(self.repo, "diff", "--name-only", before, after)
        return None if got is None else [p for p in got if not bookkeeping.is_state(p)]

    def code_tree(self, sha: str) -> str | None:
        if not sha:
            return None
        try:
            entries = G.tree_entries(self.repo, sha, timeout=self.timeout_s)
        except G.GitError:
            return None
        return tree_key(entries, self.doc_globs)

    def conflicts(self, base: str, head: str) -> bool | None:
        if not (base and head):
            return None
        # One source for the conflict verdict: probe.run (clean -> False, conflict -> True,
        # unavailable -> None) so lockgit and the probe cannot drift. probe.run adds a rev-parse
        # and a --version call per pair before its merge-tree.
        probe = probe_run(self.repo, base, head, timeout_s=self.timeout_s, min_git=self.min_git)
        if probe.result == CLEAN:
            return False
        if probe.result == CONFLICT:
            return True
        return None
