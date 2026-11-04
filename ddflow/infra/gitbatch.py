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

import os
from pathlib import Path

from . import git as G
from . import worktree as W


def _real(path: Path | str) -> str:
    return os.path.realpath(str(path))


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
            r = G.run(
                self.repo,
                "for-each-ref",
                f"--format=%(objectname) %(ahead-behind:{base})",
                "refs/heads",
            )
            out: dict[str, int] = {}
            if r.ok:
                for line in r.out.splitlines():
                    parts = line.split()
                    if len(parts) == 3 and parts[1].isdigit():  # noqa: PLR2004 -- oid ahead behind
                        out[parts[0]] = int(parts[1])
            self._tips[base] = out
        return self._tips[base]

    def _worktree_heads(self) -> dict[str, str]:
        """HEAD object id by real worktree path, one `worktree list --porcelain`."""
        if self._heads is None:
            heads: dict[str, str] = {}
            r = G.run(self.repo, "worktree", "list", "--porcelain")
            path = ""
            if r.ok:
                for line in r.out.splitlines():
                    if line.startswith("worktree "):
                        path = _real(line[len("worktree ") :])
                    elif line.startswith("HEAD ") and path:
                        heads[path] = line[len("HEAD ") :].strip()
            self._heads = heads
        return self._heads

    def head(self, path: Path | str) -> str:
        """The HEAD commit of the worktree at ``path``: from the one listing, else asked for
        (a tree the listing lacks); "" when git cannot say."""
        key = _real(path)
        heads = self._worktree_heads()
        if key not in heads:
            r = G.run(path, "rev-parse", "--verify", "--quiet", "HEAD")
            heads[key] = r.out if r.ok else ""
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
            r = G.run(self.repo, "rev-list", "--count", f"{base}..{head}")
            self._counts[key] = int(r.out) if r.ok and r.out.isdigit() else -1
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
            r = G.run(self.repo, "rev-list", "--no-walk", *chunk, f"^{base}", "--")
            if not r.ok:
                continue
            missing = set(r.out.split())
            for s in chunk:
                # An abbreviated id would never match the full ones git prints: not remembered.
                if len(s) in (40, 64):
                    self._on_base[(s, base)] = s not in missing

    def on_base(self, sha: str, base: str) -> bool:
        """Is ``sha`` reachable from ``base``?"""
        key = (sha, base)
        if key not in self._on_base:
            r = G.run(self.repo, "merge-base", "--is-ancestor", sha, base)
            if r.code not in (0, 1):  # 128 is an unknown commit, not "not an ancestor"
                return False  # unknown: not remembered, and never "on the base"
            self._on_base[key] = r.ok
        return self._on_base[key]
