"""The git index, committing tree and staged-file plumbing every check reads."""

from __future__ import annotations

import shlex
import stat
from dataclasses import dataclass
from pathlib import Path

from ...infra import git as G
from ...infra import proc as P
from ...infra import worktree as W
from .. import changes as CH


def _chmod_x(path: Path) -> None:
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _this_worktree(repo: Path) -> Path | None:
    """The git working tree this hook is running in, or None.

    `--show-toplevel` from the cwd, because the hook's cwd IS the tree being committed
    — which is exactly the information needed to decide which lease applies.
    """
    r = G.run(".", "rev-parse", "--show-toplevel", timeout=P.TIMEOUTS["probe"])
    if not r.ok or not r.out:
        return None
    return Path(r.out).resolve()


def _committing_tree(repo: Path) -> Path | None:
    """The tree the commit being checked is made in: `_this_worktree`, but only when it
    is a checkout of ``repo``'s own repository; None otherwise.

    ``repo`` is the PRIMARY checkout (the CLI resolves it so), which is the wrong place
    to ask what a commit in a linked worktree stages. A cwd outside git, or in some
    other repository, says nothing about this commit (bug Bba366d9893).
    """
    here = _this_worktree(repo)
    if here is None:
        return None
    if here == Path(repo).resolve():
        return here
    try:
        same = W.repo_root(here).resolve() == W.repo_root(Path(repo)).resolve()
    except W.GitError:
        return None
    return here if same else None


def _index_tree(repo: Path) -> Path:
    """Where to run a read of THIS commit's index: the committing tree, else ``repo``.

    `git -C <primary> diff --cached` inside a linked worktree compared the index git
    hands the hook (`GIT_INDEX_FILE`, the worktree's) with the PRIMARY's HEAD, so every
    file main changed since the branch point, and every change the branch had already
    committed, read as staged (bug Bba366d9893). Run in the committing tree, the read
    gets that tree's HEAD, and its own index -- or, when git set one, the temporary
    index of a `commit -a` or `commit <paths>`, which is inherited, never dropped.
    Falling back to ``repo`` keeps the old behaviour where no tree of this repository
    is in sight.
    """
    return _committing_tree(repo) or repo


def staged_paths(repo: Path, *, tree: Path | None = None) -> list[str] | None:
    """Paths this commit will write.

    `--diff-filter=ACMR` over the INDEX, plus `--cached`, because a file the agent just
    created is not in HEAD and a diff against HEAD alone would not see it. Read in the
    committing tree (`_index_tree`, or ``tree`` when the caller already resolved it),
    against its own HEAD. Through
    `W.git_paths`, so a non-ASCII or non-UTF-8 name is neither C-quoted past the lease
    and view checks nor a crash of every commit.

    None when git could not say. It used to collapse to `[]`, and "nothing staged" lets
    every check pass: a damaged index (git exits 128, "index file smaller than expected")
    turned the lease and view checks into clean passes (roborev on 18cae1a). Callers
    refuse on None. NOT a held `index.lock`: these reads take no lock and succeed under
    one (verified), so naming it would send an operator hunting for the wrong cause.
    """
    return CH.changed_paths(
        tree or _index_tree(repo), include=("staged",), renames=True, diff_filter="ACMR"
    )


#: The refusal when the staged set itself is unknowable. Never a pass: "could not tell"
#: is not "nothing to check".
_UNKNOWN_STAGED = (
    "ddflow: git could not report which paths this commit stages, so it cannot be "
    "checked.\nRefusing rather than guessing. Check `git status`: a damaged or "
    "truncated `.git/index` is the usual cause."
)


def _index_blob(repo: Path, path: str) -> str | None:
    """The blob id the index holds for ``path`` (what `upgrade_written.digests` records)."""
    r = G.run(_index_tree(repo), "rev-parse", f":{path}", timeout=P.TIMEOUTS["git_listing"])
    return r.out.strip() if r.ok and r.out.strip() else None


