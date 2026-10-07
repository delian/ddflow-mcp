"""Onboarding stage 2: what the old workflow left behind, and what is safe to remove.

The prompt is explicit (onboard.md lines 13-18) and every rule here exists because the
cheap alternative is wrong: `merge-base --is-ancestor` is the only honest "merged", a
matching commit message is not evidence; a worktree is holding work when its status
shows anything beyond caches; a `locked` worktree belongs to an agent harness (Claude
Code locks the worktrees it spawns), so it is REPORTED -- owner alive or gone -- and
never removed here, because removing it ends that session's working directory; stashes
are shared by every worktree and are never popped or dropped.

Nothing is removed until `apply`, and then only what was merged, clean and unlocked --
and, when the operator names what they approved, only those. Everything else is
reported for the operator to land, import (`ddflow_import` proposes unmerged branches)
or leave.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from ..config import Config
from ..core.model import fold
from ..infra import worktree as W
from ..infra.log import EventLog
from . import cleanup as C
from .jobs import alive

#: How a `git worktree lock --reason ...` names the process that owns it. The reason is
#: free text, so a missing pid is "cannot tell", never "dead" -- and even a "gone" pid
#: is only reported: a pid namespace can make a live owner look gone.
_LOCK_PID = re.compile(r"\bpid[ =:]*(\d+)\b", re.I)
#: How many unmerged commits/subjects the report quotes before summarising.
_EXAMPLES = 3
#: Porcelain v1 puts the status letters and a space in front of the path.
_XY_WIDTH = 3
#: Directory names that are disposable caches. The prompt says a worktree is unmerged
#: by "anything beyond caches", and nearly every tree here has a `.venv` or a
#: `__pycache__`; anything untracked or ignored that is NOT one of these is work.
_CACHES = frozenset(
    {
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".venv",
        "venv",
        "node_modules",
        ".cache",
    }
)


@dataclass(frozen=True)
class Leftover:
    """One thing the old workflow left behind, with the only action that is safe."""

    kind: str  #: worktree | branch | stash | remote
    name: str  #: a path, a branch, a stash ref
    state: str  #: merged | unique | dirty | unreadable | locked | locked-stale | remote
    action: str  #: remove | keep
    detail: str = ""
    branch: str = ""  #: the branch a worktree is on, when it has one

    def to_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "name": self.name,
            "state": self.state,
            "action": self.action,
            "detail": self.detail,
            "branch": self.branch,
        }


@dataclass
class _Context:
    repo: Path
    base: str
    where: Path  #: the caller's own checkout, never a candidate
    primary: Path  #: the main working tree, never a candidate
    checked_out: dict[str, Path] = field(default_factory=dict)


def _is_cache(name: str) -> bool:
    return any(part in _CACHES for part in PurePosixPath(name).parts)


def _status(path: Path) -> tuple[bool, list[str], list[str]]:
    """(readable, work files, non-cache ignored files) for one tree.

    `--ignored=matching` is what makes ignored work visible at all: `git worktree
    remove` deletes ignored files without complaint, so a tree that "looks clean" while
    holding a `.env` or a hand-edited local file is exactly the tree that must not be
    removed (rubber_duck on f0d27314). A status that could not run is NOT clean.
    """
    r = W.git(path, "status", "--porcelain", "-z", "--ignored=matching")
    if not r.ok:
        return False, [], []
    work: list[str] = []
    ignored: list[str] = []
    fields = r.out.split("\0")
    index = 0
    while index < len(fields):
        entry = fields[index]
        index += 1
        if not entry.strip():
            continue
        code = entry[:2]
        name = entry[_XY_WIDTH:].strip() if len(entry) > _XY_WIDTH else ""
        if not name:
            continue
        if "R" in code or "C" in code:
            index += 1  # -z: a rename/copy carries its source as the next field
        if code == "!!":
            if not _is_cache(name):
                ignored.append(name)
        elif not _is_cache(name):
            work.append(name)
    return True, work, ignored


def _commits(repo: Path, base: str, ref: str) -> list[str] | None:
    """The commits on `ref` not in `base`; None when git itself could not answer."""
    r = W.git(repo, "log", "--oneline", f"{base}..{ref}")
    if not r.ok:
        return None
    return [line.strip() for line in r.out.splitlines() if line.strip()]


def _commit_detail(repo: Path, base: str, ref: str) -> str:
    lines = _commits(repo, base, ref)
    if lines is None:
        return f"could not list {base}..{ref} (git failed); inspect by hand"
    if not lines:
        return f"nothing in {base}..{ref}?"
    head = ", ".join(lines[:_EXAMPLES])
    more = f" and {len(lines) - _EXAMPLES} more" if len(lines) > _EXAMPLES else ""
    return f"{len(lines)} commit(s) not in {base}: {head}{more}"


def _lock_state(reason: str) -> tuple[str, str]:
    """(state, what to say) about a locked worktree -- and the OWNER, alive or gone.

    A stale lock is REPORTED, not acted on: a pid namespace can make a live owner look
    gone, so removing a locked tree stays the operator's call (critic on f0d27314).
    """
    match = _LOCK_PID.search(reason)
    if not match:
        return "locked", f"locked ({reason}); no pid in the lock, cannot tell if its owner is alive"
    pid = int(match.group(1))
    if alive(pid):
        return "locked", f"lock process {pid} is alive; this is a session's working directory"
    return (
        "locked-stale",
        f"lock process {pid} is gone (stale lock: {reason}); remove it by hand if you are sure",
    )


def _file_detail(files: list[str], what: str) -> str:
    shown = ", ".join(files[:_EXAMPLES]) + (" and more" if len(files) > _EXAMPLES else "")
    return f"{len(files)} {what}: {shown}"


def preflight(repo: Path) -> list[Leftover]:
    """Everything left behind in `repo`, each classified merged / unique / not ours."""
    repo = Path(repo)
    base = W.default_branch(repo)
    ctx = _Context(
        repo=repo,
        base=base,
        where=repo.resolve(),
        primary=W.repo_root(repo).resolve(),
    )
    out: list[Leftover] = []
    out.extend(_worktrees(ctx))
    out.extend(_branches(ctx))
    out.extend(_stashes(repo))
    out.extend(_remotes(ctx))
    return out


def _worktrees(ctx: _Context) -> list[Leftover]:
    out: list[Leftover] = []
    for entry in W.list_worktrees(ctx.repo):
        path = Path(entry["worktree"])
        branch = str(entry.get("branch", "")).removeprefix("refs/heads/")
        # record EVERY checked-out branch, including the caller's and the primary's,
        # before the skip: `_branches` must not offer to delete a branch that is
        # checked out somewhere, and `git branch -d` would refuse it anyway (roborev
        # on 876f5b79).
        if branch:
            ctx.checked_out[branch] = path
        # The checkout we were run from, the main working tree, and the branch whose
        # deletion would be catastrophic are never candidates -- including when this
        # runs from a linked worktree or a subdirectory, where the "primary" is an
        # entry like any other (rubber_duck on f0d27314).
        if path.resolve() in (ctx.where, ctx.primary) or branch == ctx.base:
            continue
        if not branch:
            out.append(
                Leftover(
                    "worktree", str(path), "unique", "keep", "detached HEAD; no branch to check"
                )
            )
            continue
        problems: list[str] = []
        readable, work, ignored = _status(path)
        merged = W.is_merged(ctx.repo, branch, ctx.base)
        if not merged:
            problems.append(_commit_detail(ctx.repo, ctx.base, branch))
        if work:
            problems.append(_file_detail(work, "uncommitted file(s)"))
        if ignored:
            problems.append(_file_detail(ignored, "ignored file(s) that are not caches"))
        lock = str(entry.get("locked", ""))
        if not readable:
            out.append(
                Leftover(
                    "worktree",
                    str(path),
                    "unreadable",
                    "keep",
                    "git status could not run; nothing removed blind",
                    branch,
                )
            )
        elif not merged:
            detail = "; ".join(problems)
            if lock:
                detail = f"{detail}; {_lock_state(lock)[1]}"
            out.append(Leftover("worktree", str(path), "unique", "keep", detail, branch))
        elif work or ignored:
            detail = "; ".join(problems)
            if lock:
                detail = f"{detail}; {_lock_state(lock)[1]}"
            out.append(Leftover("worktree", str(path), "dirty", "keep", detail, branch))
        elif lock:
            state, detail = _lock_state(lock)
            out.append(Leftover("worktree", str(path), state, "keep", detail, branch))
        else:
            out.append(
                Leftover(
                    "worktree",
                    str(path),
                    "merged",
                    "remove",
                    f"clean and merged into {ctx.base}",
                    branch,
                )
            )
    return out


def _branches(ctx: _Context) -> list[Leftover]:
    out: list[Leftover] = []
    for name in W.branches(ctx.repo):
        if name == ctx.base or name in ctx.checked_out:
            continue  # the default is its own ancestor; a checked-out branch is its worktree's
        if W.is_merged(ctx.repo, name, ctx.base):
            out.append(Leftover("branch", name, "merged", "remove", f"merged into {ctx.base}"))
        else:
            out.append(
                Leftover("branch", name, "unique", "keep", _commit_detail(ctx.repo, ctx.base, name))
            )
    return out


def _stashes(repo: Path) -> list[Leftover]:
    r = W.git(repo, "stash", "list")
    out: list[Leftover] = []
    for line in r.out.splitlines():
        text = line.strip()
        if not text:
            continue
        ref, _, message = text.partition(": ")
        out.append(
            Leftover(
                "stash",
                ref,
                "unique",
                "keep",
                f"{message} (shared by every worktree; never dropped here)",
            )
        )
    return out


def _remotes(ctx: _Context) -> list[Leftover]:
    r = W.git(ctx.repo, "for-each-ref", "--format=%(refname:short)", "refs/remotes/")
    out: list[Leftover] = []
    for line in r.out.splitlines():
        name = line.strip()
        if not name or name.endswith("/HEAD"):
            continue
        if not W.is_merged(ctx.repo, name, ctx.base):
            out.append(
                Leftover(
                    "remote",
                    name,
                    "remote",
                    "keep",
                    f"{_commit_detail(ctx.repo, ctx.base, name)}; not yours to delete",
                )
            )
    return out


def render(leftovers: list[Leftover]) -> str:
    """The operator's report: one line per thing, and the action it will get."""
    if not leftovers:
        return "nothing left behind: no other worktrees, no unmerged branches, no stashes"
    lines = []
    for item in leftovers:
        detail = f" -- {item.detail}" if item.detail else ""
        lines.append(f"{item.action} {item.kind} {item.name} [{item.state}]{detail}")
    removes = sum(1 for item in leftovers if item.action == "remove")
    lines.append(
        f"{len(leftovers)} leftover(s): {removes} merged and clean (removed only with apply), "
        f"{len(leftovers) - removes} holding work or not ours"
    )
    return "\n".join(lines)


