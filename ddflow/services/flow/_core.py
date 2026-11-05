"""PR flow: where an item lands, and how its worktree forks.

Part of the former ``ddflow/services/flow.py``, split into submodules that stay under
30k chars each (rule r-small-files); see the package docstring for the full story.
"""

from __future__ import annotations

from pathlib import Path

from ...config import Config
from ...core import flow as F
from ...core.model import REVIEW, Item, State, fold
from ...core.schedule import inherited_deps
from ...infra import worktree as W
from ...infra.log import EventLog


def _state(log: EventLog) -> State:
    return fold(log.read_all(), strict=False)


def target(repo: Path, cfg: Config, it: Item, st: State) -> str:
    """Where ``it`` lands: its release line's branch, or what `model` says for the current one."""
    return F.target_branch(it, cfg, W.default_branch(repo), F.effective_line(st, it))


def stacked_on(st: State, it: Item) -> Item | None:
    """The dependency whose unmerged branch ``it`` forked from, if it is still unmerged."""
    if not it.base:
        return None
    for other in st.items.values():
        if other.id != it.id and other.branch == it.base and other.state == REVIEW:
            return other
    return None


def fork_point(repo: Path, cfg: Config, st: State, it: Item) -> tuple[str, str]:
    """(base, branch) for a new worktree: stacked on a dependency, or off the target."""

    branch = F.branch_name(it, cfg)
    stack = F.stack_base(st, it, cfg, [d for _, d in inherited_deps(st, it, cfg)])
    if stack.base:
        return stack.base, branch
    base = target(repo, cfg, it, st)
    if cfg.flow.integration == "pr":
        # Fork from what the forge HAS, not from a local branch nobody pulls: in PR mode
        # every merge happens remotely, so the local base is stale by construction.
        remote = cfg.flow.remote
        if W.fetch(repo, remote, base).ok and W.rev(repo, f"{remote}/{base}"):
            return f"{remote}/{base}", branch
    return base, branch
