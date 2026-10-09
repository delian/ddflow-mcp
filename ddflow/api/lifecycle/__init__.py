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

# The operations live one module per area in this package; every name -- public, private
# and the modules the single file imported (`L`, `W`, `O` ...) -- is re-exported, so
# `from ddflow.api import lifecycle as L; L.claim(...)` and `L.L` keep working. A REBINDING is
# not shared: each function reads names from its own module, so to replace a helper or a
# default in a test, patch the module that defines it (`lifecycle.wait.DEFAULT_WAIT_TIMEOUT_S`),
# not this package. (Nothing patched the single module's names when it was split.)

from __future__ import annotations

import time  # noqa: F401
from datetime import (
    UTC,  # noqa: F401
    datetime,  # noqa: F401
)
from pathlib import Path  # noqa: F401
from typing import TYPE_CHECKING, Any  # noqa: F401

from ...core import clock  # noqa: F401
from ...core import globspec as GS  # noqa: F401
from ...core import outcome as O  # noqa: F401
from ...core import progress as PR  # noqa: F401
from ...core.budget import Budget, approx_tokens  # noqa: F401
from ...core.events import parse_changelog  # noqa: F401
from ...core.model import (
    ABANDONED,  # noqa: F401
    DONE,  # noqa: F401
    REVIEW,  # noqa: F401
    State,  # noqa: F401
)
from ...core.plain import plain  # noqa: F401
from ...core.schedule import needs_tree, plan  # noqa: F401
from ...infra import worktree as W  # noqa: F401
from ...services import changes as CH  # noqa: F401
from ...services import flowstate as FL  # noqa: F401
from ...services import gates as G  # noqa: F401
from ...services import leases as L  # noqa: F401
from ...services import searchcore as SC  # noqa: F401
from ...services.gates import measured as GM  # noqa: F401
from ...services.guidance import inject as GI  # noqa: F401
from .._base import _load  # noqa: F401
from ._common import (  # noqa: F401
    _require,
)
from .brief import (  # noqa: F401
    _REFUTED_SHOWN,
    _refuted_line,
    _waiting_on_you,
    brief,
)
from .claim import (  # noqa: F401
    _blocking_items,
    _bring_local_files,
    _early_refusal,
    _in_leased_tree,
    _is_items_tree,
    _join_line,
    _leave_line,
    _new_report_count,
    _prior_lease,
    _recorded_tree,
    _refuse_if_reserved,
    _refuse_looping,
    _tree_let_go,
    _tree_of,
    _undo_claim,
    _worktree_held_by,
    callers_tree,
    claim,
)
from .complete import (  # noqa: F401
    _abandon_refused,
    _complete_umbrellas_above,
    _refuted_extra,
    _session_model,
    _umbrella_children,
    abandon,
    block,
    complete,
    fold,
    remove,
    unblock,
)
from .heartbeat import (  # noqa: F401
    _catch_up_globs,
    _commit_events,
    _waiters,
    heartbeat,
    release,
)
from .merge import (  # noqa: F401
    _branch_to_land,
    _dispose_tree,
    _lands_nothing,
    _open_request,
    _outside_globs,
    _refresh_documents,
    _scope_fields,
    _stands_in,
    _what_to_land,
    dispose_tree,
    merge,
    record_item_removed,
)
from .planning import PURPOSES, alternatives_offer, plan_for  # noqa: F401
from .ready import (  # noqa: F401
    _PREFIX_SHOWN,
    DEFAULT_CHECK_RECOVERY,
    DEFAULT_NEXT_KIND,
    _ready_rows,
    _unknown_phase,
    _wait_hint,
    next_,
)
from .reservations import (  # noqa: F401
    WAITABLE,
    _blocking_leases,
    _claim_blocker,
    _clears_on_release,
    _fmt_since,
    _in_motion,
    _reservation_hold,
    _reserved_for,
    _reserved_msg,
)
from .wait import (  # noqa: F401
    DEFAULT_WAIT_TIMEOUT_S,
    WAIT_MAX_S,
    _end_wait,
    _freed,
    _judge_any,
    _judge_wait,
    _note_cap,
    _wait_globs,
    wait,
)

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
