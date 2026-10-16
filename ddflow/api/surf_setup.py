"""What the setup, review, operations and CI commands (`surfaces/commands/`) read from the
domain layers: one forward each, no logic of their own. A command module prints and sets an
exit code; where a fact comes from is asked here, so a surface never imports a service or
the infra layer directly (the `surfaces-through-api` contract)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.model import State
from ..infra import worktree as W
from ..services import gates as G
from ..services import help as H
from ..services import importer as IM
from ..services import review as R
from ..services import reviewer_trust as RT
from ..services.adopt import rules_status
from ..services.tree_owner import foreign_tree_owner

REVIEWED = R.REVIEWED
ReviewerRefused = RT.ReviewerRefused
IMPORT_KINDS = IM.KINDS


def tree_is_dirty(tree: Path, rel: str) -> bool:
    """Whether ``rel`` under ``tree`` shows in `git status`; an unreadable status counts."""
    return W.status(tree, rel) != []


def head_here_or_repo(repo: Path, where: Path | None = None) -> str:
    """HEAD of ``where`` (default: the current directory) when it is a worktree of THIS
    repository, else the repository's HEAD."""
    here = where if where is not None else Path.cwd()
    common = [
        W.git(p, "rev-parse", "--path-format=absolute", "--git-common-dir") for p in (here, repo)
    ]
    if all(r.ok for r in common) and Path(common[0].out).resolve() == Path(common[1].out).resolve():
        return W.rev(here, "HEAD") or "HEAD"
    return "HEAD"


def caller_tree(repo: Path, called_from: Path, foreign: bool, agent: str, events: Any) -> Path:
    """The tree a command's default ref is read from: ``called_from`` when it is a linked
    worktree or another identity's tree was swapped for the primary (``foreign``); else the
    shell's directory when that is a tree of ``repo`` and the caller's own (an explicit
    --repo makes ``called_from`` the primary, whatever the shell stands in)."""
    if foreign or W.current(called_from) is not None:
        return called_from
    here = Path.cwd()
    common = [
        W.git(p, "rev-parse", "--path-format=absolute", "--git-common-dir") for p in (here, repo)
    ]
    if not all(r.ok for r in common):
        return called_from
    if Path(common[0].out).resolve() != Path(common[1].out).resolve():
        return called_from
    return called_from if foreign_tree_owner(repo, here, agent, events) else here


def rules_needing_attention(tree: Path) -> list[Any]:
    """The project-rules surface findings that want an operator's eye."""
    return [r for r in rules_status(tree) if r.needs_attention]


def help_topics() -> list[str]:
    return list(H.TOPICS)


def approve_gate(*args: Any, **kwargs: Any) -> str:
    """`gates.approve`: the human checkpoint of a gate (`ddflow approve`)."""
    return G.approve(*args, **kwargs)


def reviewer_presets() -> dict[str, dict[str, Any]]:
    return R.PRESETS


def reviewers_named(repo: Path, name: str) -> list[Any]:
    """The configured reviewers, or just the one called ``name``."""
    return [r for r in R.load_reviewers(repo) if not name or r.name == name]


def probe_reviewer(reviewer: Any, diff: str, intent: str) -> Any:
    """One live review of a canned diff (`ddflow reviewers test`)."""
    return R.review(reviewer, diff, intent=intent)


def reviewer_agent_marker(agent: str) -> str:
    """Why this identity is an agent's ("" for a person): a command reviewer needs a person."""
    return RT.agent_marker(agent)


def reviewers_pending(repo: Path, state: State) -> list[dict[str, Any]]:
    return RT.pending(repo, state)


def approve_reviewer(repo: Path, name: str, *, requested_agent: str, note: str) -> str:
    return RT.approve(repo, name, requested_agent=requested_agent, note=note)
