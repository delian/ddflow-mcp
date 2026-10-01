"""Whose working tree a CLI claim is standing in (B11e4c5a185).

`claim` adopts the linked worktree it is run from: an agent its harness already isolated
works there, and a second tree would strand its work. But a subagent's shell often runs
in its PARENT's harness tree, and `ddflow --agent sub-1 claim T1` from there bound T1 to
the parent's tree and branch -- the CLI twin of B7c7a0d9222, where the MCP surface knows
the connection's identity and can compare. The CLI has no connection, so it asks the log.

A linked tree is someone ELSE's when the identity derived from it -- what an agent
standing in it is called by default -- is not the caller, and that identity has written
to the log: somebody has been working there under that name. Then the claim is answered
as from the primary, with a tree of its own. Everything else still adopts:

* no identity named (the caller IS the derived one, as before);
* the tree's own identity named explicitly;
* a named agent alone in a tree nobody has worked in under its derived identity -- the
  harness-isolated agent that names itself, which adoption exists for.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from ..infra import worktree as W
from ..infra.log import default_agent_id, tree_agent_ids


def foreign_tree_owner(repo: Path, called_from: Path, agent: str, events: Iterable) -> str:
    """The identity whose working tree ``called_from`` is, when it is not ``agent``'s; "".

    ``agent`` is the caller's resolved identity; ``events`` the log, read once by the
    caller (a claim reads it anyway).
    """
    here = W.current(called_from)
    if here is None or not agent or agent == default_agent_id(repo):
        return ""
    own = tree_agent_ids(here.path, repo)
    if agent in own:
        return ""
    for e in events:
        if e.agent in own:
            return e.agent
    return ""
