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
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import Config
from ..core.flow import safe_name as _core_safe_name
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


def git_paths(repo: Path | str, *args: str, timeout: int = 60) -> list[str] | None:
    """File paths git lists, EXACTLY as the filesystem names them; None if git failed.

    The way a path listing SHOULD be read -- not yet the only one: several older listings
    (`dirty`, the gate tree fingerprint, and others named in docs/HANDOFF.md §2) still
    read without `-z` and are not migrated. A caller must treat None as "could not
    tell", never as "no paths". Adds `-z` and reads BYTES:
    - without `-z`, git C-quotes any non-ASCII name (`"caf\\303\\251.txt"`), so a
      path built from it names no file and matches no glob;
    - with `-z` but in text mode, the raw bytes are decoded strictly as UTF-8, so ONE
      non-UTF-8 name anywhere raised out of the caller -- every commit (the hook), and
      every `ddflow lesson add` (the inventory scan), failed; text mode also rewrote a
      `\\r` in a name to `\\n`.
    `os.fsdecode` (surrogateescape) round-trips any name. Both bugs were found by roborev
    (on 40950c9 and 4f54455), the second introduced by the fix for the first, and then
    found again in a third caller (on e8543f9) -- hence one helper rather than three.

    `args` are git's arguments up to (not including) `-z`; pass any pathspec after a
    `"--"` in ``args`` as usual -- `-z` is inserted before it.
    """
    argv = list(args)
    at = argv.index("--") if "--" in argv else len(argv)
    argv.insert(at, "-z")
    p = P.run(["git", "-C", str(repo), *argv], capture_output=True, timeout=timeout)
    if p.returncode != 0:
        return None
    return [os.fsdecode(x) for x in p.stdout.split(b"\0") if x]


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


# One implementation, in `core` -- the branch-name rules there build on it.
safe_name = _core_safe_name


@dataclass
class Worktree:
    item: str
    path: Path
    branch: str
    base: str
    created: bool = False


def create(repo: Path, cfg: Config, item_id: str, *, base: str = "", branch: str = "") -> Worktree:
    """Create (or adopt) the worktree for an item. Idempotent by design.

    Adoption matters for recovery: after a crash the agent restarts, finds the
    worktree already present, and continues in it rather than erroring or — far worse —
    creating a second tree and abandoning the first with its work inside.

    ``base`` and ``branch`` come from `core.flow` when a branching model names them (a
    gitflow ``feature/`` branch off ``develop``, a task stacked on a dependency's
    branch); empty keeps the trunk defaults.
    """
    root = repo_root(repo)
    base = base or cfg.worktree.base_ref or default_branch(root)
    name = safe_name(item_id)
    branch = branch or f"{cfg.worktree.branch_prefix}{name}"
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
    """Merge the task branch into base, never switching any checkout's branch."""
    return merge_into(repo, cfg, wt.base, wt.branch, message=message or f"merge {wt.item}")


def checked_out_at(repo: Path, branch: str) -> Path | None:
    """The worktree (primary or linked) that has ``branch`` checked out, if any."""
    for entry in list_worktrees(repo):
        if entry.get("branch", "") == f"refs/heads/{branch}":
            return Path(entry["worktree"])
    return None


def merge_into(repo: Path, cfg: Config, target: str, source: str, *, message: str) -> GitResult:
    """Merge ``source`` into ``target`` without switching any checkout's branch.

    Three cases, by where ``target`` is checked out:

    * **in the primary** — merge there, as ddflow always has.
    * **nowhere** — merge in a throwaway worktree of ``target``, then remove it. This
      is what gitflow needs: a hotfix lands on production AND develop, and the primary
      can be on at most one of them. It used to be refused ("check out X yourself"),
      which under gitflow meant every hotfix and every release stopped for a person.
      The throwaway tree touches no one's files; only the branch ref moves.
    * **in some other linked worktree** — refused. Merging there changes files under
      whoever is working in it, which is exactly the live-session hazard rule 1 exists
      for, and a throwaway tree is impossible because git allows a branch checked out
      in one place only.
    """
    root = repo_root(repo)
    if not git(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{target}").ok:
        return GitResult(
            GIT_REFUSED,
            "",
            f"target branch {target!r} does not exist. Create it (for gitflow: "
            f"`git branch {target} <production>`), or set [flow] / [worktree].base_ref.",
        )
    where = checked_out_at(root, target)
    if where is not None and where.resolve() != root.resolve():
        return GitResult(
            GIT_REFUSED,
            "",
            (
                f"{target!r} is checked out in the worktree {where}. ddflow will NOT merge "
                f"into a tree someone may be working in, and git allows a branch checked "
                f"out in only one place. Finish or move that session, then re-run."
            ),
        )
    if where is not None:
        return _merge_here(root, cfg, source, message)
    wt_root = (root / cfg.worktree.root).resolve()
    wt_root.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".merge-{safe_name(target)}-", dir=wt_root))
    tmp.rmdir()  # `worktree add` wants to create it
    add = git(root, "worktree", "add", str(tmp), target)
    if not add.ok:
        return GitResult(add.code, add.out, f"could not stage a merge of {target}: {add.err}")
    try:
        r = _merge_here(tmp, cfg, source, message)
        if not r.ok:
            git(tmp, "merge", "--abort")
        return r
    finally:
        git(root, "worktree", "remove", "--force", str(tmp))
        git(root, "worktree", "prune")