def staged_bytes(repo: Path, path: str, *, tree: Path | None = None) -> bytes | None:
    """The INDEX copy of ``path`` -- what the commit will actually write.

    Not the working copy: a file fixed on disk but not re-staged would pass a check of
    the disk while the commit carried the broken bytes. Read in the committing tree, the
    same index `staged_paths` listed ``path`` from.
    """
    r = G.run(
        tree or _index_tree(repo),
        "show",
        f":{path}",
        binary=True,
        timeout=P.TIMEOUTS["git_listing"],
    )
    return r.out_bytes if r.ok else None


def _rel(repo: Path, path: Path) -> str:
    return W.repo_relative(repo, path) or str(path)


@dataclass(frozen=True)
class LogProbe:
    """What the commit would NOT record under the log dir, and why."""

    paths: list[str]  #: not in the index, or modified since staged
    ignored: list[str]  #: the subset a plain `git add` would skip
    failed: bool = False  #: git could not say -- never read as "clean"


def _unstaged_under(repo: Path, d: Path, tree: Path | None = None) -> LogProbe:
    """Paths under ``d`` whose working copy is not what the commit will record:
    modified-but-not-staged, or not in the index at all.

    Two cases, decided by whether ANY shard is tracked. If none is, the project does not
    commit its log, no view can be checked against a committed one, and nothing is
    reported -- the check compares against the working log as before. If one is, EVERY
    file there that is not in the index counts, ignored or not: a project ignoring only
    some shards would otherwise render a view from an ignored shard, commit it beside a
    log that lacks it, and pass (roborev on 43c2034).

    A git failure is `failed`, never an empty list: an empty stdout from a command that
    failed is not evidence of a clean log, and reading it as one would silently reopen
    the exact false pass this exists to close (roborev on 43c2034).

    The files are ``repo``'s and the index is the COMMITTING tree's, deliberately mixed.
    The view is rendered from `EventLog(repo)`, the primary's log on disk; the commit
    records the committing tree's index. Those two are what must agree -- the worktree's
    own working copy of the log is read by neither, so an unstaged edit to it cannot
    make the view wrong. A real hook in a linked worktree already got this pairing: git
    exports the worktree's `GIT_DIR`, and `-C <primary>` then made the primary the work
    tree. It is spelled out here so a check with no git environment (run by hand from
    the worktree) compares the same two things rather than the primary's own index (bug
    Bba366d9893).
    """
    rel = _rel(repo, d)
    via: list[str] = []
    tree = tree or _index_tree(repo)
    if tree.resolve() != Path(repo).resolve():
        gitdir = W.git(tree, "rev-parse", "--absolute-git-dir", timeout=30)
        if not gitdir.ok or not gitdir.out:
            return LogProbe([], [], failed=True)
        via = [f"--git-dir={gitdir.out}", f"--work-tree={Path(repo).resolve()}"]

    def git(*argv: str) -> list[str] | None:
        # `-z`: without it git C-quotes a non-ASCII path (`"caf\303\251.jsonl"`), and a
        # remedy built from that string names a file that does not exist -- `git add -f`
        # fails and the refusal never clears (roborev on 40950c9, reproduced).
        return W.git_paths(repo, *via, *argv, "--", rel)

    tracked = git("ls-files")
    modified = git("diff", "--name-only")
    untracked = git("ls-files", "--others")  # ignored ones included, deliberately
    ignored = git("ls-files", "--others", "--ignored", "--exclude-standard")
    if tracked is None or modified is None or untracked is None or ignored is None:
        return LogProbe([], [], failed=True)
    if not tracked:
        return LogProbe([], [])
    return LogProbe(sorted(set(modified + untracked)), sorted(ignored))


def _lf(data: bytes) -> bytes:
    """Line endings are git's and the platform's business, not the view's content.
    `write_text` writes CRLF on Windows and `core.autocrlf=false` stages it as is."""
    return data.replace(b"\r\n", b"\n")


def _out_hint(paths: list[str]) -> str:
    """` --out DIR` when every wrong view lives outside the default directory together."""
    dirs = {str(Path(p).parent) for p in paths}
    if len(dirs) == 1 and (d := dirs.pop()) != "docs/ddflow":
        return f" --out {shlex.quote(d)}"
    return ""