def _branch_exists(repo: Path, name: str) -> bool:
    return W.git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{name}").ok


def _held(repo: Path, cfg: Config, log: EventLog | None, path: Path, branch: str) -> str:
    """Why the tree must be left alone (a live lease, or adopted by an item), or ""."""
    if log is None:
        return ""
    root = W.repo_root(repo)
    return C._protected(root, cfg, fold(log.read_all(), strict=False)).why(str(path), branch)[1]


def _remove_worktree(repo: Path, cfg: Config, item: Leftover) -> dict[str, str]:
    """Remove one approved worktree and its branch, reporting git's own answer.

    `W.remove` deletes the branch itself but ignores that delete's result; a branch
    that survived is checked for and reported as a failure, because the report must not
    say "and its branch" when the branch is still there (critic on f0d27314).
    """
    path = Path(item.name)
    base = W.default_branch(repo)
    # Re-inspect IMMEDIATELY before the forced removal: force skips W.remove's own
    # guard, so anything written into the tree after the report would be deleted
    # without warning (roborev on 876f5b79).
    readable, work, ignored = _status(path)
    if not readable or work or ignored or not W.is_merged(repo, item.branch, base):
        return {
            "name": item.name,
            "kind": "worktree",
            "outcome": "failed",
            "detail": "changed or no longer merged since the report; re-run the preflight",
        }
    worktree = W.Worktree(item="", path=path, branch=item.branch, base=base)
    had_branch = bool(item.branch) and _branch_exists(repo, item.branch)
    # force only AFTER this module's own inspection: the tree is merged, unlocked and
    # holds nothing beyond caches. `W.remove`'s check counts an untracked `__pycache__`
    # or `.venv` as work and would refuse the very trees the report called removable
    # (critic on f0d27314); nothing else reaches here, because dirty/unique/locked items
    # never carry action "remove". In an adopted project, under the log lock with the
    # leases re-read -- what `cleanup` does -- and recorded (B5e83fb22cb).
    log = (
        EventLog(
            repo,
            cfg.agent.id or "",
            log_cfg=cfg.log,
            lock_timeout_s=cfg.lease.acquire_timeout_s,
        )
        if (repo / ".ddflow" / "events").is_dir()
        else None
    )
    with log.transaction() if log else contextlib.nullcontext():
        held = _held(repo, cfg, log, path, item.branch)
        if held:
            # Refused, not failed: coordination said no, and the tree was left as it is.
            return {"name": item.name, "kind": "worktree", "outcome": "refused", "detail": held}
        r = W.remove(repo, cfg, worktree, force=True)
        if r.ok and log:
            C.record_removed(log, W.repo_root(repo), str(path))
    if not r.ok:
        return {
            "name": item.name,
            "kind": "worktree",
            "outcome": "failed",
            "detail": (r.err or r.out).strip() or f"git exit {r.code}",
        }
    if not had_branch:
        return {
            "name": item.name,
            "kind": "worktree",
            "outcome": "removed",
            "detail": "worktree removed",
        }
    if not _branch_exists(repo, item.branch):
        return {
            "name": item.name,
            "kind": "worktree",
            "outcome": "removed",
            "detail": f"worktree and branch {item.branch} removed",
        }
    rb = W.git(repo, "branch", "-d", item.branch)
    if rb.ok:
        return {
            "name": item.name,
            "kind": "worktree",
            "outcome": "removed",
            "detail": f"worktree and branch {item.branch} removed",
        }
    return {
        "name": item.name,
        "kind": "worktree",
        "outcome": "failed",
        "detail": f"worktree removed, branch {item.branch} not deleted: {(rb.err or rb.out).strip() or f'git exit {rb.code}'}",
    }


