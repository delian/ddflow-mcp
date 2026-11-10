"""The shipped fake of the `Git` port: a real git repository in a temp directory.

`FakeGit` is a git adapter bound to a `TempRepo` it owns (or adopts): the conformance
cases exercise real git behaviour against it, so a test that needs a commit, a branch
or a merge stands one up with no caller-owned repository to arrange, and no answer is
ever scripted -- "unavailable" is git's own exit code, never fabricated. `TempRepo` is
the shared helper (r-adapters): one way to create a throwaway repository, for the fake
and for a test that wants the real adapter pointed at a scratch tree.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import fsio
from .. import git as G
from . import fake


class TempRepo:
    """A throwaway git repository: `git init`, a user identity, one marker commit.

    `path` is the repository root; `write` adds a file, `commit` stages everything and
    returns the HEAD commit id. `remove` deletes the directory; the object also cleans
    up when it is garbage-collected (it owns a `tempfile.TemporaryDirectory`).
    """

    def __init__(self, prefix: str = "ddflow-fakegit-") -> None:
        # `TemporaryDirectory` cleans up on garbage collection, so a `FakeGit()` built
        # and dropped by the conformance harness does not leak a repository on disk.
        self._tmp = fsio.TemporaryDirectory(prefix=prefix)
        self.path = Path(self._tmp.name)
        if not G.run(self.path, "init", "-q", "-b", "main").ok:
            raise RuntimeError(f"git init failed in {self.path}")
        G.run(self.path, "config", "user.name", "ddflow")
        G.run(self.path, "config", "user.email", "ddflow@example.invalid")
        self.write("README.md", "init\n")
        self.commit("init")

    def write(self, name: str, text: str = "") -> Path:
        """Add (or replace) the file ``name`` in the working tree; returns its path."""
        p = self.path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def commit(self, message: str = "commit") -> str:
        """Stage everything and commit; returns the HEAD commit id."""
        G.run(self.path, "add", "-A")
        r = G.run(self.path, "commit", "-qm", message)
        if not r.ok:
            raise RuntimeError(f"git commit failed: {r.err or r.out}")
        return G.commit_of(self.path, "HEAD")

    def remove(self) -> None:
        self._tmp.cleanup()


@fake
class FakeGit(G.Git):
    """A `Git` adapter bound to a `TempRepo`: every verb runs git for real in that repo.

    The ``repo`` argument of each verb is ignored -- the fake IS its repository, so a
    caller (and the conformance cases) need no repository of their own. ``calls``
    records every verb with its arguments, for a test that must assert an ordering.
    """

    def __init__(self, temp: TempRepo | None = None) -> None:
        self.temp = temp or TempRepo()
        self.repo = self.temp.path
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def run(self, repo: Path | str, *args: str, **kwargs: Any) -> Any:
        self.calls.append(("run", (repo, *args)))
        return G.run(self.repo, *args, **kwargs)

    def paths(self, repo: Path | str, *args: str, **kwargs: Any) -> list[str] | None:
        self.calls.append(("paths", (repo, *args)))
        return G.paths(self.repo, *args, **kwargs)

    def unmerged(self, repo: Path | str, timeout: float | None = None) -> list[str] | None:
        self.calls.append(("unmerged", (repo,)))
        return G.unmerged(self.repo, timeout=timeout)

    def files(self, repo: Path | str, *args: str, **kwargs: Any) -> list[str] | None:
        self.calls.append(("files", (repo, *args)))
        return G.files(self.repo, *args, **kwargs)

    def status(self, repo: Path | str, *args: str, **kwargs: Any) -> list | None:
        self.calls.append(("status", (repo, *args)))
        return G.status(self.repo, *args, **kwargs)

    def status_run(self, repo: Path | str, *args: str, **kwargs: Any) -> Any:
        self.calls.append(("status_run", (repo, *args)))
        return G.status_run(self.repo, *args, **kwargs)

    def head_commit(self, repo: Path | str) -> str:
        self.calls.append(("head_commit", (repo,)))
        return G.head_commit(self.repo)

    def commit_of(self, repo: Path | str, rev: str, **kwargs: Any) -> str:
        self.calls.append(("commit_of", (repo, rev)))
        return G.commit_of(self.repo, rev, **kwargs)

    def branch_tip_aheads(self, repo: Path | str, base: str) -> dict[str, int]:
        self.calls.append(("branch_tip_aheads", (repo, base)))
        return G.branch_tip_aheads(self.repo, base)

    def commits_ahead(self, repo: Path | str, base: str, head: str, **kwargs: Any) -> int:
        self.calls.append(("commits_ahead", (repo, base, head)))
        return G.commits_ahead(self.repo, base, head, **kwargs)

    def commits_missing_from(self, repo: Path | str, shas: list[str], base: str) -> set[str] | None:
        self.calls.append(("commits_missing_from", (repo, tuple(shas), base)))
        return G.commits_missing_from(self.repo, shas, base)

    def is_ancestor(self, repo: Path | str, sha: str, base: str, **kwargs: Any) -> bool | None:
        self.calls.append(("is_ancestor", (repo, sha, base)))
        return G.is_ancestor(self.repo, sha, base, **kwargs)

    def changed_paths(self, repo: Path | str, old: str, new: str, **kwargs: Any) -> list[str]:
        self.calls.append(("changed_paths", (repo, old, new)))
        return G.changed_paths(self.repo, old, new, **kwargs)

    def bundle_create(self, repo: Path | str, dest: Path, ref: str, **kwargs: Any) -> None:
        self.calls.append(("bundle_create", (repo, str(dest), ref)))
        return G.bundle_create(self.repo, dest, ref, **kwargs)

    def bundle_heads(self, bundle: Path, **kwargs: Any) -> list[str]:
        self.calls.append(("bundle_heads", (str(bundle),)))
        return G.bundle_heads(bundle, **kwargs)
