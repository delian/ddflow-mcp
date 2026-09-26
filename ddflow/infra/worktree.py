"""Git worktree lifecycle — isolation that survives a crash.

One task, one worktree, one branch. The isolation is not about tidiness: two agents
sharing a working tree destroy each other, because ``git reset --hard``, ``git clean``
and ``git checkout`` are whole-tree operations and an agent has no way to scope them to
its own files.

Three rules encoded here, each from a failure that actually happened somewhere:

1. **Never switch the primary checkout's branch.** A checkout under a live session
   swaps the files beneath it. Worktrees are created FROM the primary and merged INTO
   it while it stays put; ``merge`` runs without any preceding checkout.
2. **Never remove a worktree that has unmerged work.** ``remove`` refuses on a dirty
   or ahead tree unless forced, because "the task is done" and "the tree is empty" are
   different claims and only the second one licenses deletion.
3. **Sync before you start, not at the end.** A branch that diverges for a week
   becomes unmergeable; the merge everyone postpones is the one that fails.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from ..config import Config
from ..infra import proc as P


class GitError(RuntimeError):
    pass


@dataclass
class GitResult:
    code: int
    out: str
    err: str

    @property
    def ok(self) -> bool:
        return self.code == 0

    def text(self) -> str:
        return (self.out or self.err).strip()


def git(repo: Path | str, *args: str, timeout: int = 300, check: bool = False) -> GitResult:
    p = P.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=timeout)
    res = GitResult(p.returncode, p.stdout.strip(), p.stderr.strip())
    if check and not res.ok:
        raise GitError(f"git {' '.join(args)} failed ({res.code}): {res.err or res.out}")
    return res


def default_branch(repo: Path) -> str:
    """Resolve the repo's default branch. Never assume 'main'.

    Half the repositories in the world are on ``master``; hardcoding either name makes
    the tool silently wrong on the other half. Order: origin/HEAD, then whichever of
    main/master exists, then the current HEAD as a last resort.
    """
    r = git(repo, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    if r.ok and r.out:
        return r.out.split("/", 1)[1] if "/" in r.out else r.out
    for cand in ("main", "master"):
        if git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{cand}").ok:
            return cand
    r = git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    return r.out or "HEAD"


def repo_root(start: Path) -> Path:
    """The PRIMARY checkout, even when called from inside a linked worktree.

    ``--git-common-dir`` points at the shared ``.git`` from any worktree, which is what
    makes every agent land on ONE event log. Using ``--show-toplevel`` instead would
    give each worktree its own log directory — one identity per agent, which is the
    worst possible failure for a coordination system.
    """
    r = git(start, "rev-parse", "--git-common-dir")
    if not r.ok:
        raise GitError(f"not a git repository: {start}")
    common = Path(r.out)
    if not common.is_absolute():
        common = (Path(start) / common).resolve()
    return common.parent


def current(start: Path) -> Worktree | None:
    """The LINKED worktree the caller is standing in, or None if it is the primary.

    `--show-toplevel` is the caller's own tree; `repo_root` is the primary. They are
    equal in the primary checkout and differ inside a linked worktree, which is the
    whole discriminator — no name conventions, no path prefixes, nothing that a
    differently-configured harness could defeat.

    This exists because `claim` used to create a worktree unconditionally, so an agent
    whose harness had ALREADY isolated it (Claude Code and Cursor both do) was sent to
    a second tree on a second branch and its uncommitted work was stranded in the
    first. ddflow does not need to have CREATED a tree; it needs to know WHICH tree an
    item is being worked in, so `recover` and `merge` can find it. Being told is as
    good as having made it.

    `base` is left empty: an adopted tree's branch already exists and was not branched
    by us, so claiming to know what it came from would be a guess.

    `created` is False, and that flag alone is NOT what protects the tree. It lives on
    an in-memory `Worktree` that never crosses a process boundary, while
    `remove_on_merge` runs from a later invocation with only the fold to consult — so an
    earlier version of this docstring asserted a safety property that did not exist and
    `merge` deleted the agent's own tree. What protects it is `Item.adopted`, set by the
    `worktree.adopted` handler and checked in `cmd_merge`.
    """
    top = git(start, "rev-parse", "--show-toplevel")
    if not top.ok:
        return None
    try:
        primary = repo_root(start)
    except GitError:
        return None
    here = Path(top.out).resolve()
    if here == primary.resolve():
        return None
    head = git(here, "rev-parse", "--abbrev-ref", "HEAD")
    branch = head.out.strip() if head.ok else ""
    if branch == "HEAD":
        # Detached: there is a tree but no branch to merge from later. Reported as no
        # adoptable worktree rather than adopted with an empty branch, because `merge`
        # would then have nothing to act on and would fail at the end of the work
        # instead of at the start.
        return None
    return Worktree(item="", path=here, branch=branch, base="", created=False)


def safe_name(item_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "-", item_id).strip("-") or "item"


@dataclass
class Worktree:
    item: str
    path: Path
    branch: str
    base: str
    created: bool = False


def create(repo: Path, cfg: Config, item_id: str, *, base: str = "") -> Worktree:
    """Create (or adopt) the worktree for an item. Idempotent by design.

    Adoption matters for recovery: after a crash the agent restarts, finds the
    worktree already present, and continues in it rather than erroring or — far worse —
    creating a second tree and abandoning the first with its work inside.
    """
    root = repo_root(repo)
    base = base or cfg.worktree.base_ref or default_branch(root)
    name = safe_name(item_id)
    branch = f"{cfg.worktree.branch_prefix}{name}"
    from ..infra.container import default_worktree_root

    # Inside a container the default sibling root lands on the ephemeral layer and is
    # destroyed on exit, taking uncommitted work with it. See container.py.
    wt_root = (root / default_worktree_root(cfg.worktree.root)).resolve()
    path = wt_root / name
    wt = Worktree(item=item_id, path=path, branch=branch, base=base)

    if path.exists() and (path / ".git").exists():
        return wt  # adopt
    wt_root.mkdir(parents=True, exist_ok=True)

    have_branch = git(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}").ok
    args = ["worktree", "add"]
    if have_branch:
        args += [str(path), branch]
    else:
        args += ["-b", branch, str(path), base]
    r = git(root, *args)
    if not r.ok:
        raise GitError(f"worktree add failed for {item_id}: {r.err or r.out}")
    wt.created = True
    if cfg.worktree.sync_before_start and have_branch:
        sync(wt, cfg)
    return wt


def sync(wt: Worktree, cfg: Config) -> GitResult:
    """Merge the base branch INTO the task branch, from inside the worktree.

    This direction is always safe: it never touches the primary checkout and never
    performs a checkout. The reverse (checking out base to update it) is what kills
    live sessions.
    """
    return git(wt.path, "merge", "--no-edit", wt.base)


def behind(wt: Worktree) -> int:
    r = git(wt.path, "rev-list", "--count", f"HEAD..{wt.base}")
    return int(r.out) if r.ok and r.out.isdigit() else -1


def ahead(wt: Worktree) -> int:
    r = git(wt.path, "rev-list", "--count", f"{wt.base}..HEAD")
    return int(r.out) if r.ok and r.out.isdigit() else -1


def dirty(wt: Worktree, *, untracked: bool = True) -> list[str]:
    """Uncommitted changes in a tree.

    ``untracked=False`` reports only MODIFIED TRACKED files, which is the right
    question to ask before a merge: git refuses a merge over local modifications, but
    untracked files are none of its business except in the one case it detects and
    reports itself. Counting untracked files as "dirty" made `merge` refuse forever in
    any repo holding a build directory, a virtualenv, or — as found here — ddflow's
    own freshly-created `.ddflow/` before it was committed.
    """
    args = ["status", "--porcelain"] + ([] if untracked else ["--untracked-files=no"])
    r = git(wt.path, *args)
    return [ln for ln in r.out.splitlines() if ln.strip()] if r.ok else []


def commit(
    wt: Worktree,
    message: str,
    paths: list[str] | None = None,
    *,
    trailers: dict[str, str] | None = None,
) -> GitResult:
    """Commit with explicit paths and machine-readable trailers.

    Explicit paths, never ``-A``: a parallel agent's unrelated file staged into your
    commit is a corruption that is very hard to notice and very hard to undo. The
    ``Item:`` trailer is what lets an audit reconcile commits against the queue with
    ``git log --format='%(trailers:key=Item)'`` instead of parsing prose.
    """
    if paths:
        git(wt.path, "add", "--", *paths)
    else:
        git(wt.path, "add", "-u")
    body = message
    tr = dict(trailers or {})
    tr.setdefault("Item", wt.item)
    body += "\n\n" + "\n".join(f"{k}: {v}" for k, v in tr.items())
    return git(wt.path, "commit", "-m", body)


def merge(repo: Path, cfg: Config, wt: Worktree, *, message: str = "") -> GitResult:
    """Merge the task branch into base FROM THE PRIMARY CHECKOUT, with no checkout.

    Refuses if the primary is not already on base — switching it is exactly the
    operation that kills a live session, so the tool reports the problem instead of
    performing the dangerous fix.
    """
    root = repo_root(repo)
    cur = git(root, "rev-parse", "--abbrev-ref", "HEAD").out
    if cur != wt.base:
        return GitResult(
            2,
            "",
            (
                f"primary checkout is on {cur!r}, not {wt.base!r}. ddflow will NOT switch "
                f"it — a branch switch under a live agent session swaps files beneath it. "
                f"Check out {wt.base} yourself in a quiet moment, then re-run."
            ),
        )
    # NO blanket dirty check here. An earlier version refused whenever the primary had
    # any modified tracked file, on the stated grounds that "a merge would mix them
    # into the result". That premise is FALSE, and a probe says so: merging with an
    # unrelated file dirty succeeds, the local edit does NOT enter the merge commit,
    # and it stays uncommitted afterwards. Git refuses precisely and only when the
    # merge would OVERWRITE a locally-modified file, and it names those files exactly.
    # Duplicating that judgement more crudely only refused safe merges -- including,
    # routinely, a merge blocked by ddflow's own freshly-written config.
    args = ["merge"]
    if cfg.worktree.merge_strategy == "no-ff":
        args.append("--no-ff")
    elif cfg.worktree.merge_strategy == "ff-only":
        args.append("--ff-only")
    elif cfg.worktree.merge_strategy == "squash":
        args.append("--squash")
    args += ["-m", message or f"merge {wt.item}", wt.branch]
    return git(root, *args)


def head_sha(path: Path) -> str:
    r = git(path, "rev-parse", "HEAD")
    return r.out if r.ok else ""


def remove(repo: Path, cfg: Config, wt: Worktree, *, force: bool = False) -> GitResult:
    """Remove a worktree, refusing to destroy unmerged work unless forced."""
    root = repo_root(repo)
    if not force:
        d, a = dirty(wt), ahead(wt)
        if d or a > 0:
            return GitResult(
                2,
                "",
                (
                    f"refusing to remove {wt.path}: {len(d)} uncommitted file(s), "
                    f"{a} unmerged commit(s). Inspect with `git -C {wt.path} diff {wt.base}`; "
                    f"pass --force once you are certain."
                ),
            )
    args = ["worktree", "remove"] + (["--force"] if force else []) + [str(wt.path)]
    r = git(root, *args)
    if not r.ok and wt.path.exists() and force:
        shutil.rmtree(wt.path, ignore_errors=True)
        git(root, "worktree", "prune")
        return GitResult(0, "removed (pruned)", "")
    if r.ok:
        git(root, "branch", "-d", wt.branch)
    return r


def list_worktrees(repo: Path) -> list[dict[str, str]]:
    r = git(repo_root(repo), "worktree", "list", "--porcelain")
    out, cur = [], {}
    for line in r.out.splitlines():
        if not line.strip():
            if cur:
                out.append(cur)
            cur = {}
        elif " " in line:
            k, v = line.split(" ", 1)
            cur[k] = v
        else:
            cur[line] = "true"
    if cur:
        out.append(cur)
    return out


def capture_diff(tree: Path, base: str = "", *, include_untracked: bool = True) -> str:
    """The diff a reviewer should actually see, including NEW files.

    `git diff` omits untracked files entirely. A reviewer handed that diff cannot see
    the regression test you just wrote — and then reports "this change has no tests",
    which is both wrong and expensive, because it is exactly the finding a careful
    reviewer is supposed to produce. `git add -N` (intent-to-add) registers untracked
    paths in the index so they appear as new-file diffs, without staging their content.

    Ordering matters: intent-to-add FIRST, then one `git diff HEAD` that covers tracked
    modifications and new files together. Concatenating two separate diffs produces
    duplicate headers when a file is both modified and re-added.
    """
    untracked = [
        ln
        for ln in git(tree, "ls-files", "--others", "--exclude-standard").out.splitlines()
        if ln.strip()
    ]
    if include_untracked and untracked:
        git(tree, "add", "-N", "--", *untracked)
    try:
        if base:
            merge_base = git(tree, "merge-base", base, "HEAD").out or base
            committed = git(tree, "diff", f"{merge_base}..HEAD").out
        else:
            committed = ""
        working = git(tree, "diff", "HEAD").out
    finally:
        if include_untracked and untracked:
            # Undo intent-to-add so the caller's index is exactly as we found it. A
            # review that leaves files staged changes what the next commit contains.
            git(tree, "reset", "--quiet", "--", *untracked)
    return "\n".join(part for part in (committed, working) if part.strip())


def diff_covers_everything(tree: Path, diff: str) -> tuple[bool, list[str]]:
    """Cross-check: every path git reports as changed must appear in the diff.

    A silent omission is the failure this guards -- and it is silent by construction,
    because a diff that is missing a file looks exactly like a diff of a change that
    did not touch that file.
    """
    changed = [
        ln[3:].strip().strip('"')
        for ln in git(tree, "status", "--porcelain").out.splitlines()
        if ln.strip()
    ]
    missing = [p for p in changed if p and p not in diff]
    return (not missing), missing


def store_path(repo: Path, path: Path | str) -> str:
    """How a worktree path is written INTO the event log: relative to the repo root.

    The log is committed and shared. An absolute path is true only on the machine that
    wrote it, so storing one makes the log say something false on every other checkout:
    a teammate who clones to a different directory, a CI job, and — most sharply — a
    container, where the repo is `/repo` and nothing else on the host is.

    Falls back to an absolute path only when the worktree genuinely lies outside the
    repository tree AND `os.path.relpath` cannot express it portably. That case is
    reported by `ddflow doctor` rather than silently accepted.
    """
    p = Path(path).resolve()
    root = Path(repo).resolve()
    try:
        return p.relative_to(root).as_posix()
    except ValueError:
        pass
    try:
        rel = os.path.relpath(p, root)
    except ValueError:  # different drive on Windows
        return str(p)
    return Path(rel).as_posix()


def load_path(repo: Path, stored: str) -> Path:
    """Resolve a stored worktree path back to something on THIS machine.

    Absolute stored paths are honoured as-is, because logs written by older versions
    contain them and silently reinterpreting an absolute path as relative would point
    recovery at a directory that does not exist -- which reports "nothing to salvage"
    over real work, the one error this system must never make.
    """
    if not stored:
        return Path()
    p = Path(stored)
    return p if p.is_absolute() else (Path(repo).resolve() / p).resolve()


def branches(repo: Path, prefix: str = "") -> list[str]:
    """Local branches, optionally filtered to a prefix."""
    r = git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/")
    names = [ln.strip() for ln in r.out.splitlines() if ln.strip()] if r.ok else []
    return [n for n in names if not prefix or n.startswith(prefix)]


def is_merged(repo: Path, branch: str, base: str) -> bool:
    """Is every commit on ``branch`` already reachable from ``base``?

    Asked with `rev-list --count base..branch == 0` rather than `branch --merged`,
    because the latter answers about the CURRENT checkout's HEAD when given no
    argument, and a cleanup pass that silently asked the wrong question would delete
    branches that were not merged at all.
    """
    r = git(repo, "rev-list", "--count", f"{base}..{branch}")
    return r.ok and r.out.strip() == "0"