def apply(repo: Path, names: Iterable[str] | None = None) -> list[dict[str, str]]:
    """Remove the approved merged-and-clean items; every outcome is a record.

    `names` is the operator's approval: None means everything the report marked
    `remove`; a list means exactly those names. A name that was NOT marked remove is
    refused rather than acted on, so a typo cannot delete a branch. Locked trees and
    anything holding work never reach the removal path at all.
    """
    repo = Path(repo)
    cfg = Config.load(repo)
    wanted = None if names is None else set(names)
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for item in preflight(repo):
        if wanted is not None and item.name in wanted:
            seen.add(item.name)
        if item.action != "remove":
            if wanted is not None and item.name in wanted:
                out.append(
                    {
                        "name": item.name,
                        "kind": item.kind,
                        "outcome": "refused",
                        "detail": f"not marked for removal [{item.state}]",
                    }
                )
            continue
        if wanted is not None and item.name not in wanted:
            continue
        if item.kind == "worktree":
            out.append(_remove_worktree(repo, cfg, item))
        elif item.kind == "branch":
            r = W.git(repo, "branch", "-d", item.name)
            if r.ok:
                out.append(
                    {
                        "name": item.name,
                        "kind": "branch",
                        "outcome": "removed",
                        "detail": "branch deleted",
                    }
                )
            else:
                out.append(
                    {
                        "name": item.name,
                        "kind": "branch",
                        "outcome": "failed",
                        "detail": (r.err or r.out).strip() or f"git exit {r.code}",
                    }
                )
    if wanted is not None:
        # An approval that matched nothing is a vacuous pass unless it is said: a
        # mistyped name used to return ok with every list empty (roborev on 876f5b79).
        for name in sorted(wanted - seen):
            out.append(
                {
                    "name": name,
                    "kind": "unknown",
                    "outcome": "refused",
                    "detail": "no such leftover; nothing was matched",
                }
            )
    return out
