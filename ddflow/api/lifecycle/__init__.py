"""Claim, work, finish: the coordination path.

The densest policy in the package, and until now all of it lived in `surfaces/cli.py`,
reachable only through `main(argv)`. Four rules here exist because each was violated once
and cost something:

* **A claim that is refused releases its lease.** `L.acquire` runs before the
  worktree-conflict check, so returning early left the item leased by an agent that had
  just been told it could not have it — the refusal CREATED the stuck claim `recover`
  exists to clean up, and the caller had no way to know.
* **Adopt before creating.** An agent whose harness already isolated it (Claude Code and
  Cursor both do) was sent to a second tree on a second branch, stranding the uncommitted
  work in the first and giving one item two branches. ddflow does not need to have MADE
  the tree; it needs to know which tree the item is being worked in.
* **Never remove an ADOPTED tree on merge.** ddflow did not create it, the agent's harness
  did, and it may still be working in it. Deleting it takes uncommitted work with it.
* **`merge` goes through the existence check like every other mutating command.** `.get()`
  finds a REMOVED item, because removal is a flag on an item that still folds — so merge
  once landed the branch of work the operator had explicitly dropped, and reported
  success.
"""

# The operations live one module per area in this package; the public functions and classes
# are re-exported, so
# `from ddflow.api import lifecycle as L; L.claim(...)` keeps working. A REBINDING is
# not shared: each function reads names from its own module, so to replace a helper or a
# default in a test, patch the module that defines it (`lifecycle.wait.DEFAULT_WAIT_TIMEOUT_S`),
# not this package. (Nothing patched the single module's names when it was split.)

from __future__ import annotations

from .brief import brief
from .claim import callers_tree, claim
from .complete import abandon, block, complete, fold, remove, unblock  # noqa: F401
from .heartbeat import heartbeat, release
from .merge import dispose_tree, merge, record_item_removed  # noqa: F401
from .planning import PURPOSES, alternatives_offer, plan_for  # noqa: F401
from .ready import DEFAULT_CHECK_RECOVERY, DEFAULT_NEXT_KIND, next_
from .reservations import WAITABLE
from .wait import DEFAULT_WAIT_TIMEOUT_S, WAIT_MAX_S, wait  # noqa: F401

#: The public operations and constants, as the single module exposed them.
__all__ = [
    "DEFAULT_CHECK_RECOVERY",
    "DEFAULT_NEXT_KIND",
    "DEFAULT_WAIT_TIMEOUT_S",
    "WAITABLE",
    "abandon",
    "block",
    "brief",
    "callers_tree",
    "claim",
    "complete",
    "heartbeat",
    "merge",
    "next_",
    "release",
    "remove",
    "unblock",
    "wait",
]
