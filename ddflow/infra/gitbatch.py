"""Batched git facts for a scan over many worktrees (PS-git-batch, R-scalability).

A scan that asks git one question per worktree spawns a process per lease: 622 for the 126
stale leases of the measured log. The answers it wants are repository-wide and cheap to ask
once, so `RepoFacts` asks each of them ONCE per scan and remembers it:

* the base branch (``default_branch`` spawns two processes; once, not once per tree);
* the commits-ahead count of every local branch tip, from one ``for-each-ref`` with the
  ``ahead-behind`` atom (git 2.41+; where it is missing the count falls back to one
  ``rev-list --count`` per distinct HEAD, remembered);
* every worktree's HEAD, from one ``worktree list --porcelain``;
* which of a set of commits the base already holds, from one ``rev-list --no-walk``.

Everything is DERIVED and lives only as long as the scan object (r-derived-cache-bounded):
nothing is written, so there is nothing to invalidate across processes. All git goes
through `infra.git` (r-adapters). An answer git could not give is ``None`` / -1, never a
guess: unknown is not zero.
"""

from __future__ import annotations

from pathlib import Path

from . import git as G
from . import worktree as W


class RepoFacts:
    """Repository-wide git answers for one scan; build one per command and drop it."""

    def __init__(self, repo: Path | str):
        self.repo = Path(repo)
        self._base: str | None = None
        self._tips: dict[str, dict[str, int]] = {}
        self._heads: dict[str, str] | None = None
        self._counts: dict[tuple[str, str], int] = {}
        self._on_base: dict[tuple[str, str], bool] = {}

    def base(self, configured: str = "") -> str:
        """The base branch: ``configured`` when set, else the repository default, resolved once."""
        if configured:
            return configured
        if self._base is None:
            self._base = W.default_branch(self.repo)
        return self._base

    def _tip_aheads(self, base: str) -> dict[str, int]:
        """Commits ahead of ``base`` per branch-tip object id, one `for-each-ref`."""
        if base not in self._tips:
            self._tips[base] = G.branch_tip_aheads(self.repo, base)
        return self._tips[base]

    def _worktree_heads(self) -> dict[str, str]:
        """HEAD object id by real worktree path, one `worktree list --porcelain`."""
        if self._heads is None:
            try:
                listed = W.list_worktrees(self.repo)
            except G.GitError:  # could not tell: `head` then asks each tree for itself
                listed = []
            self._heads = {
                G.real_path(w["worktree"]): w.get("HEAD", "") for w in listed if "worktree" in w
            }
        return self._heads

    def head(self, path: Path | str) -> str:
        """The HEAD commit of the worktree at ``path``: from the one listing, else asked for
        (a tree the listing lacks); "" when git cannot say."""
        key = G.real_path(path)
        heads = self._worktree_heads()
        if key not in heads:
            heads[key] = G.head_commit(path)
        return heads[key]

    def ahead(self, path: Path | str, base: str) -> int:
        """Commits of the worktree's HEAD that ``base`` lacks; -1 when it could not be told.

        A HEAD that is some branch's tip is answered by the one `for-each-ref`; any other
        is counted once per (HEAD, base) and remembered."""
        head = self.head(path)
        if not head:
            return -1
        known = self._tip_aheads(base).get(head)
        if known is not None:
            return known
        key = (head, base)
        if key not in self._counts:
            self._counts[key] = G.commits_ahead(self.repo, base, head)
        return self._counts[key]

    def prefetch_on_base(self, shas: list[str], base: str) -> None:
        """Ask once which of ``shas`` the base holds; `on_base` then answers from memory.

        `rev-list --no-walk S... ^base` prints exactly the listed commits the base lacks.
        If git refuses (an unknown object among them) nothing is remembered and each
        `on_base` asks for itself."""
        todo = sorted({s for s in shas if s and (s, base) not in self._on_base})
        step = 500  # commits per query: argv stays small
        for i in range(0, len(todo), step):
            chunk = todo[i : i + step]
            missing = G.commits_missing_from(self.repo, chunk, base)
            if missing is None:
                continue
            for s in chunk:
                # An abbreviated id would never match the full ones git prints: not remembered.
                if len(s) in (40, 64):
                    self._on_base[(s, base)] = s not in missing

    def on_base(self, sha: str, base: str) -> bool | None:
        """Is ``sha`` reachable from ``base``? ``None`` when git could not say (an unknown
        commit, a refused merge-base): an unknown is NOT "off the base", it is unknown, and it
        is never remembered (Bd3a93d1823)."""
        key = (sha, base)
        if key not in self._on_base:
            known = G.is_ancestor(self.repo, sha, base)
            if known is None:
                return None
            self._on_base[key] = known
        return self._on_base[key]
