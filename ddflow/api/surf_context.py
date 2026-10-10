"""What the CLI's per-invocation context (`surfaces/context.py`) reads from the domain
layers: one forward each, no logic. The context owns the log, store, worktree and harness
handles of the connection itself; the gate roster and the question of whose working tree
the caller stands in are the domain's, and are asked here."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ..config import Config
from ..services import gates as G
from ..services.tree_owner import foreign_tree_owner as _foreign_tree_owner


def load_gates(repo: Path, cfg: Config) -> dict[str, Any]:
    """Every gate definition in force: the shipped ones, the project's, the local ones."""
    return G.load_gates(repo, cfg)


def foreign_tree_owner(repo: Path, called_from: Path, agent: str, events: Iterable) -> str:
    """The identity whose working tree ``called_from`` is, when it is not ``agent``'s; ""."""
    return _foreign_tree_owner(repo, called_from, agent, events)
