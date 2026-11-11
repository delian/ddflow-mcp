"""The command log a surface records, and the repo root its path is resolved from.

`ddflow.services.cmdlog` is a service and `ddflow.infra.worktree.repo_root` is infra; the
surfaces reach services and infra only through this package (`.importlinter`
``surfaces-through-api``), so `cli.py` imports both from here rather than from
`ddflow.services` / `ddflow.infra` directly.
"""

from __future__ import annotations

from ..infra.worktree import repo_root
from ..services import cmdlog

__all__ = ["cmdlog", "repo_root"]
