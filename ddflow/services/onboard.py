"""Onboarding stage 2: what the old workflow left behind, and what is safe to remove.

The prompt is explicit (onboard.md lines 13-18) and every rule here exists because the
cheap alternative is wrong: `merge-base --is-ancestor` is the only honest "merged", a
matching commit message is not evidence; a worktree with anything uncommitted is holding
work even when its branch is merged; a `locked` worktree belongs to an agent harness
(Claude Code locks the worktrees it spawns) and removing it ends that session's working
directory, so the lock's process is checked and a live owner is never touched; stashes
are shared by every worktree and are never popped or dropped here.

Nothing is removed until `apply`, and then only what is merged, clean, and either
unlocked or locked by a process that is GONE. Everything else is reported for the
operator to land, import (`ddflow_import` proposes unmerged branches) or leave.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Config
from ..infra import worktree as W
from .jobs import alive

#: How a `git worktree lock --reason ...` names the process that owns it. The reason is
#: free text, so a missing pid is "cannot tell", never "dead".
_LOCK_PID = re.compile(r"\bpid[ =:]*(\d+)\b", re.I)
#: How many unmerged commits/subjects the report quotes before summarising.
_EXAMPLES = 3
#: How many uncommitted files the report names before saying "and more".
_DIRTY_EXAMPLES = 3


@dataclass(frozen=True)
class Leftover:
    """One thing the old workflow left behind, with the only action that is safe."""

    kind: str  #: worktree | branch | stash | remote
    name: str  #: a path, a branch, a stash ref
    state: str  #: merged | unique | dirty | locked | locked-stale | remote
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
    checked_out: dict[str, Path] = field(default_factory=dict)


def _commits(repo: Path, base: str, ref: str) -> list[str]:
    r = W.git(repo, "log", "--oneline", f"{base}..{ref}")
    return [line.strip() for line in r.out.splitlines() if line.strip()] if r.ok else []


def _commit_detail(repo: Path, base: str, ref: str) -> str:
    lines = _commits(repo, base, ref)
    if not lines:
        return f"nothing in {base}..{ref}?"
    head = ", ".join(lines[:_EXAMPLES])
    more = f" and {len(lines) - _EXAMPLES} more" if len(lines) > _EXAMPLES else ""
    return f"{len(lines)} commit(s) not in {base}: {head}{more}"


def _lock_state(reason: str) -> tuple[str, str, str]:
    """(state, action, detail) for a locked worktree, from its lock reason.

    A pid that is alive keeps the tree; a pid that is gone is a stale lock from a
    crashed session and the tree can be removed; a reason without a pid cannot tell.
    """
    match = _LOCK_PID.search(reason)
    if not match:
        return (
            "locked",
            "keep",
            f"locked ({reason}); no pid in the lock, cannot tell if its owner is alive",
        )
    pid = int(match.group(1))
    if alive(pid):
        return (
            "locked",
            "keep",
            f"lock process {pid} is alive; removing this ends that session's working directory",
        )
    return ("locked-stale", "remove", f"lock process {pid} is gone; the lock is stale ({reason})")


def preflight(repo: Path) -> list[Leftover]:
    """Everything left behind in `repo`, each classified merged / unique / not ours."""
    repo = Path(repo)
    base = W.default_branch(repo)
    ctx = _Context(repo=repo, base=base)
    out: list[Leftover] = []
    out.extend(_worktrees(ctx))
    out.extend(_branches(ctx))
    out.extend(_stashes(repo))
    out.extend(_remotes(ctx))
    return out


def _worktrees(ctx: _Context) -> list[Leftover]:
    out: list[Leftover] = []
    primary = ctx.repo.resolve()
    for entry in W.list_worktrees(ctx.repo):
        path = Path(entry["worktree"])
        branch = str(entry.get("branch", "")).removeprefix("refs/heads/")
        if path.resolve() == primary:
            continue  # the checkout we are reporting from, never a candidate
        if not branch:
            out.append(
                Leftover(
                    "worktree", str(path), "unique", "keep", "detached HEAD; no branch to check", ""
                )
            )
            continue
        ctx.checked_out[branch] = path
        dirty = W.dirty(W.Worktree(item="", path=path, branch=branch, base=ctx.base))
        if dirty:
            shown = ", ".join(dirty[:_DIRTY_EXAMPLES]) + (
                " and more" if len(dirty) > _DIRTY_EXAMPLES else ""
            )
            out.append(
                Leftover(
                    "worktree",
                    str(path),
                    "dirty",
                    "keep",
                    f"{len(dirty)} uncommitted file(s): {shown}",
                    branch,
                )
            )
            continue
        if not W.is_merged(ctx.repo, branch, ctx.base):
            out.append(
                Leftover(
                    "worktree",
                    str(path),
                    "unique",
                    "keep",
                    _commit_detail(ctx.repo, ctx.base, branch),
                    branch,
                )
            )
            continue
        lock = str(entry.get("locked", ""))
        if lock:
            state, action, detail = _lock_state(lock)
            out.append(Leftover("worktree", str(path), state, action, detail, branch))
            continue
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


def apply(repo: Path) -> list[str]:
    """Remove the merged-and-clean worktrees and branches; never anything else.

    A stale lock is unlocked first, because `git worktree remove` refuses a locked tree;
    a live lock never reaches here (its action is keep). Every outcome is a line, so a
    refusal by git itself (`branch -d` refuses an unmerged branch) is reported, not lost.
    """
    repo = Path(repo)
    cfg = Config.load(repo)
    base = W.default_branch(repo)
    out: list[str] = []
    for item in preflight(repo):
        if item.action != "remove":
            continue
        if item.kind == "worktree":
            path = Path(item.name)
            if item.state == "locked-stale":
                W.git(repo, "worktree", "unlock", str(path))
            worktree = W.Worktree(item="", path=path, branch=item.branch, base=base)
            r = W.remove(repo, cfg, worktree)
            if r.ok:
                out.append(f"removed worktree {path} and its branch {item.branch}")
            else:
                out.append(
                    f"could not remove {path}: {(r.err or r.out).strip() or f'exit {r.code}'}"
                )
        elif item.kind == "branch":
            r = W.git(repo, "branch", "-d", item.name)
            if r.ok:
                out.append(f"deleted branch {item.name}")
            else:
                out.append(
                    f"could not delete {item.name}: {(r.err or r.out).strip() or f'exit {r.code}'}"
                )
    return out