def _merge_here(tree: Path, cfg: Config, source: str, message: str) -> GitResult:
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
    args += ["-m", message, source]
    r = git(tree, *args)
    if r.ok and cfg.worktree.merge_strategy == "squash":
        # `git merge --squash` STAGES the result and commits nothing -- `-m` is accepted
        # and ignored. So a squash "merge" used to report success with the work sitting
        # uncommitted in the primary, the task branch never reachable from base, and
        # `remove_on_merge` then refusing to delete a tree it believed unmerged.
        # Nothing to commit means the branch was already contained; that is success.
        if git(tree, "diff", "--cached", "--quiet").ok:
            return r
        c = git(tree, "commit", "-m", message)
        if not c.ok:
            return GitResult(c.code, c.out, f"squash staged but commit failed: {c.err}")
        return c
    return r


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


#: What `merge` and `remove` return to mean "refused on a precondition" as opposed to
#: "git failed". It maps to the surfaces' REFUSED (3), and naming it here — beside the
#: functions that return it — keeps the two exit vocabularies from being silently
#: conflated by whoever reads only one of them.
GIT_REFUSED = 2


def absolutise(repo: Path, data: Any) -> Any:
    """Plain-data view with worktree paths resolved to absolute.

    Storage portable, interface usable. The LOG stores worktree paths relative to the
    repo root, which is what makes a committed log true on every checkout; a CALLER needs
    a path it can `cd` to. The conversion happens at the boundary, and it happens HERE
    rather than in `surfaces/context.py` because the api layer hands the same objects to
    the same callers and cannot import a surface to do it.
    """
    if not isinstance(data, dict):
        return data
    out = dict(data)
    for key in ("worktree", "path"):
        val = out.get(key)
        if isinstance(val, str) and val and not os.path.isabs(val):
            out[key] = str(load_path(repo, val))
    if isinstance(out.get("lease"), dict):
        lv = out["lease"].get("worktree")
        if isinstance(lv, str) and lv and not os.path.isabs(lv):
            out["lease"] = {**out["lease"], "worktree": str(load_path(repo, lv))}
    return out


# -- remotes and tags (RESEARCH R16) -------------------------------------------------


def push(path: Path, remote: str, branch: str, *, timeout: int = 300) -> GitResult:
    """Push ``branch`` and set its upstream. Never ``--force``.

    A rejected push means someone else moved the remote branch -- a reviewer's suggested
    change committed from the forge UI is the usual one. Forcing would discard it, so
    the rejection is returned for the agent to `git pull` and retry.
    """
    return git(path, "push", "--set-upstream", remote, f"{branch}:{branch}", timeout=timeout)


def fetch(repo: Path, remote: str, *refs: str) -> GitResult:
    return git(repo, "fetch", "--quiet", remote, *refs)


def rev(repo: Path, ref: str) -> str:
    r = git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    return r.out if r.ok else ""


def version_tags(repo: Path, prefix: str) -> list[tuple[str, str]]:
    """``(tag, version)`` for every tag ``<prefix>MAJOR.MINOR.PATCH``, newest version first."""
    from ..core.flow import parse_version

    r = git(repo, "tag", "--list", f"{prefix}*")
    found = []
    for line in r.out.splitlines() if r.ok else []:
        name = line.strip()
        ver = name[len(prefix) :]
        parsed = parse_version(ver)
        if parsed is not None:
            found.append((parsed, name, ver))
    return [(name, ver) for _, name, ver in sorted(found, reverse=True)]


def reachable_tag(repo: Path, prefix: str, ref: str) -> tuple[str, str]:
    """The highest version tag reachable from ``ref`` -- the version that ref is AT.

    Not simply the highest tag in the repo: under gitflow a hotfix tag on production is
    not on develop until it is back-merged, and a version computed from a tag the branch
    does not contain would count the same commits twice.
    """
    for tag, ver in version_tags(repo, prefix):
        if git(repo, "merge-base", "--is-ancestor", tag, ref).ok:
            return tag, ver
    return "", ""


def log_messages(repo: Path, since: str, ref: str) -> list[str]:
    """Full commit messages in ``since..ref`` (all of ``ref`` when ``since`` is empty).

    ``--no-merges``: a merge commit carries no change of its own, and counting one made
    every gitflow release look unreleased -- the back-merge of the tag into develop is a
    commit after the tag, so develop always had "one commit to release".
    """
    rng = f"{since}..{ref}" if since else ref
    r = git(repo, "log", "--no-merges", "--format=%B%x00", rng)
    if not r.ok:
        return []
    return [m.strip() for m in r.out.split("\x00") if m.strip()]


def tag(repo: Path, name: str, ref: str, message: str) -> GitResult:
    """An ANNOTATED tag: it records who and when, and `git describe` sees it by default."""
    return git(repo, "tag", "--annotate", name, "-m", message, ref)
